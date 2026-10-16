"""配置加载。

配置文件路径来自环境变量 SKILL_LAB_CONFIG，默认是项目根目录下的 config.yaml。
用户配置会与 DEFAULTS 深度合并，所以 config.yaml 里只写需要覆盖的部分即可。
配置中所有相对路径都相对于配置文件所在目录解析。

LLM 相关配置优先读取环境变量（也可写在项目根目录的 .env 文件里）：
    LLM_BASE_URL / LLM_API_KEY / LLM_MODEL
"""
from __future__ import annotations

import copy
import os
from pathlib import Path
from typing import Any

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]

DEFAULTS: dict[str, Any] = {
    "data_dir": "./data",
    "skills": {
        # skill 工作目录（网页上编辑的就是这里的文件）
        "root": "./workspace/skills",
        # 目录布局：{lang}/{skill} 表示 root/zh/<skill>、root/en/<skill>；
        # 也可以写 {skill}/{lang} 表示 root/<skill>/zh、root/<skill>/en
        "layout": "{lang}/{skill}",
        # 逻辑语言 -> 实际目录名
        "langs": {"zh": "zh", "en": "en"},
        "main_file": "SKILL.md",
        "ignore": [".git", "__pycache__", ".DS_Store"],
    },
    "llm": {
        # 任意 OpenAI 兼容接口（vLLM / Ollama / Xinference / LMDeploy ...）
        "base_url": "http://127.0.0.1:8000/v1",
        "api_key": "EMPTY",
        "model": "qwen2.5-32b-instruct",
        "timeout": 300,
        "temperature": 0.1,
        "max_tokens": 8192,
        "concurrency": 2,
        "extra_headers": {},
        "extra_body": {},
    },
    "env": {
        "namespace": "default",
        # 命令模板。{xxx} 占位符的值会自动做 shell 转义。
        "commands": {
            "list_pods": "kubectl get pods -n {namespace} --no-headers "
                         "-o custom-columns=NAME:.metadata.name,STATUS:.status.phase",
            "exec": "kubectl exec -n {namespace} {pod} -- sh -c {cmd}",
            "cp_to": "kubectl cp {src} {namespace}/{pod}:{dst}",
            # 环境信息脚本（可选），输出原样展示在页面上
            "env_info": "",
        },
        "command_timeout": 120,
    },
    "deploy": {
        "default_pod_filter": "",
        # 打包命令（可选）。为空时使用内置 tar.gz 打包。
        # 可用占位符：{skill} {version} {staging} {out} {langs}
        "package_cmd": "",
        "remote_tmp": "/tmp",
        # skill 在 pod 内的根目录，按 layout 解包到这里
        "remote_dir": "/opt/agent/skills",
        # 在 pod 内执行的安装命令，可用占位符：{pkg} {remote_dir} {skill} {version}
        "install_cmd": "mkdir -p {remote_dir} && tar -xzf {pkg} -C {remote_dir} && rm -f {pkg}",
        # 安装后命令（可选），例如 reload 接口
        "post_cmd": "",
        # 并发发布的 pod 数
        "concurrency": 4,
        # 从环境拉取 skill 时在 pod 内执行的命令，stdout 需要是 tar/tar.gz。
        # 可用占位符：{remote_dir} {paths}（要拉取的相对路径列表，拉取全部时为 "."）
        "fetch_cmd": "cd {remote_dir} && tar -czf - $(ls -d {paths} 2>/dev/null)",
    },
    "chat": {
        "endpoints": [
            {
                "name": "default",
                "url": "http://127.0.0.1:8080/api/chat",
                "method": "POST",
                "headers": {"Content-Type": "application/json"},
                # body 模板，字符串中的 {question} / {conversation_id} 会被替换
                "body": {"query": "{question}", "stream": False},
                "timeout": 600,
                # 从响应中提取 conversation-id 的规则，按顺序尝试
                #   header:<name>    响应头
                #   json:<a.b.c>     响应 JSON（或 SSE 事件 JSON）中的字段
                #   regex:<pattern>  对响应全文正则匹配，取第一个分组
                "conversation_id_rules": [
                    "header:x-conversation-id",
                    "json:conversation_id",
                    "json:data.conversation_id",
                    r'regex:"?conversation[_-]?id"?\s*[:=]\s*"?([\w\-]+)',
                ],
                # 如果环境支持由调用方指定 conversation-id，在这里填 header 名，
                # 每次提问会生成 uuid 放入该 header。
                "send_conversation_id_header": "",
                # SSE 流式时拼接回答文本的字段（JSON 路径），按顺序尝试
                "stream_text_paths": ["choices.0.delta.content", "delta", "content", "answer", "text", "data.content"],
            }
        ],
        # 故障注入预设。每个故障可以：加请求头 / 合并请求体 / 提问前在 pod 内执行命令 / 提问后清理
        "faults": [
            {
                "name": "tool_timeout",
                "label": "工具调用超时",
                "description": "通过请求头让环境模拟工具超时",
                "headers": {"x-fault-inject": "tool_timeout"},
            },
            {
                "name": "tool_error",
                "label": "工具返回错误",
                "description": "通过请求头让环境模拟工具返回错误",
                "headers": {"x-fault-inject": "tool_error"},
            },
        ],
    },
    "logs": {
        "pod_filter": "",
        # 在 pod 内查找日志的目录与文件名
        "paths": ["/var/log/agent", "/opt/agent/logs"],
        "glob": "llm-calls*.jsonl",
        # 从每行日志里取 conversation-id 的字段（JSON 路径），按顺序尝试；
        # 都取不到时会递归查找常见字段名
        "conversation_id_paths": ["conversation_id", "conversationId", "metadata.conversation_id",
                                  "request.metadata.conversation_id", "headers.x-conversation-id"],
    },
    "analysis": {
        "default_lang": "zh",
        # 字段映射覆盖（JSON 路径），留空则自动识别 OpenAI / Anthropic 等常见格式
        "fields": {
            "start": "",
            "end": "",
            "duration_ms": "",
            "model": "",
            "messages": "",
            "response": "",
            "usage": "",
        },
        # 工具分类规则（按工具名正则匹配，按顺序生效）
        "tool_classes": [
            {"type": "CLI", "pattern": "bash|shell|exec|terminal|command|cmd|run_|kubectl|ssh"},
            {"type": "API", "pattern": "http|api|request|curl|fetch|rest|rpc"},
            {"type": "FILE", "pattern": "read|write|edit|file|glob|grep|ls"},
        ],
        # 启发式对齐的最低得分
        "match_threshold": 0.18,
    },
}


ENV_OVERRIDES = {
    "LLM_BASE_URL": ("llm", "base_url"),
    "LLM_API_KEY": ("llm", "api_key"),
    "LLM_MODEL": ("llm", "model"),
}


def load_dotenv(path: Path) -> None:
    """极简 .env 解析：KEY=VALUE，忽略注释；不覆盖已存在的环境变量。"""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[7:]
        key, _, val = line.partition("=")
        key, val = key.strip(), val.strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
            val = val[1:-1]
        os.environ.setdefault(key, val)


def _deep_merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


class Config:
    def __init__(self, path: Path | None = None):
        env_path = os.environ.get("SKILL_LAB_CONFIG")
        self.path = Path(path or env_path or PROJECT_ROOT / "config.yaml").resolve()
        self.reload()

    def reload(self) -> None:
        user: dict = {}
        if self.path.exists():
            user = yaml.safe_load(self.path.read_text(encoding="utf-8")) or {}
        self.raw = user
        self.data = _deep_merge(DEFAULTS, user)
        base = self.path.parent
        load_dotenv(base / ".env")
        if base != PROJECT_ROOT:
            load_dotenv(PROJECT_ROOT / ".env")
        for env_key, (sec, key) in ENV_OVERRIDES.items():
            if os.environ.get(env_key):
                self.data[sec][key] = os.environ[env_key]
        # 列表类配置整体覆盖，这里给每个 endpoint 补齐默认字段（如 conversation-id 提取规则）
        ep_default = DEFAULTS["chat"]["endpoints"][0]
        self.data["chat"]["endpoints"] = [
            {**{k: v for k, v in ep_default.items() if k not in ("name", "url")}, **ep}
            for ep in self.data["chat"].get("endpoints") or []
        ]
        self.data_dir = (base / self.data["data_dir"]).resolve()
        self.skills_root = (base / self.data["skills"]["root"]).resolve()
        self.base_dir = base
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.skills_root.mkdir(parents=True, exist_ok=True)

    def __getitem__(self, key: str) -> Any:
        return self.data[key]

    def get(self, dotted: str, default: Any = None) -> Any:
        cur: Any = self.data
        for part in dotted.split("."):
            if not isinstance(cur, dict) or part not in cur:
                return default
            cur = cur[part]
        return cur

    def public(self) -> dict:
        """给前端展示的配置（隐藏密钥）。"""
        d = copy.deepcopy(self.data)
        if d.get("llm", {}).get("api_key"):
            d["llm"]["api_key"] = "***"
        d["_config_path"] = str(self.path)
        d["_skills_root"] = str(self.skills_root)
        d["_data_dir"] = str(self.data_dir)
        return d


_config: Config | None = None


def get_config() -> Config:
    global _config
    if _config is None:
        _config = Config()
    return _config


def set_config(cfg: Config) -> None:
    global _config
    _config = cfg
