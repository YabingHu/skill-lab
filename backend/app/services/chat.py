"""向环境提问：支持自定义 header、故障注入、SSE 流式响应，并提取记录 conversation-id。"""
from __future__ import annotations

import copy
import json
import re
import time
import uuid
from typing import Any

import httpx

from .. import shell
from ..fault_injection import FaultEngine, FaultError
from ..config import Config
from ..db import DB, dumps, loads, now
from .env import EnvService


def get_path(obj: Any, path: str) -> Any:
    """按 a.b.0.c 形式取值，取不到返回 None。"""
    if not path:
        return None
    cur = obj
    for part in path.split("."):
        if isinstance(cur, dict):
            if part in cur:
                cur = cur[part]
            else:
                return None
        elif isinstance(cur, list):
            try:
                cur = cur[int(part)]
            except (ValueError, IndexError):
                return None
        else:
            return None
    return cur


def fill_template(obj: Any, values: dict[str, str]) -> Any:
    """递归替换 body 模板中的 {question} 等占位符。整串等于占位符时保留原类型。"""
    if isinstance(obj, str):
        for k, v in values.items():
            if obj == "{" + k + "}":
                return v
        for k, v in values.items():
            obj = obj.replace("{" + k + "}", str(v))
        return obj
    if isinstance(obj, dict):
        return {k: fill_template(v, values) for k, v in obj.items()}
    if isinstance(obj, list):
        return [fill_template(v, values) for v in obj]
    return obj


def deep_merge(a: Any, b: Any) -> Any:
    if isinstance(a, dict) and isinstance(b, dict):
        out = dict(a)
        for k, v in b.items():
            out[k] = deep_merge(a.get(k), v) if k in a else v
        return out
    return b


def extract_conversation_id(rules: list[str], headers: dict[str, str], body_jsons: list[Any], text: str) -> str | None:
    lower_headers = {k.lower(): v for k, v in headers.items()}
    for rule in rules:
        kind, _, arg = rule.partition(":")
        if kind == "header":
            v = lower_headers.get(arg.lower())
            if v:
                return v
        elif kind == "json":
            for j in body_jsons:
                v = get_path(j, arg)
                if v not in (None, ""):
                    return str(v)
        elif kind == "regex":
            m = re.search(arg, text)
            if m:
                return m.group(1) if m.groups() else m.group(0)
    return None


class ChatService:
    def __init__(self, cfg: Config, db: DB, env: EnvService):
        self.cfg = cfg
        self.db = db
        self.env = env
        self.fault_engine = FaultEngine()

    def endpoints(self) -> list[dict]:
        return self.cfg["chat"]["endpoints"]

    def faults(self) -> list[dict]:
        return self.cfg["chat"]["faults"]

    def _endpoint(self, name: str | None) -> dict:
        eps = self.endpoints()
        for ep in eps:
            if ep["name"] == name:
                return ep
        if not eps:
            raise ValueError("未配置 chat.endpoints")
        return eps[0]

    def build_request(self, req: dict) -> tuple[dict, dict, list[dict]]:
        ep = self._endpoint(req.get("endpoint"))
        conv_id_sent = None
        headers: dict[str, str] = dict(ep.get("headers") or {})
        body_tpl = copy.deepcopy(ep.get("body") or {})
        fault_defs = {f["name"]: f for f in self.faults()}
        faults = [fault_defs[n] for n in req.get("faults") or [] if n in fault_defs]
        for f in faults:
            headers.update({k: str(v) for k, v in (f.get("headers") or {}).items()})
            if f.get("body"):
                body_tpl = deep_merge(body_tpl, f["body"])
        for h in req.get("headers") or []:
            if h.get("key"):
                headers[h["key"]] = str(h.get("value", ""))
        if ep.get("send_conversation_id_header"):
            conv_id_sent = req.get("conversation_id") or str(uuid.uuid4())
            headers[ep["send_conversation_id_header"]] = conv_id_sent
        values = {"question": req.get("question", ""), "conversation_id": conv_id_sent or req.get("conversation_id", "")}
        if req.get("body_override"):
            body = req["body_override"]
            if isinstance(body, str):
                body = json.loads(body)
            body = fill_template(body, values)
        else:
            body = fill_template(body_tpl, values)
        built = {
            "url": req.get("url") or ep["url"], "method": ep.get("method", "POST").upper(),
            "headers": headers, "body": body, "timeout": ep.get("timeout", 600),
            "conversation_id_sent": conv_id_sent,
        }
        return built, ep, faults

    def _run_fault_cmds(self, faults: list[dict], key: str, pod_filter: str, namespace: str | None, logs: list[str]) -> None:
        for f in faults:
            tpl = f.get(key)
            if not tpl:
                continue
            pods = self.env.list_pods(namespace, f.get("pod_filter") or pod_filter)["pods"]
            for p in pods:
                cmd = shell.render(tpl, pod=p["name"])
                r = self.env.exec(p["name"], cmd, namespace)
                logs.append(f"[{f['name']}:{key}] {p['name']}: rc={r.rc} {r.brief(300)}")

    def ask(self, req: dict) -> dict:
        """Run provider-backed faults around existing chat pipeline.

        Only explicitly registered providers can be invoked here. Provider
        recovery executes in finally even when the chat request fails.
        """
        names = set(req.get("faults") or [])
        configured = [f for f in self.faults() if f["name"] in names and f.get("provider")]
        if not configured:
            return self._ask_legacy(req)
        if len(configured) != 1:
            raise ValueError("Only one provider-backed fault per chat is supported")
        fault = configured[0]
        if fault["provider"] not in self.fault_engine.providers:
            raise ValueError("Fault provider is not registered")
        run = self.fault_engine.prepare(fault["provider"], fault.get("parameters") or {})
        try:
            self.fault_engine.inject(run.id)
            if run.status != "INJECTED":
                raise FaultError("Fault verification failed")
            return self._ask_legacy(req)
        finally:
            if run.status in ("INJECTED", "VERIFY_FAILED", "RECOVERY_REQUIRED"):
                self.fault_engine.recover(run.id)

    def _ask_legacy(self, req: dict) -> dict:
        built, ep, faults = self.build_request(req)
        fault_logs: list[str] = []
        self._run_fault_cmds(faults, "pod_cmd", req.get("pod_filter", ""), req.get("namespace"), fault_logs)
        t0 = time.time()
        status_code, resp_headers, text, answer, error = None, {}, "", "", None
        body_jsons: list[Any] = []
        try:
            with httpx.Client(timeout=built["timeout"], trust_env=False) as client:
                kwargs: dict[str, Any] = {"headers": built["headers"]}
                if built["method"] in ("POST", "PUT", "PATCH"):
                    if isinstance(built["body"], (dict, list)):
                        kwargs["json"] = built["body"]
                    else:
                        kwargs["content"] = str(built["body"])
                elif isinstance(built["body"], dict):
                    kwargs["params"] = built["body"]
                with client.stream(built["method"], built["url"], **kwargs) as r:
                    status_code = r.status_code
                    resp_headers = dict(r.headers)
                    if "text/event-stream" in r.headers.get("content-type", ""):
                        lines, parts = [], []
                        for line in r.iter_lines():
                            lines.append(line)
                            if line.startswith("data:"):
                                data = line[5:].strip()
                                if data in ("", "[DONE]"):
                                    continue
                                try:
                                    j = json.loads(data)
                                except json.JSONDecodeError:
                                    parts.append(data)
                                    continue
                                body_jsons.append(j)
                                for path in ep.get("stream_text_paths") or []:
                                    v = get_path(j, path)
                                    if isinstance(v, str):
                                        parts.append(v)
                                        break
                        text = "\n".join(lines)
                        answer = "".join(parts)
                    else:
                        r.read()
                        text = r.text
                        try:
                            j = r.json()
                            body_jsons.append(j)
                            for path in ("answer", "data.answer", "content", "output", "result", "message",
                                         "choices.0.message.content", "data.content", "text"):
                                v = get_path(j, path)
                                if isinstance(v, str):
                                    answer = v
                                    break
                        except Exception:  # noqa: BLE001
                            answer = text
        except httpx.HTTPError as e:
            error = f"{type(e).__name__}: {e}"
        latency = (time.time() - t0) * 1000
        self._run_fault_cmds(faults, "cleanup_cmd", req.get("pod_filter", ""), req.get("namespace"), fault_logs)

        conv_id = built.get("conversation_id_sent") or extract_conversation_id(
            ep.get("conversation_id_rules") or [], resp_headers, body_jsons, text)
        request_record = {k: built[k] for k in ("url", "method", "headers", "body")}
        if fault_logs:
            request_record["fault_logs"] = fault_logs
        chat_id = self.db.execute(
            "INSERT INTO chats(endpoint, question, request_json, faults_json, status_code, response_headers_json, "
            "response_text, answer, conversation_id, latency_ms, error, note, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (ep["name"], req.get("question", ""), dumps(request_record), dumps([f["name"] for f in faults]),
             status_code, dumps(resp_headers), text[:2_000_000], answer[:500_000], conv_id, latency, error,
             req.get("note", ""), now()),
        ).lastrowid
        return self.get(chat_id)

    def get(self, chat_id: int) -> dict | None:
        row = self.db.one("SELECT * FROM chats WHERE id=?", (chat_id,))
        return _decode(row) if row else None

    def list(self, limit: int = 200, q: str = "") -> list[dict]:
        if q:
            like = f"%{q}%"
            rows = self.db.query("SELECT * FROM chats WHERE question LIKE ? OR conversation_id LIKE ? OR note LIKE ? "
                                 "ORDER BY id DESC LIMIT ?", (like, like, like, limit))
        else:
            rows = self.db.query("SELECT * FROM chats ORDER BY id DESC LIMIT ?", (limit,))
        out = []
        for r in rows:
            d = _decode(r)
            d.pop("response_text", None)
            out.append(d)
        return out

    def update(self, chat_id: int, conversation_id: str | None = None, note: str | None = None) -> dict | None:
        if conversation_id is not None:
            self.db.execute("UPDATE chats SET conversation_id=? WHERE id=?", (conversation_id.strip() or None, chat_id))
        if note is not None:
            self.db.execute("UPDATE chats SET note=? WHERE id=?", (note, chat_id))
        return self.get(chat_id)

    def delete(self, chat_id: int) -> None:
        self.db.execute("DELETE FROM chats WHERE id=?", (chat_id,))


def _decode(row: dict) -> dict:
    row["request"] = loads(row.pop("request_json", None), {})
    row["faults"] = loads(row.pop("faults_json", None), [])
    row["response_headers"] = loads(row.pop("response_headers_json", None), {})
    return row
