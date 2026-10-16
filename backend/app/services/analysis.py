"""会话分析：执行轨迹 + skill 步骤对齐 + 耗时/token 统计 + 可选的 LLM 评审。"""
from __future__ import annotations

import json

from ..config import Config
from ..db import DB, dumps, loads, now
from .align import align, answer_steps, compact_calls_for_llm, detect_skill, parse_steps, summarize
from .deploy import DeployService
from .llm import LLMClient, extract_json
from .logs import LogService
from .skills import SkillError, SkillStore
from .trace import TraceBuilder

REVIEW_SYSTEM = """你是 Agent Skill 执行质量评审专家。给你一份 skill 的步骤定义和一次真实会话中 Agent 的工具调用序列，
请判断 Agent 是否按照 skill 执行，并给出对 skill 文档本身的优化建议。只输出 JSON。"""

REVIEW_PROMPT = """## 用户问题
{question}

## Skill 步骤（{skill} v{version}，{lang}）
{steps}

## 工具调用序列（按时间顺序）
{calls}

## Agent 最终回答（截断）
{answer}

请输出如下 JSON（不要输出其他内容）：
{{
  "call_alignment": [{{"call": 调用序号, "step": "步骤ID 或 null", "reason": "简述"}}],
  "steps": [{{"step": "步骤ID", "status": "done|partial|skipped|deviated", "comment": "简述执行情况"}}],
  "score": 0到100的整数（遵从度）,
  "summary": "一两句话总结执行情况",
  "issues": ["发现的问题，例如跳步、重复调用、参数错误、未处理工具错误"],
  "suggestions": ["对 skill 文档的具体修改建议，便于下次 Agent 更好地遵从"]
}}"""


class AnalysisService:
    def __init__(self, cfg: Config, db: DB, store: SkillStore, logs: LogService, deploy: DeployService,
                 llm: LLMClient):
        self.cfg = cfg
        self.db = db
        self.store = store
        self.logs = logs
        self.deploy = deploy
        self.llm = llm

    def _context(self, conversation_id: str, skill: str | None, version: int | None, lang: str | None) -> dict:
        rows = self.logs.records(conversation_id)
        builder = TraceBuilder(self.cfg["analysis"])
        trace = builder.build(rows)
        skill_names = [s["name"] for s in self.store.list_skills()]
        detected = detect_skill(trace, skill_names)
        skill = skill or detected
        pods = sorted({r["pod"] for r in rows})
        first_ts = next((c["start"] for c in trace["llm_calls"] if c.get("start") and not c.get("synthetic_time")), None)
        version_source = "manual" if version else None
        if skill and not version:
            if first_ts:
                version = self.deploy.version_deployed_before(skill, first_ts, pods)
                version_source = "deployed" if version else None
            if not version:
                latest = self.store.latest_version(skill)
                version = latest["seq"] if latest else None
                version_source = "latest" if version else None
        lang = lang or self.cfg["analysis"]["default_lang"]
        steps_info = {"meta": {}, "steps": [], "strategy": ""}
        skill_error = None
        if skill:
            try:
                content = self.store.read_file(skill, lang, self.cfg["skills"]["main_file"], ref=version)
                steps_info = parse_steps(content)
            except SkillError as e:
                skill_error = str(e)
        chat = self.db.one("SELECT id, question, answer, faults_json, created_at, latency_ms FROM chats "
                           "WHERE conversation_id=? ORDER BY id DESC LIMIT 1", (conversation_id,))
        return {"rows": rows, "trace": trace, "skill": skill, "detected_skill": detected, "version": version,
                "version_source": version_source, "lang": lang, "steps_info": steps_info, "pods": pods,
                "skill_error": skill_error, "chat": chat}

    def analyze(self, conversation_id: str, skill: str | None = None, version: int | None = None,
                lang: str | None = None) -> dict:
        ctx = self._context(conversation_id, skill, version, lang)
        trace = ctx["trace"]
        llm_calls, tools = trace["llm_calls"], trace["tools"]
        steps = ctx["steps_info"]["steps"]
        heuristic = align(steps, tools, float(self.cfg["analysis"]["match_threshold"]), ctx["skill"],
                          trace["final_answer"])

        review_row = self.db.one(
            "SELECT * FROM analyses WHERE conversation_id=? AND kind='llm-review' AND COALESCE(skill,'')=? "
            "AND COALESCE(version_seq,-1)=? AND lang=? ORDER BY id DESC LIMIT 1",
            (conversation_id, ctx["skill"] or "", ctx["version"] if ctx["version"] is not None else -1, ctx["lang"]))
        review = loads(review_row["result_json"]) if review_row else None
        if review_row:
            review["created_at"] = review_row["created_at"]

        # ------------------------------------------------ 指标
        starts = [c["start"] for c in llm_calls if c.get("start") is not None]
        ends = [c["end"] for c in llm_calls if c.get("end") is not None] + \
               [t["end"] for t in tools if t.get("end") is not None]
        t0 = min(starts) if starts else 0
        total_ms = (max(ends) - t0) * 1000 if starts and ends else None
        llm_ms = sum(c.get("duration_ms") or 0 for c in llm_calls)
        tool_ms = sum(t.get("duration_ms") or 0 for t in tools)
        usage = {k: sum(c["usage"][k] for c in llm_calls) for k in ("prompt", "completion", "cached", "reasoning", "total")}
        by_type: dict[str, dict] = {}
        for t in tools:
            d = by_type.setdefault(t["type"], {"count": 0, "errors": 0, "duration_ms": 0})
            d["count"] += 1
            d["errors"] += 1 if t.get("is_error") else 0
            d["duration_ms"] += t.get("duration_ms") or 0
        by_tool: dict[str, dict] = {}
        for t in tools:
            d = by_tool.setdefault(t["name"], {"name": t["name"], "type": t["type"], "count": 0, "errors": 0,
                                               "duration_ms": 0})
            d["count"] += 1
            d["errors"] += 1 if t.get("is_error") else 0
            d["duration_ms"] += t.get("duration_ms") or 0

        # ------------------------------------------------ 时间轴
        timeline = []
        for c in llm_calls:
            if c.get("start") is None:
                continue
            timeline.append({"kind": "llm", "ref": c["index"], "label": f"LLM #{c['index']}",
                             "start_ms": (c["start"] - t0) * 1000, "duration_ms": c.get("duration_ms") or 0,
                             "tokens": c["usage"]["total"], "model": c["model"]})
        step_of = {a["call"]: a.get("step") for a in heuristic["assignments"]}
        for t in tools:
            if t.get("start") is None:
                continue
            timeline.append({"kind": "tool", "ref": t["index"], "label": t["name"], "type": t["type"],
                             "start_ms": (t["start"] - t0) * 1000, "duration_ms": t.get("duration_ms") or 0,
                             "error": t.get("is_error"), "suspect": t.get("suspect_error"), "estimated": t.get("duration_estimated", False),
                             "step": step_of.get(t["index"])})

        llm_out = []
        for c in llm_calls:
            llm_out.append({k: c.get(k) for k in (
                "index", "record_id", "pod", "file", "line_no", "start", "end", "duration_ms", "model", "message_count",
                "last_input", "output_text", "reasoning", "finish_reason", "usage", "tools_available", "error",
                "synthetic_time")} | {
                "start_ms": (c["start"] - t0) * 1000 if c.get("start") is not None else None,
                "tool_calls": [{"id": tc.get("id"), "name": tc.get("name")} for tc in c["tool_calls"]],
            })
        tools_out = []
        for t in tools:
            tools_out.append({k: t.get(k) for k in (
                "index", "id", "name", "type", "arguments", "input_summary", "output", "is_error", "suspect_error",
                "issued_by", "consumed_by", "duration_ms", "duration_estimated", "from_history")} | {
                "start_ms": (t["start"] - t0) * 1000 if t.get("start") is not None else None})

        chat = ctx["chat"]
        return {
            "conversation_id": conversation_id,
            "record_count": len(ctx["rows"]),
            "pods": ctx["pods"],
            "skill": ctx["skill"], "detected_skill": ctx["detected_skill"], "version": ctx["version"],
            "version_source": ctx["version_source"], "lang": ctx["lang"], "skill_error": ctx["skill_error"],
            "skill_meta": ctx["steps_info"]["meta"], "step_strategy": ctx["steps_info"]["strategy"],
            "chat": {"id": chat["id"], "question": chat["question"], "answer": chat["answer"],
                     "faults": loads(chat["faults_json"], []), "created_at": chat["created_at"]} if chat else None,
            "final_answer": trace["final_answer"],
            "start_ts": t0 or None,
            "metrics": {
                "total_ms": total_ms, "llm_ms": llm_ms, "tool_ms": tool_ms, "llm_calls": len(llm_calls),
                "tool_calls": len(tools), "tool_errors": sum(1 for t in tools if t.get("is_error")),
                "tool_suspect_errors": sum(1 for t in tools if t.get("suspect_error")),
                "usage": usage, "by_type": by_type, "by_tool": sorted(by_tool.values(), key=lambda x: -x["count"]),
                "models": sorted({c["model"] for c in llm_calls if c["model"]}),
            },
            "llm_calls": llm_out,
            "tools": tools_out,
            "timeline": timeline,
            "alignment": heuristic,
            "llm_review": review,
        }

    def review(self, conversation_id: str, skill: str | None = None, version: int | None = None,
               lang: str | None = None) -> dict:
        ctx = self._context(conversation_id, skill, version, lang)
        steps = ctx["steps_info"]["steps"]
        if not steps:
            raise ValueError("没有可对齐的 skill 步骤（请选择 skill / 检查 SKILL.md）")
        tools = ctx["trace"]["tools"]
        steps_text = "\n".join(
            f"- {s['id']}: {s['title']}" + (f"\n  命令/接口: {'; '.join(s['commands'][:5])}" if s["commands"] else "")
            for s in steps)
        calls_text = json.dumps(compact_calls_for_llm(tools), ensure_ascii=False, indent=1)
        chat = ctx["chat"] or {}
        first_input = ctx["trace"]["llm_calls"][0]["last_input"] if ctx["trace"]["llm_calls"] else ""
        prompt = REVIEW_PROMPT.format(
            question=(chat.get("question") or first_input or "")[:2000],
            skill=ctx["skill"], version=ctx["version"], lang=ctx["lang"], steps=steps_text, calls=calls_text,
            answer=(ctx["trace"]["final_answer"] or "")[:1500])
        raw = self.llm.chat([{"role": "system", "content": REVIEW_SYSTEM}, {"role": "user", "content": prompt}])
        data = extract_json(raw)
        if not isinstance(data, dict):
            raise ValueError("LLM 返回格式不正确")
        # 用 LLM 的映射重新计算步骤状态，便于和启发式结果切换对比
        valid_steps = {s["id"] for s in steps}
        assignments = []
        llm_map = {int(a.get("call")): a for a in data.get("call_alignment") or [] if str(a.get("call", "")).isdigit()}
        for t in tools:
            a = llm_map.get(t["index"], {})
            sid = a.get("step") if a.get("step") in valid_steps else None
            assignments.append({"call": t["index"], "step": sid, "kind": "matched" if sid else "unmatched",
                                "reason": a.get("reason", ""), "score": None})
        llm_step_status = {x.get("step"): x.get("status") for x in data.get("steps") or []}
        for sid in answer_steps(steps, ctx["trace"]["final_answer"]):
            if llm_step_status.get(sid) in (None, "done", "partial"):
                assignments.append({"call": "answer", "step": sid, "kind": "answer", "reason": "由最终回答完成",
                                    "score": None})
        data["alignment"] = summarize(steps, tools, assignments)
        self.db.execute(
            "INSERT INTO analyses(conversation_id, skill, version_seq, lang, kind, result_json, created_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (conversation_id, ctx["skill"], ctx["version"], ctx["lang"], "llm-review", dumps(data), now()))
        return data
