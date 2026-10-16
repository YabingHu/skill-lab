"""把 llm-calls.jsonl 的原始记录归一化成「LLM 调用 + 工具调用」的执行轨迹。

自动兼容的常见格式：
- OpenAI Chat Completions：request.messages / response.choices[0].message.tool_calls / usage.prompt_tokens
- Anthropic Messages：content blocks（tool_use / tool_result）/ usage.input_tokens
- OpenAI Responses：response.output[type=function_call]
- 扁平格式：messages / output / prompt_tokens 等直接在顶层
字段位置不同的话，可以在 config.analysis.fields 里用 JSON 路径覆盖。
"""
from __future__ import annotations

import json
import re
from typing import Any

from .chat import get_path
from .logs import find_ts, parse_ts

DURATION_KEYS_MS = ["latency_ms", "duration_ms", "elapsed_ms", "cost_ms", "time_cost_ms", "response_time_ms"]
DURATION_KEYS_S = ["latency", "duration", "elapsed", "cost", "time_cost", "response_time"]
END_KEYS = ["end_time", "finished_at", "end", "response_time_at", "completed_at"]


def _as_text(content: Any) -> str:
    """把 OpenAI/Anthropic 的 content（字符串或 block 列表）转成文本。"""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for b in content:
            if isinstance(b, str):
                parts.append(b)
            elif isinstance(b, dict):
                t = b.get("type")
                if t in ("text", "input_text", "output_text") or "text" in b:
                    parts.append(str(b.get("text", "")))
                elif t == "tool_result":
                    parts.append(_as_text(b.get("content")))
                elif t == "tool_use":
                    parts.append(f"[tool_use {b.get('name')}] {json.dumps(b.get('input'), ensure_ascii=False)}")
                elif t in ("image", "image_url"):
                    parts.append("[image]")
                else:
                    parts.append(json.dumps(b, ensure_ascii=False))
        return "\n".join(p for p in parts if p)
    if isinstance(content, dict):
        return _as_text([content])
    return str(content)


def _parse_args(args: Any) -> Any:
    if isinstance(args, str):
        s = args.strip()
        if s.startswith("{") or s.startswith("["):
            try:
                return json.loads(s)
            except json.JSONDecodeError:
                return args
    return args


def _first(*vals: Any) -> Any:
    for v in vals:
        if v not in (None, "", [], {}):
            return v
    return None


def _num(v: Any) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


class TraceBuilder:
    def __init__(self, analysis_conf: dict):
        self.fields = analysis_conf.get("fields") or {}
        self.classes = [(c["type"], re.compile(c["pattern"], re.I)) for c in analysis_conf.get("tool_classes") or []]

    # ------------------------------------------------------------- 字段提取
    def _f(self, rec: dict, key: str) -> Any:
        p = self.fields.get(key)
        return get_path(rec, p) if p else None

    def _request(self, rec: dict) -> dict:
        for k in ("request", "req", "input", "params", "payload"):
            v = rec.get(k)
            if isinstance(v, dict) and ("messages" in v or "input" in v or "model" in v or "system" in v):
                return v
        return rec

    def _response(self, rec: dict) -> Any:
        v = self._f(rec, "response")
        if v is not None:
            return v
        for k in ("response", "resp", "output", "result", "completion"):
            if k in rec:
                return rec[k]
        return {}

    def _messages(self, rec: dict, req: dict) -> list[dict]:
        msgs = self._f(rec, "messages")
        if msgs is None:
            msgs = req.get("messages")
            if msgs is None and isinstance(req.get("input"), list):
                msgs = req["input"]
            if msgs is None:
                msgs = rec.get("messages") or rec.get("prompt")
        if isinstance(msgs, str):
            msgs = [{"role": "user", "content": msgs}]
        msgs = [m for m in (msgs or []) if isinstance(m, dict)]
        system = req.get("system")
        if system and not any(m.get("role") == "system" for m in msgs):
            msgs = [{"role": "system", "content": system}] + msgs
        return msgs

    def _usage(self, rec: dict, resp: Any) -> dict:
        u = self._f(rec, "usage")
        if u is None:
            u = _first(resp.get("usage") if isinstance(resp, dict) else None, rec.get("usage"), rec.get("token_usage"),
                       rec.get("tokens"), (rec.get("metrics") or {}).get("usage") if isinstance(rec.get("metrics"), dict) else None)
        u = u if isinstance(u, dict) else {}
        prompt = _num(_first(u.get("prompt_tokens"), u.get("input_tokens"), rec.get("prompt_tokens"), rec.get("input_tokens"))) or 0
        completion = _num(_first(u.get("completion_tokens"), u.get("output_tokens"), rec.get("completion_tokens"),
                                 rec.get("output_tokens"))) or 0
        cached = _num(_first(get_path(u, "prompt_tokens_details.cached_tokens"), u.get("cache_read_input_tokens"),
                             u.get("cached_tokens"), get_path(u, "input_tokens_details.cached_tokens"))) or 0
        # Anthropic 的 input_tokens 不含缓存部分
        if "input_tokens" in u and "cache_read_input_tokens" in u:
            prompt += cached + (_num(u.get("cache_creation_input_tokens")) or 0)
        reasoning = _num(_first(get_path(u, "completion_tokens_details.reasoning_tokens"),
                                get_path(u, "output_tokens_details.reasoning_tokens"))) or 0
        total = _num(u.get("total_tokens")) or (prompt + completion)
        return {"prompt": int(prompt), "completion": int(completion), "cached": int(cached),
                "reasoning": int(reasoning), "total": int(total)}

    def _timing(self, rec: dict, ts: float | None) -> tuple[float | None, float | None, float | None]:
        start = parse_ts(self._f(rec, "start")) or None
        end = parse_ts(self._f(rec, "end")) or None
        dur = _num(self._f(rec, "duration_ms"))
        containers = [rec] + [rec[k] for k in ("metadata", "metrics", "timing", "stats") if isinstance(rec.get(k), dict)]
        if dur is None:
            for c in containers:
                for k in DURATION_KEYS_MS:
                    if c.get(k) is not None and _num(c.get(k)) is not None:
                        dur = _num(c[k])
                        break
                if dur is not None:
                    break
        if dur is None:
            for c in containers:
                for k in DURATION_KEYS_S:
                    v = _num(c.get(k))
                    if v is not None:
                        dur = v * 1000 if v < 3600 else v  # 小于 3600 视为秒
                        break
                if dur is not None:
                    break
        if end is None:
            for c in containers:
                for k in END_KEYS:
                    if k in c:
                        end = parse_ts(c[k])
                        if end:
                            break
                if end:
                    break
        if start is None:
            start = ts
            if start is not None and end is not None and abs(start - end) < 1e-6 and dur:
                start = end - dur / 1000
        if start is not None and end is not None and dur is None:
            dur = max(0.0, (end - start) * 1000)
        if start is not None and end is None and dur is not None:
            end = start + dur / 1000
        if start is None and end is not None and dur is not None:
            start = end - dur / 1000
        return start, end, dur

    def _output(self, resp: Any) -> tuple[str, str, list[dict], str]:
        """返回 (文本, 思考过程, 工具调用列表, finish_reason)"""
        text, reasoning, calls, finish = "", "", [], ""
        if isinstance(resp, str):
            return resp, "", [], ""
        if not isinstance(resp, dict):
            return _as_text(resp), "", [], ""
        msg = None
        if isinstance(resp.get("choices"), list) and resp["choices"]:
            ch = resp["choices"][0]
            msg = ch.get("message") or ch.get("delta") or {}
            finish = ch.get("finish_reason") or ""
        elif "message" in resp and isinstance(resp["message"], dict):
            msg = resp["message"]
        if msg is not None:
            text = _as_text(msg.get("content"))
            reasoning = msg.get("reasoning_content") or msg.get("reasoning") or ""
            for tc in msg.get("tool_calls") or []:
                fn = tc.get("function") or {}
                calls.append({"id": tc.get("id") or "", "name": fn.get("name") or tc.get("name") or "",
                              "arguments": _parse_args(fn.get("arguments", tc.get("arguments")))})
            if msg.get("function_call"):
                fc = msg["function_call"]
                calls.append({"id": "", "name": fc.get("name", ""), "arguments": _parse_args(fc.get("arguments"))})
            if isinstance(msg.get("content"), list):
                for b in msg["content"]:
                    if isinstance(b, dict) and b.get("type") == "tool_use":
                        calls.append({"id": b.get("id", ""), "name": b.get("name", ""), "arguments": b.get("input")})
        elif isinstance(resp.get("content"), list):  # Anthropic
            finish = resp.get("stop_reason") or ""
            texts = []
            for b in resp["content"]:
                if not isinstance(b, dict):
                    continue
                if b.get("type") == "text":
                    texts.append(b.get("text", ""))
                elif b.get("type") == "thinking":
                    reasoning += b.get("thinking", "")
                elif b.get("type") == "tool_use":
                    calls.append({"id": b.get("id", ""), "name": b.get("name", ""), "arguments": b.get("input")})
            text = "\n".join(texts)
        elif isinstance(resp.get("output"), list):  # OpenAI Responses
            for item in resp["output"]:
                if not isinstance(item, dict):
                    continue
                if item.get("type") == "function_call":
                    calls.append({"id": item.get("call_id") or item.get("id", ""), "name": item.get("name", ""),
                                  "arguments": _parse_args(item.get("arguments"))})
                elif item.get("type") == "message":
                    text += _as_text(item.get("content"))
                elif item.get("type") == "reasoning":
                    reasoning += _as_text(item.get("summary"))
        else:
            text = _as_text(_first(resp.get("content"), resp.get("text"), resp.get("output"), resp.get("answer")))
            for tc in resp.get("tool_calls") or []:
                fn = tc.get("function") or tc
                calls.append({"id": tc.get("id", ""), "name": fn.get("name", ""),
                              "arguments": _parse_args(fn.get("arguments", fn.get("input")))})
        return text or "", reasoning or "", calls, finish

    @staticmethod
    def _tool_results(messages: list[dict]) -> list[tuple[str, str, Any, bool]]:
        """从请求消息里找工具结果：(tool_call_id, name, content, is_error)"""
        out = []
        for m in messages:
            role = m.get("role")
            if role == "tool":
                out.append((m.get("tool_call_id") or "", m.get("name") or "", _as_text(m.get("content")),
                            bool(m.get("is_error"))))
            elif role == "function":
                out.append(("", m.get("name") or "", _as_text(m.get("content")), False))
            elif isinstance(m.get("content"), list):
                for b in m["content"]:
                    if isinstance(b, dict) and b.get("type") == "tool_result":
                        out.append((b.get("tool_use_id") or "", "", _as_text(b.get("content")), bool(b.get("is_error"))))
            if m.get("type") == "function_call_output":  # Responses API
                out.append((m.get("call_id") or "", "", _as_text(m.get("output")), False))
        return out

    @staticmethod
    def _history_calls(messages: list[dict]) -> list[dict]:
        out = []
        for m in messages:
            if m.get("role") == "assistant":
                for tc in m.get("tool_calls") or []:
                    fn = tc.get("function") or {}
                    out.append({"id": tc.get("id", ""), "name": fn.get("name", ""),
                                "arguments": _parse_args(fn.get("arguments"))})
                if isinstance(m.get("content"), list):
                    for b in m["content"]:
                        if isinstance(b, dict) and b.get("type") == "tool_use":
                            out.append({"id": b.get("id", ""), "name": b.get("name", ""), "arguments": b.get("input")})
            if m.get("type") == "function_call":
                out.append({"id": m.get("call_id", ""), "name": m.get("name", ""),
                            "arguments": _parse_args(m.get("arguments"))})
        return out

    def classify(self, name: str, args: Any) -> str:
        for typ, pat in self.classes:
            if pat.search(name or ""):
                return typ
        if isinstance(args, dict):
            keys = {k.lower() for k in args}
            if keys & {"command", "cmd", "script", "shell"}:
                return "CLI"
            if keys & {"url", "endpoint", "method", "path"}:
                return "API"
        return "TOOL"

    @staticmethod
    def summarize_input(args: Any) -> str:
        if isinstance(args, dict):
            for k in ("command", "cmd", "script", "shell", "code"):
                if isinstance(args.get(k), str):
                    return args[k]
            if args.get("url"):
                return f"{args.get('method', '').upper()} {args['url']}".strip()
            for k in ("path", "file_path", "query", "pattern", "prompt", "skill", "name"):
                if isinstance(args.get(k), str):
                    return f"{k}={args[k]}"
            return json.dumps(args, ensure_ascii=False)
        if args is None:
            return ""
        return str(args)

    # ------------------------------------------------------------- 主流程
    def build(self, rows: list[dict]) -> dict:
        llm_calls: list[dict] = []
        for row in rows:
            rec = row["record"]
            # 独立记录的工具调用（部分 agent 会把工具执行单独写一行）
            rtype = str(rec.get("type") or rec.get("event") or "").lower()
            req = self._request(rec)
            resp = self._response(rec)
            msgs = self._messages(rec, req)
            ts = row.get("ts") or find_ts(rec)
            start, end, dur = self._timing(rec, ts)
            if rtype in ("tool", "tool_call", "tool_result", "tool_execution") and not msgs:
                llm_calls.append({"_tool_record": True, "row": row, "rec": rec, "start": start, "end": end,
                                  "duration_ms": dur})
                continue
            text, reasoning, calls, finish = self._output(resp)
            model = _first(self._f(rec, "model"), req.get("model"), resp.get("model") if isinstance(resp, dict) else None,
                           rec.get("model")) or ""
            last_user = ""
            for m in reversed(msgs):
                if m.get("role") in ("user", "tool"):
                    last_user = _as_text(m.get("content"))
                    break
            tools_def = req.get("tools") or []
            llm_calls.append({
                "record_id": row.get("id"), "pod": row.get("pod"), "file": row.get("file"), "line_no": row.get("line_no"),
                "start": start, "end": end, "duration_ms": dur, "model": model,
                "message_count": len(msgs), "messages": msgs, "last_input": last_user,
                "system_prompt": _as_text(next((m.get("content") for m in msgs if m.get("role") == "system"), "")),
                "output_text": text, "reasoning": reasoning, "tool_calls": calls, "finish_reason": finish,
                "usage": self._usage(rec, resp),
                "tools_available": [(t.get("function") or t).get("name") for t in tools_def if isinstance(t, dict)],
                "error": rec.get("error") or (resp.get("error") if isinstance(resp, dict) else None),
            })

        tool_records = [c for c in llm_calls if c.get("_tool_record")]
        llm_calls = [c for c in llm_calls if not c.get("_tool_record")]
        # 时间排序（没有时间戳的保持文件顺序）
        if all(c["start"] is not None for c in llm_calls):
            llm_calls.sort(key=lambda c: c["start"])
        # 没有时间的用耗时累加合成一个相对时间轴
        if any(c["start"] is None for c in llm_calls):
            t = 0.0
            for c in llm_calls:
                c["start"] = t
                c["end"] = t + (c["duration_ms"] or 0) / 1000
                t = c["end"]
            for c in llm_calls:
                c["synthetic_time"] = True

        # 工具调用：response 里的 tool_calls + 后续请求历史里出现但 response 未记录的
        tools: list[dict] = []
        by_id: dict[str, dict] = {}
        for i, c in enumerate(llm_calls):
            c["index"] = i + 1
            for tc in c["tool_calls"]:
                t = {**tc, "issued_by": i + 1}
                tools.append(t)
                if tc.get("id"):
                    by_id[tc["id"]] = t
        for i, c in enumerate(llm_calls):
            if i == 0:
                continue
            for tc in self._history_calls(c["messages"]):
                if tc.get("id") and tc["id"] not in by_id:
                    t = {**tc, "issued_by": i, "from_history": True}
                    tools.append(t)
                    by_id[tc["id"]] = t
                    llm_calls[i - 1]["tool_calls"].append(tc)
        # 工具结果：在之后第一次出现的请求里找
        for i, c in enumerate(llm_calls):
            for tid, name, content, is_err in self._tool_results(c["messages"]):
                t = by_id.get(tid) if tid else None
                if t is None and not tid:
                    # 无 id 时按顺序匹配同名、尚无结果的调用
                    t = next((x for x in tools if x.get("output") is None and (not name or x["name"] == name)
                              and x["issued_by"] <= i), None)
                if t is not None and t.get("output") is None:
                    t["output"] = content
                    t["is_error"] = is_err
                    t["suspect_error"] = not is_err and _looks_error(content)
                    t["consumed_by"] = i + 1
        # 独立工具记录补充时间 / 输出
        for tr in tool_records:
            rec = tr["rec"]
            tid = rec.get("tool_call_id") or rec.get("id") or rec.get("call_id")
            t = by_id.get(tid) if tid else None
            if t is None:
                t = next((x for x in tools if x["name"] == rec.get("name") and not x.get("_timed")), None)
            if t is not None:
                t["start"], t["end"], t["duration_ms"], t["_timed"] = tr["start"], tr["end"], tr["duration_ms"], True
                if t.get("output") is None and rec.get("output") is not None:
                    t["output"] = _as_text(rec.get("output"))
        # 估算工具耗时：发出它的 LLM 调用结束 -> 消费它结果的 LLM 调用开始
        for idx, t in enumerate(tools):
            t["index"] = idx + 1
            t["type"] = self.classify(t["name"], t.get("arguments"))
            t["input_summary"] = self.summarize_input(t.get("arguments"))
            if not t.get("_timed"):
                issuer = llm_calls[t["issued_by"] - 1]
                t["start"] = issuer.get("end")
                consumer = llm_calls[t["consumed_by"] - 1] if t.get("consumed_by") else None
                t["end"] = consumer.get("start") if consumer else None
                if t["start"] is not None and t["end"] is not None:
                    t["duration_ms"] = max(0.0, (t["end"] - t["start"]) * 1000)
                    t["duration_estimated"] = True
                else:
                    t["duration_ms"] = None
            t.setdefault("output", None)
            t.setdefault("is_error", False)
            t.setdefault("suspect_error", False)
            t.pop("_timed", None)

        final_answer = ""
        for c in reversed(llm_calls):
            if c["output_text"] and not c["tool_calls"]:
                final_answer = c["output_text"]
                break
        return {"llm_calls": llm_calls, "tools": tools, "final_answer": final_answer}


# 只看输出开头的错误前缀和少量明确的失败短语，避免把日志内容里的 "Error" 误判为工具失败
_ERR_PREFIX_RE = re.compile(r"^\W{0,3}(error|err|exception|traceback|fatal|failed|failure|timeout|timed out|"
                            r"错误|失败|异常|超时|执行失败|调用失败)\b", re.I)
_ERR_PHRASE_RE = re.compile(r"(command not found|permission denied|connection refused|connection reset|"
                            r"no such file or directory|timed out after|non-zero exit|"
                            r"\b(exit[ _]code|returncode|exit status)\W{0,3}[1-9])", re.I)


def _looks_error(content: Any) -> bool:
    s = str(content or "").strip()
    if not s:
        return False
    if _ERR_PREFIX_RE.search(s[:80]):
        return True
    return bool(_ERR_PHRASE_RE.search(s[:500]))
