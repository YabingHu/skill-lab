"""从环境 pod 拉取当前生效的 skill 内容，与工作区对比后选择性导入为新版本。

流程：
1. 在 pod 内执行 fetch_cmd（默认 tar 打包 remote_dir 下的 skill 目录），stdout 读回本地并安全解包到快照目录；
2. 按 layout 识别快照中的 skill，与工作区逐文件对比（新增 / 有差异 / 一致）；
3. 用户勾选要导入的 skill：先把工作区当前内容保存为版本，再用环境内容替换并生成一个「环境拉取」版本。
"""
from __future__ import annotations

import io
import shutil
import tarfile
import uuid
from pathlib import Path, PurePosixPath

from .. import shell
from ..config import Config
from ..db import dumps, loads, now
from ..jobs import Job
from .env import EnvService
from .skills import SkillStore, _is_text, _line_stats

KEEP_SNAPSHOTS = 10


def safe_extract(data: bytes, dest: Path) -> int:
    """只解出普通文件和目录，拒绝绝对路径、.. 和链接，返回文件数。"""
    n = 0
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:*") as tar:
        for m in tar.getmembers():
            name = PurePosixPath(m.name)
            if name.is_absolute() or ".." in name.parts:
                continue
            target = dest.joinpath(*[p for p in name.parts if p not in ("", ".")])
            if m.isdir():
                target.mkdir(parents=True, exist_ok=True)
            elif m.isfile():
                target.parent.mkdir(parents=True, exist_ok=True)
                f = tar.extractfile(m)
                if f is not None:
                    target.write_bytes(f.read())
                    n += 1
    return n


class EnvPullService:
    def __init__(self, cfg: Config, store: SkillStore, env: EnvService):
        self.cfg = cfg
        self.store = store
        self.env = env
        self.root = cfg.data_dir / "env_pull"
        self.root.mkdir(parents=True, exist_ok=True)

    def _cleanup(self) -> None:
        dirs = sorted((d for d in self.root.iterdir() if d.is_dir()), key=lambda d: d.stat().st_mtime, reverse=True)
        for d in dirs[KEEP_SNAPSHOTS:]:
            shutil.rmtree(d, ignore_errors=True)

    def pull(self, job: Job, pod: str, namespace: str | None, skills: list[str] | None, remote_dir: str = "") -> dict:
        remote_dir = remote_dir or self.cfg["deploy"]["remote_dir"]
        if skills:
            paths = [self.cfg["skills"]["layout"].format(lang=ld, skill=s)
                     for s in skills for ld in self.cfg["skills"]["langs"].values()]
        else:
            paths = ["."]
        tpl = self.cfg["deploy"]["fetch_cmd"]
        cmd = shell.render(tpl, remote_dir=remote_dir, paths=paths)
        job.log(f"[{pod}] $ {cmd}")
        r = self.env.exec(pod, cmd, namespace, timeout=600, binary=True)
        data: bytes = r.stdout  # type: ignore[assignment]
        if r.rc != 0 or not data:
            job.log(f"✗ 拉取失败（rc={r.rc}）：{(r.stderr or '').strip()[:1000] or '没有输出'}")
            if skills:
                job.log(f"  请确认 pod 内 {remote_dir} 下存在：{', '.join(paths)}")
            job.status = "failed"
            return {}
        pull_id = uuid.uuid4().hex[:12]
        snap = self.root / pull_id
        snap.mkdir(parents=True)
        try:
            n = safe_extract(data, snap)
        except tarfile.TarError as e:
            job.log(f"✗ 解包失败：{e}（fetch_cmd 的输出需要是 tar / tar.gz）")
            job.status = "failed"
            shutil.rmtree(snap, ignore_errors=True)
            return {}
        job.log(f"读回 {len(data)} 字节，解出 {n} 个文件")
        meta = {"pod": pod, "namespace": self.env.ns(namespace), "remote_dir": remote_dir, "created_at": now()}
        (snap / ".meta.json").write_text(dumps(meta), encoding="utf-8")
        self._cleanup()
        result = {"pull_id": pull_id, **meta, "skills": self.compare(pull_id, skills)}
        summary = {}
        for s in result["skills"]:
            summary[s["status"]] = summary.get(s["status"], 0) + 1
        job.log("对比完成：" + "，".join(f"{ {'new': '新增', 'changed': '有差异', 'same': '一致'}[k]} {v} 个"
                                      for k, v in summary.items()) if summary else "没有识别到 skill，请检查 remote_dir 与 layout")
        return result

    def _snap(self, pull_id: str) -> Path:
        snap = (self.root / pull_id).resolve()
        if self.root.resolve() not in snap.parents or not snap.is_dir():
            raise ValueError("拉取结果已过期，请重新拉取")
        return snap

    def compare(self, pull_id: str, only: list[str] | None = None) -> list[dict]:
        snap = self._snap(pull_id)
        local_names = set(self.store.skill_names())
        out = []
        for skill in self.store.skill_names(snap):
            if only and skill not in only:
                continue
            files = []
            env_langs = []
            for lang in self.store.langs:
                env_dir = self.store.lang_dir(skill, lang, root=snap)
                if not env_dir.is_dir():
                    continue  # 环境上没有这个语言目录，导入时保留本地
                env_langs.append(lang)
                envf = self.store.scan_dir(env_dir)
                localf = self.store.scan_dir(self.store.lang_dir(skill, lang)) if skill in local_names else {}
                for path in sorted(set(envf) | set(localf)):
                    a, b = localf.get(path), envf.get(path)
                    if a == b:
                        continue
                    status = "added" if a is None else "deleted" if b is None else "modified"
                    ta = "" if a is None else a.decode("utf-8", "replace") if _is_text(a) else "<二进制文件>"
                    tb = "" if b is None else b.decode("utf-8", "replace") if _is_text(b) else "<二进制文件>"
                    add, rem = _line_stats(ta, tb)
                    files.append({"lang": lang, "path": path, "status": status, "old": ta, "new": tb,
                                  "additions": add, "deletions": rem})
            status = "new" if skill not in local_names else ("changed" if files else "same")
            out.append({"name": skill, "status": status, "langs": env_langs, "files": files})
        return out

    def apply(self, pull_id: str, skills: list[str], message: str = "") -> dict:
        snap = self._snap(pull_id)
        meta = loads((snap / ".meta.json").read_text(encoding="utf-8"), {})
        available = set(self.store.skill_names(snap))
        local_names = set(self.store.skill_names())
        done = []
        for skill in skills:
            if skill not in available:
                raise ValueError(f"拉取结果中没有 {skill}")
            if skill in local_names:
                # 先把本地当前内容留成版本，保证可以回退
                self.store.ensure_initial_version(skill)
                self.store.create_version(skill, "从环境拉取前自动保存", source="manual")
            for lang in self.store.langs:
                env_dir = self.store.lang_dir(skill, lang, root=snap)
                if env_dir.is_dir():
                    self.store.replace_lang_dir(skill, lang, self.store.scan_dir(env_dir))
            v = self.store.create_version(
                skill, message or f"从环境拉取（{meta.get('pod', '')}:{meta.get('remote_dir', '')}）", source="env-pull")
            done.append({"skill": skill, "version": v["seq"] if v else None})
        return {"ok": True, "imported": done}
