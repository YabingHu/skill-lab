"""一键发布：把某个版本的 skill 打包，复制到名称包含指定字符串的 pod 并安装。"""
from __future__ import annotations

import shutil
import tarfile
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .. import shell
from ..config import Config
from ..db import DB, dumps, loads, now
from ..jobs import Job
from .env import EnvService
from .skills import SkillStore


class DeployService:
    def __init__(self, cfg: Config, db: DB, store: SkillStore, env: EnvService):
        self.cfg = cfg
        self.db = db
        self.store = store
        self.env = env

    @property
    def conf(self) -> dict:
        return self.cfg["deploy"]

    def build_package(self, job: Job, skill: str, seq: int, langs: list[str], workdir: Path) -> Path:
        snap = self.store.snapshot_dir(skill, seq)
        if not snap.is_dir():
            raise RuntimeError(f"版本 v{seq} 不存在")
        # staging 目录按工作区 layout 组织，便于直接解包到 remote_dir
        staging = workdir / "staging"
        for lang in langs:
            src = snap / lang
            dst = self.store.lang_dir(skill, lang, root=staging)
            if src.is_dir():
                shutil.copytree(src, dst, dirs_exist_ok=True)
        out = workdir / f"{skill}-v{seq}.tar.gz"
        tpl = self.conf.get("package_cmd") or ""
        if tpl:
            cmd = shell.render(tpl, skill=skill, version=seq, staging=str(staging), out=str(out),
                               langs=",".join(langs))
            job.log(f"$ {cmd}")
            r = shell.run(cmd, timeout=600, cwd=str(self.cfg.base_dir))
            if r.stdout or r.stderr:
                job.log(r.brief(5000))
            if not r.ok:
                raise RuntimeError(f"打包脚本失败（rc={r.rc}）")
            if not out.exists():
                raise RuntimeError(f"打包脚本没有生成 {out}")
        else:
            with tarfile.open(out, "w:gz") as tar:
                for child in sorted(staging.iterdir()):
                    tar.add(child, arcname=child.name)
        job.log(f"打包完成：{out.name}（{out.stat().st_size} 字节）")
        return out

    def deploy(self, job: Job, skill: str, seq: int, langs: list[str], pods: list[str],
               namespace: str | None, pod_filter: str) -> dict:
        ns = self.env.ns(namespace)
        dep_id = self.db.execute(
            "INSERT INTO deployments(skill, version_seq, langs, namespace, pod_filter, pods_json, status, job_id, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (skill, seq, ",".join(langs), ns, pod_filter, dumps([{"pod": p} for p in pods]), "running", job.id, now()),
        ).lastrowid
        job.log(f"发布 {skill} v{seq}（{'/'.join(langs)}）到 {len(pods)} 个 pod，namespace={ns}")
        results: list[dict] = []
        with tempfile.TemporaryDirectory(prefix="skill-lab-") as tmp:
            try:
                pkg = self.build_package(job, skill, seq, langs, Path(tmp))
            except Exception as e:  # noqa: BLE001
                job.log(f"✗ 打包失败：{e}")
                self.db.execute("UPDATE deployments SET status='failed' WHERE id=?", (dep_id,))
                job.status = "failed"
                return {"deployment_id": dep_id, "results": []}
            remote_pkg = f"{self.conf['remote_tmp'].rstrip('/')}/{pkg.name}"
            values = dict(pkg=remote_pkg, remote_dir=self.conf["remote_dir"], skill=skill, version=seq)

            def one(pod: str) -> dict:
                logs = []
                r = self.env.copy_to(pod, str(pkg), remote_pkg, ns)
                logs.append(f"[{pod}] $ {r.cmd}")
                if not r.ok:
                    return {"pod": pod, "ok": False, "message": f"复制失败：{r.brief()}", "logs": logs}
                for key in ("install_cmd", "post_cmd"):
                    tpl = self.conf.get(key) or ""
                    if not tpl:
                        continue
                    inner = shell.render(tpl, **values)
                    r = self.env.exec(pod, inner, ns, timeout=300)
                    logs.append(f"[{pod}] {key}: {inner}")
                    if r.stdout.strip() or r.stderr.strip():
                        logs.append(f"[{pod}]   {r.brief(2000)}")
                    if not r.ok:
                        return {"pod": pod, "ok": False, "message": f"{key} 失败（rc={r.rc}）：{r.brief(500)}",
                                "logs": logs}
                return {"pod": pod, "ok": True, "message": "成功", "logs": logs}

            workers = max(1, int(self.conf.get("concurrency", 4)))
            with ThreadPoolExecutor(max_workers=workers) as pool:
                for res in pool.map(one, pods):
                    for line in res.pop("logs"):
                        job.log(line)
                    job.log(f"{'✓' if res['ok'] else '✗'} {res['pod']}：{res['message']}")
                    results.append(res)
        ok = sum(1 for r in results if r["ok"])
        status = "success" if ok == len(results) else ("partial" if ok else "failed")
        self.db.execute("UPDATE deployments SET status=?, pods_json=? WHERE id=?", (status, dumps(results), dep_id))
        job.log(f"完成：成功 {ok}/{len(results)}")
        if status == "failed":
            job.status = "failed"
        return {"deployment_id": dep_id, "status": status, "results": results}

    def history(self, skill: str | None = None, limit: int = 100) -> list[dict]:
        if skill:
            rows = self.db.query("SELECT * FROM deployments WHERE skill=? ORDER BY id DESC LIMIT ?", (skill, limit))
        else:
            rows = self.db.query("SELECT * FROM deployments ORDER BY id DESC LIMIT ?", (limit,))
        for r in rows:
            r["pods"] = loads(r.pop("pods_json"), [])
        return rows

    def version_deployed_before(self, skill: str, ts: float, pods: list[str] | None = None) -> int | None:
        """查找某时刻之前最后一次成功发布到（任一）相关 pod 的版本。"""
        rows = self.db.query("SELECT version_seq, pods_json, created_at FROM deployments "
                             "WHERE skill=? AND created_at<=? AND status IN ('success','partial') "
                             "ORDER BY created_at DESC", (skill, ts))
        for r in rows:
            if not pods:
                return r["version_seq"]
            deployed = {p["pod"] for p in loads(r["pods_json"], []) if p.get("ok")}
            if deployed & set(pods):
                return r["version_seq"]
        return None
