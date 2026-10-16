"""命令模板渲染与执行。"""
from __future__ import annotations

import shlex
import string
import subprocess
from dataclasses import dataclass


class _SafeDict(dict):
    def __missing__(self, key: str) -> str:  # 未知占位符原样保留
        return "{" + key + "}"


def render(template: str, quote: bool = True, **values) -> str:
    """渲染命令模板。默认对所有值做 shell 转义，避免空格 / 引号问题。

    以 `_raw` 结尾的变量名不转义，例如 {paths_raw}。
    """
    vals = {}
    for k, v in values.items():
        if v is None:
            v = ""
        if isinstance(v, (list, tuple)):
            v = " ".join(shlex.quote(str(x)) for x in v) if quote else " ".join(map(str, v))
            vals[k] = v
            continue
        v = str(v)
        vals[k] = shlex.quote(v) if quote and not k.endswith("_raw") else v
    return string.Formatter().vformat(template, (), _SafeDict(vals))


def render_text(template: str, **values) -> str:
    """非 shell 场景下的模板替换（不转义）。"""
    return render(template, quote=False, **values)


@dataclass
class Result:
    cmd: str
    rc: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.rc == 0

    def brief(self, limit: int = 2000) -> str:
        out = (self.stdout or "") + (("\n" + self.stderr) if self.stderr else "")
        out = out.strip()
        return out if len(out) <= limit else out[:limit] + f"\n...（已截断，共 {len(out)} 字符）"


def run(cmd: str, timeout: float = 120, cwd: str | None = None, binary: bool = False) -> Result:
    try:
        p = subprocess.run(
            ["bash", "-c", cmd], capture_output=True, timeout=timeout, cwd=cwd,
        )
        if binary:
            return Result(cmd, p.returncode, p.stdout, p.stderr.decode("utf-8", "replace"))  # type: ignore[arg-type]
        return Result(cmd, p.returncode, p.stdout.decode("utf-8", "replace"),
                      p.stderr.decode("utf-8", "replace"))
    except subprocess.TimeoutExpired:
        return Result(cmd, 124, b"" if binary else "", f"命令超时（{timeout}s）")  # type: ignore[arg-type]
