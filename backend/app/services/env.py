"""环境操作：列 pod、在 pod 内执行命令、向 pod 复制文件。全部基于可配置的命令模板。"""
from __future__ import annotations

from .. import shell
from ..config import Config


class EnvService:
    def __init__(self, cfg: Config):
        self.cfg = cfg

    @property
    def cmds(self) -> dict:
        return self.cfg["env"]["commands"]

    @property
    def timeout(self) -> float:
        return float(self.cfg["env"].get("command_timeout", 120))

    def ns(self, namespace: str | None) -> str:
        return namespace or self.cfg["env"]["namespace"]

    def list_pods(self, namespace: str | None = None, filter_: str = "") -> dict:
        cmd = shell.render(self.cmds["list_pods"], namespace=self.ns(namespace))
        r = shell.run(cmd, timeout=self.timeout, cwd=str(self.cfg.base_dir))
        if not r.ok:
            return {"ok": False, "cmd": cmd, "error": r.brief(), "pods": []}
        pods = []
        for line in r.stdout.splitlines():
            parts = line.split()
            if not parts:
                continue
            name = parts[0].removeprefix("pod/")
            status = parts[1] if len(parts) > 1 else ""
            pods.append({"name": name, "status": status, "raw": line.strip()})
        pods = filter_pods(pods, filter_)
        return {"ok": True, "cmd": cmd, "pods": pods}

    def exec(self, pod: str, cmd: str, namespace: str | None = None, timeout: float | None = None,
             binary: bool = False) -> shell.Result:
        full = shell.render(self.cmds["exec"], namespace=self.ns(namespace), pod=pod, cmd=cmd)
        return shell.run(full, timeout=timeout or self.timeout, cwd=str(self.cfg.base_dir), binary=binary)

    def copy_to(self, pod: str, src: str, dst: str, namespace: str | None = None) -> shell.Result:
        full = shell.render(self.cmds["cp_to"], namespace=self.ns(namespace), pod=pod, src=src, dst=dst)
        return shell.run(full, timeout=self.timeout * 3, cwd=str(self.cfg.base_dir))

    def env_info(self, namespace: str | None = None) -> dict:
        tpl = self.cmds.get("env_info") or ""
        if not tpl:
            return {"ok": False, "error": "未配置 env.commands.env_info", "output": ""}
        cmd = shell.render(tpl, namespace=self.ns(namespace))
        r = shell.run(cmd, timeout=self.timeout, cwd=str(self.cfg.base_dir))
        return {"ok": r.ok, "cmd": cmd, "output": r.brief(200000)}


def filter_pods(pods: list[dict], filter_: str) -> list[dict]:
    """filter_ 支持多个关键字：逗号分隔为「或」，空格分隔为「且」，前缀 ! 表示排除。"""
    filter_ = (filter_ or "").strip()
    if not filter_:
        return pods
    groups = [g.split() for g in filter_.split(",") if g.strip()]

    def match(name: str) -> bool:
        for terms in groups:
            ok = True
            for t in terms:
                if t.startswith("!"):
                    if t[1:] and t[1:] in name:
                        ok = False
                        break
                elif t not in name:
                    ok = False
                    break
            if ok:
                return True
        return False

    return [p for p in pods if match(p["name"])]
