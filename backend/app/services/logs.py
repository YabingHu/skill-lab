"""拉取环境上的 llm-calls.jsonl 日志，按 conversation-id 建索引。"""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from .. import shell
from ..config import Config
from ..db import DB, loads, now
from ..jobs import Job
from .chat import get_path
from .env import EnvService

CONV_KEYS = {"conversation_id", "conversationid", "conversion_id", "conv_id", "x-conversation-id",
             "x_conversation_id", "chat_id", "session_id", "sessionid", "thread_id", "trace_id"}
TS_KEYS = ["start_time", "started_at", "request_time", "timestamp", "ts", "time", "created_at", "created",
           "@timestamp", "begin_time", "end_time", "finished_at"]


def find_conversation_id(rec: Any, paths: list[str]) -> str | None:
    for p in paths:
        v = get_path(rec, p)
        if v not in (None, ""):
            return str(v)
    # 递归查找常见字段名（限制深度，避免把消息正文里的内容当成 id）
    def walk(o: Any, depth: int) -> str | None:
        if depth > 4:
            return None
        if isinstance(o, dict):
            for k, v in o.items():
                if k.lower() in CONV_KEYS and isinstance(v, (str, int)) and str(v):
                    return str(v)
            for k, v in o.items():
                if k in ("messages", "choices", "content", "tools", "input", "output"):
                    continue
                r = walk(v, depth + 1)
                if r:
                    return r
        return None
    return walk(rec, 0)


def parse_ts(v: Any) -> float | None:
    from datetime import datetime
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)):
        x = float(v)
        if x > 1e17:
            return x / 1e9  # ns
        if x > 1e14:
            return x / 1e6  # us
        if x > 1e11:
            return x / 1e3  # ms
        return x
    if isinstance(v, str):
        s = v.strip()
        if re.fullmatch(r"\d+(\.\d+)?", s):
            return parse_ts(float(s))
        s2 = s.replace("Z", "+00:00")
        for fmt in (None, "%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S"):
            try:
                dt = datetime.fromisoformat(s2) if fmt is None else datetime.strptime(s, fmt)
                return dt.timestamp()
            except ValueError:
                continue
    return None


def find_ts(rec: dict) -> float | None:
    for k in TS_KEYS:
        for container in (rec, rec.get("metadata") if isinstance(rec.get("metadata"), dict) else None,
                          rec.get("request") if isinstance(rec.get("request"), dict) else None):
            if container and k in container:
                t = parse_ts(container[k])
                if t:
                    return t
    resp = rec.get("response")
    if isinstance(resp, dict) and "created" in resp:
        return parse_ts(resp["created"])
    return None


class LogService:
    def __init__(self, cfg: Config, db: DB, env: EnvService):
        self.cfg = cfg
        self.db = db
        self.env = env
        self.local_root = cfg.data_dir / "logs"
        self.local_root.mkdir(parents=True, exist_ok=True)

    @property
    def conf(self) -> dict:
        return self.cfg["logs"]

    def pull(self, job: Job, pod_filter: str, namespace: str | None = None, conversation_id: str = "",
             paths: list[str] | None = None, glob: str = "") -> dict:
        pods_res = self.env.list_pods(namespace, pod_filter)
        if not pods_res["ok"]:
            job.log(f"列 pod 失败：{pods_res['error']}")
            job.status = "failed"
            return {}
        pods = [p["name"] for p in pods_res["pods"]]
        job.log(f"匹配到 {len(pods)} 个 pod：{', '.join(pods) or '无'}")
        paths = paths or self.conf["paths"]
        glob = glob or self.conf["glob"]
        summary = {"pods": {}, "new_records": 0, "conversations": set()}

        def one(pod: str) -> tuple[str, list[str], int, set]:
            logs: list[str] = []
            find_cmd = shell.render("find {paths} -type f -name {glob} 2>/dev/null; true", paths=list(paths), glob=glob)
            r = self.env.exec(pod, find_cmd, namespace)
            files = [f.strip() for f in r.stdout.splitlines() if f.strip()]
            logs.append(f"[{pod}] 找到 {len(files)} 个日志文件")
            new_total, convs = 0, set()
            for f in files:
                cmd = shell.render("grep -F -- {conv} {f} || true", conv=conversation_id, f=f) if conversation_id \
                    else shell.render("cat {f}", f=f)
                rr = self.env.exec(pod, cmd, namespace, timeout=600, binary=True)
                if rr.rc != 0:
                    logs.append(f"[{pod}]   ✗ 读取 {f} 失败：{rr.stderr[:300]}")
                    continue
                data: bytes = rr.stdout  # type: ignore[assignment]
                local = self.local_path(pod, f, conversation_id)
                local.parent.mkdir(parents=True, exist_ok=True)
                local.write_bytes(data)
                n, c = self.index_text(pod, f, data.decode("utf-8", "replace"))
                new_total += n
                convs |= c
                logs.append(f"[{pod}]   {f}：{len(data)} 字节，新增 {n} 条记录，{len(c)} 个会话")
            return pod, logs, new_total, convs

        with ThreadPoolExecutor(max_workers=4) as pool:
            for pod, logs, n, convs in pool.map(one, pods):
                for line in logs:
                    job.log(line)
                summary["pods"][pod] = n
                summary["new_records"] += n
                summary["conversations"] |= convs
        summary["conversations"] = sorted(summary["conversations"])
        job.log(f"完成：新增 {summary['new_records']} 条记录，涉及 {len(summary['conversations'])} 个会话")
        return summary

    # ------------------------------------------------------------ 本地原始文件
    # 全量拉取：logs/full/<pod>/<pod 内路径>；按 ID 拉取：logs/conv/<id>/<pod>/<pod 内路径>
    def local_path(self, pod: str, remote: str, conversation_id: str = "") -> Path:
        if conversation_id:
            return self.local_root / "conv" / _safe(conversation_id) / _safe(pod) / remote.lstrip("/")
        return self.local_root / "full" / _safe(pod) / remote.lstrip("/")

    def list_files(self) -> list[dict]:
        out = []
        for p in sorted(self.local_root.rglob("*")):
            if not p.is_file():
                continue
            parts = p.relative_to(self.local_root).parts
            if parts[0] == "full" and len(parts) >= 3:
                conv, pod, remote = "", parts[1], "/" + "/".join(parts[2:])
            elif parts[0] == "conv" and len(parts) >= 4:
                conv, pod, remote = parts[1], parts[2], "/" + "/".join(parts[3:])
            else:
                continue
            st = p.stat()
            out.append({"rel": p.relative_to(self.local_root).as_posix(), "pod": pod, "remote": remote,
                        "conversation_id": conv, "size": st.st_size, "mtime": st.st_mtime})
        out.sort(key=lambda x: x["mtime"], reverse=True)
        return out

    def file_path(self, rel: str) -> Path:
        p = (self.local_root / rel).resolve()
        if self.local_root.resolve() not in p.parents or not p.is_file():
            raise ValueError(f"文件不存在：{rel}")
        return p

    def download_name(self, rel: str) -> str:
        parts = Path(rel).parts
        prefix = "_".join(parts[1:3]) if parts[0] == "conv" else parts[1]
        return f"{prefix}_{parts[-1]}"

    def build_archive(self, rels: list[str] | None = None) -> Path:
        """把本地拉取的原始日志打成 zip，返回临时文件路径（调用方负责删除）。"""
        files = rels or [f["rel"] for f in self.list_files()]
        if not files:
            raise ValueError("本地还没有拉取过日志")
        fd, tmp = tempfile.mkstemp(prefix="llm-calls-", suffix=".zip")
        os.close(fd)
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zf:
            for rel in files:
                zf.write(self.file_path(rel), arcname=rel)
        return Path(tmp)

    def conversation_lines(self, conversation_id: str) -> list[str]:
        """直接在本地原始日志里按 ID 子串查找行（不依赖日志格式解析），跨文件按原样去重。"""
        conversation_id = conversation_id.strip()
        if not conversation_id:
            return []
        needle = conversation_id.encode("utf-8")
        seen: set[bytes] = set()
        out: list[str] = []
        targets = [f for f in self.list_files() if f["conversation_id"] in ("", _safe(conversation_id))]
        targets.sort(key=lambda f: (f["pod"], f["remote"], f["conversation_id"] != ""))
        for f in targets:
            with open(self.local_root / f["rel"], "rb") as fh:
                for line in fh:
                    if needle in line:
                        line = line.rstrip(b"\r\n")
                        if line and line not in seen:
                            seen.add(line)
                            out.append(line.decode("utf-8", "replace"))
        return out

    def index_text(self, pod: str, file: str, text: str) -> tuple[int, set]:
        paths = self.conf.get("conversation_id_paths") or []
        rows = []
        convs = set()
        for i, line in enumerate(text.splitlines(), 1):
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(rec, dict):
                continue
            conv = find_conversation_id(rec, paths) or ""
            if conv:
                convs.add(conv)
            h = hashlib.sha1(line.encode("utf-8")).hexdigest()
            rows.append((conv, pod, file, i, h, find_ts(rec), line, now()))
        n = self.db.executemany(
            "INSERT OR IGNORE INTO llm_calls(conversation_id, pod, file, line_no, line_hash, ts, record_json, fetched_at) "
            "VALUES (?,?,?,?,?,?,?,?)", rows)
        return n, convs

    def import_upload(self, filename: str, text: str, pod: str = "upload") -> dict:
        n, convs = self.index_text(pod, filename, text)
        return {"new_records": n, "conversations": sorted(convs)}

    def conversations(self, q: str = "", limit: int = 500) -> list[dict]:
        rows = self.db.query(
            "SELECT conversation_id, COUNT(*) AS calls, MIN(ts) AS first_ts, MAX(ts) AS last_ts, "
            "GROUP_CONCAT(DISTINCT pod) AS pods FROM llm_calls WHERE conversation_id != '' "
            + ("AND conversation_id LIKE ? " if q else "")
            + "GROUP BY conversation_id ORDER BY COALESCE(MAX(ts), MAX(fetched_at)) DESC LIMIT ?",
            ((f"%{q}%", limit) if q else (limit,)),
        )
        chats = self.db.query("SELECT id, conversation_id, question, created_at, faults_json FROM chats "
                              "WHERE conversation_id IS NOT NULL AND conversation_id != ''")
        chat_map: dict[str, dict] = {}
        for c in chats:
            chat_map.setdefault(c["conversation_id"], c)
        seen = set()
        out = []
        for r in rows:
            c = chat_map.get(r["conversation_id"])
            r["pods"] = (r["pods"] or "").split(",") if r["pods"] else []
            r["chat_id"] = c["id"] if c else None
            r["question"] = c["question"] if c else None
            r["faults"] = loads(c["faults_json"], []) if c else []
            seen.add(r["conversation_id"])
            out.append(r)
        # 已提问但还没拉到日志的会话也列出来
        for conv, c in chat_map.items():
            if conv in seen or (q and q not in conv):
                continue
            out.append({"conversation_id": conv, "calls": 0, "first_ts": c["created_at"], "last_ts": c["created_at"],
                        "pods": [], "chat_id": c["id"], "question": c["question"],
                        "faults": loads(c["faults_json"], [])})
        out.sort(key=lambda x: x.get("last_ts") or 0, reverse=True)
        return out

    def records(self, conversation_id: str) -> list[dict]:
        rows = self.db.query("SELECT * FROM llm_calls WHERE conversation_id=? ORDER BY ts, pod, file, line_no",
                             (conversation_id,))
        for r in rows:
            r["record"] = loads(r.pop("record_json"), {})
        return rows

    def stats(self) -> dict:
        return self.db.one("SELECT COUNT(*) AS records, COUNT(DISTINCT conversation_id) AS conversations, "
                           "COUNT(DISTINCT pod) AS pods, MAX(fetched_at) AS last_fetch FROM llm_calls") or {}

    def delete_conversation(self, conversation_id: str) -> None:
        self.db.execute("DELETE FROM llm_calls WHERE conversation_id=?", (conversation_id,))
        self.db.execute("DELETE FROM analyses WHERE conversation_id=?", (conversation_id,))


def _safe(s: str) -> str:
    return re.sub(r"[^\w.-]", "_", s)[:64]

