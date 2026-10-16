"""从 SKILL.md 中提取步骤，并把执行轨迹里的工具调用对齐到步骤上。"""
from __future__ import annotations

import math
import re
from typing import Any

STEP_HEADING_RE = re.compile(
    r"^(step\s*\d+|phase\s*\d+|第\s*[一二三四五六七八九十百\d]+\s*[步阶]|步骤\s*[\d一二三四五六七八九十]+|"
    r"\d+(\.\d+)*\s*[\.、:：)）]|\d+\s+\S)", re.I)
SECTION_HINT_RE = re.compile(r"(步骤|流程|操作|执行|排查|处理|workflow|steps?|procedure|instructions|process|runbook)", re.I)
OL_RE = re.compile(r"^(\d+)[\.\)、]\s+(.*)")
HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)")
INLINE_CODE_RE = re.compile(r"`([^`\n]+)`")
ASCII_TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_\-\.]*[A-Za-z0-9_]|[A-Za-z]{2,}")
CJK_RE = re.compile(r"[一-鿿]+")

STOPWORDS = {
    "the", "and", "for", "with", "this", "that", "from", "into", "then", "use", "using", "run", "if", "is", "are",
    "to", "of", "in", "on", "a", "an", "be", "or", "by", "it", "as", "at", "you", "your", "should", "must", "will",
    "can", "step", "steps", "check", "get", "set", "when", "make", "sure", "all", "any", "not", "no", "yes",
    "true", "false", "null", "none", "json", "data", "value", "name", "type", "result", "output", "input",
    "sh", "bash", "echo", "cat", "md", "skill",
}
CJK_STOP = {"使用", "执行", "如果", "然后", "进行", "需要", "可以", "是否", "检查", "步骤", "一个", "以下", "相关", "通过",
            "命令", "获取", "确认", "输出", "结果", "信息"}


def strip_front_matter(text: str) -> tuple[dict, str]:
    meta: dict[str, str] = {}
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end > 0:
            for line in text[3:end].splitlines():
                if ":" in line:
                    k, _, v = line.partition(":")
                    meta[k.strip()] = v.strip()
            text = text[end + 4:]
    return meta, text


def _clean_title(s: str) -> str:
    s = re.sub(r"[*_`]+", "", s).strip()
    return s[:120]


def parse_steps(markdown: str) -> dict:
    """返回 {meta, steps:[{id,title,text,commands,level}] , strategy}"""
    meta, body = strip_front_matter(markdown)
    lines = body.splitlines()

    # 标记代码块行
    in_code = False
    code_flags = []
    for line in lines:
        if line.strip().startswith("```"):
            code_flags.append(True)
            in_code = not in_code
            continue
        code_flags.append(in_code)

    headings = []  # (line_idx, level, title)
    for i, line in enumerate(lines):
        if code_flags[i]:
            continue
        m = HEADING_RE.match(line)
        if m:
            headings.append((i, len(m.group(1)), m.group(2).strip()))

    def section_end(idx: int, level: int) -> int:
        for j, lv, _ in headings:
            if j > idx and lv <= level:
                return j
        return len(lines)

    steps: list[dict] = []
    strategy = ""

    # 策略 A：编号标题（## 1. xxx / ## Step 2 / ### 第三步）
    step_heads = [(i, lv, t) for i, lv, t in headings if STEP_HEADING_RE.match(_clean_title(t))]
    if len(step_heads) >= 2:
        # 取出现最多的层级
        levels: dict[int, int] = {}
        for _, lv, _ in step_heads:
            levels[lv] = levels.get(lv, 0) + 1
        lvl = max(levels, key=lambda k: (levels[k], -k))
        for i, lv, t in step_heads:
            if lv != lvl:
                continue
            end = section_end(i, lv)
            steps.append({"title": _clean_title(t), "start": i, "end": end})
        strategy = "heading"

    # 策略 B：步骤类章节下的顶层有序列表
    if not steps:
        candidates = []
        for hi, (i, lv, t) in enumerate(headings):
            if SECTION_HINT_RE.search(t):
                candidates.append((i + 1, section_end(i, lv)))
        if not candidates:
            candidates = [(0, len(lines))]
        for s0, s1 in candidates:
            items = []
            for i in range(s0, s1):
                if code_flags[i]:
                    continue
                m = OL_RE.match(lines[i])
                if m:
                    items.append((i, m.group(2)))
            if len(items) >= 2:
                for k, (i, t) in enumerate(items):
                    end = items[k + 1][0] if k + 1 < len(items) else s1
                    # 有序列表项后遇到同级标题即截断
                    for j, _, _ in headings:
                        if i < j < end:
                            end = j
                            break
                    steps.append({"title": _clean_title(t), "start": i, "end": end})
                strategy = "ordered-list"
                break

    # 策略 C：二级标题
    if not steps:
        h2 = [(i, lv, t) for i, lv, t in headings if lv == 2] or [(i, lv, t) for i, lv, t in headings if lv == 3]
        for i, lv, t in h2:
            steps.append({"title": _clean_title(t), "start": i, "end": section_end(i, lv)})
        strategy = "section"

    out = []
    for n, s in enumerate(steps, 1):
        seg = lines[s["start"]:s["end"]]
        flags = code_flags[s["start"]:s["end"]]
        text = "\n".join(seg).strip()
        commands = []
        for line, is_code in zip(seg, flags):
            st = line.strip()
            if is_code and st and not st.startswith("```") and not st.startswith("#"):
                commands.append(st.lstrip("$ ").strip())
        for m in INLINE_CODE_RE.finditer("\n".join(l for l, c in zip(seg, flags) if not c)):
            v = m.group(1).strip()
            if len(v) >= 3:
                commands.append(v)
        out.append({
            "id": f"S{n}", "index": n, "title": s["title"], "text": text,
            "line": s["start"] + 1 + (len(markdown.splitlines()) - len(lines)),
            "commands": list(dict.fromkeys(commands))[:20],
        })
    return {"meta": meta, "steps": out, "strategy": strategy}


# ---------------------------------------------------------------- 分词与打分
def tokens(text: str) -> set[str]:
    text = text or ""
    out = set()
    for t in ASCII_TOKEN_RE.findall(text):
        t = t.lower().strip(".-")
        if len(t) >= 2 and t not in STOPWORDS and not t.isdigit():
            out.add(t)
            if "/" not in t and "." in t:
                out.update(p for p in t.split(".") if len(p) >= 3 and p not in STOPWORDS)
    for seg in CJK_RE.findall(text):
        for i in range(len(seg) - 1):
            bg = seg[i:i + 2]
            if bg not in CJK_STOP:
                out.add(bg)
    return out


def _cmd_head(cmd: str) -> list[str]:
    parts = [p for p in re.split(r"\s+", cmd.strip()) if p and not p.startswith("-")]
    # 跳过 sudo / 变量赋值
    parts = [p for p in parts if p != "sudo" and "=" not in p]
    return [p.lower() for p in parts[:3]]


def _call_text(call: dict) -> str:
    args = call.get("arguments")
    s = call.get("input_summary") or ""
    if not isinstance(args, str):
        import json
        try:
            s += " " + json.dumps(args, ensure_ascii=False)[:2000]
        except (TypeError, ValueError):
            pass
    return f"{call.get('name', '')} {s}"


def _cmd_score(step_cmds: list[str], call_text: str) -> float:
    words = re.split(r"[\s\"'`;|&()]+", call_text.lower())
    wordset = set(words)
    best = 0.0
    for c in step_cmds:
        head = _cmd_head(c)
        if not head:
            continue
        if head[0] not in wordset:
            continue
        hit = sum(1 for h in head if h in wordset or h in call_text.lower())
        best = max(best, hit / len(head))
    return best


def is_skill_load(call: dict, skill_name: str | None) -> bool:
    text = _call_text(call).lower()
    if "skill.md" in text:
        return True
    name = (call.get("name") or "").lower()
    return name in ("skill", "load_skill", "use_skill", "read_skill") or (
        bool(skill_name) and name.startswith("skill") and skill_name.lower() in text)


ANSWER_STEP_RE = re.compile(r"(结论|总结|汇总|回复|答复|报告|输出|告知|conclu|summar|report|answer|respond|reply)", re.I)


def answer_steps(steps: list[dict], final_answer: str) -> list[str]:
    """没有命令、且是「给出结论/总结」类的步骤，由最终回答来完成。"""
    if not final_answer.strip():
        return []
    return [s["id"] for s in steps if not s["commands"] and ANSWER_STEP_RE.search(s["title"])]


def align(steps: list[dict], tools: list[dict], threshold: float = 0.18, skill_name: str | None = None,
          final_answer: str = "") -> dict:
    n = len(steps)
    step_tokens = [tokens(s["title"] + "\n" + s["text"]) for s in steps]
    df: dict[str, int] = {}
    for ts in step_tokens:
        for t in ts:
            df[t] = df.get(t, 0) + 1
    idf = {t: math.log(1 + n / c) for t, c in df.items()}

    assignments = []
    expected = 0  # 期望的下一个步骤下标，用于顺序偏好
    for call in tools:
        if is_skill_load(call, skill_name):
            assignments.append({"call": call["index"], "step": None, "score": 1.0, "kind": "skill_load",
                                "reason": "加载 skill 文档"})
            continue
        ctext = _call_text(call)
        ctoks = tokens(ctext)
        best, best_i, best_detail = 0.0, None, ""
        for i, s in enumerate(steps):
            st = step_tokens[i]
            if not st:
                continue
            common = st & ctoks
            denom = sum(sorted((idf[t] for t in st), reverse=True)[:12]) or 1
            kw = min(1.0, sum(idf[t] for t in common) / denom * 2)
            cmd = _cmd_score(s["commands"], ctext)
            score = 0.6 * cmd + 0.4 * kw if s["commands"] else kw
            if i in (expected, expected - 1):  # 顺序偏好：当前或下一步骤
                score += 0.05
            if score > best:
                best, best_i = score, i
                best_detail = f"命令匹配 {cmd:.2f}，关键词 {kw:.2f}（{', '.join(sorted(common)[:6])}）"
        if best_i is not None and best >= threshold:
            assignments.append({"call": call["index"], "step": steps[best_i]["id"], "score": round(best, 3),
                                "kind": "matched", "reason": best_detail})
            expected = max(expected, best_i + 1)
        else:
            assignments.append({"call": call["index"], "step": None, "score": round(best, 3), "kind": "unmatched",
                                "reason": "未找到对应步骤" + (f"（最高分 {best:.2f}）" if best else "")})
    for sid in answer_steps(steps, final_answer):
        assignments.append({"call": "answer", "step": sid, "score": 1.0, "kind": "answer", "reason": "由最终回答完成"})
    return summarize(steps, tools, assignments)


def summarize(steps: list[dict], tools: list[dict], assignments: list[dict]) -> dict:
    """根据 call -> step 映射计算每个步骤的状态、顺序以及综合得分。heuristic 与 LLM 对齐共用。"""
    idx_of = {s["id"]: i for i, s in enumerate(steps)}
    tool_by_idx = {t["index"]: t for t in tools}
    first_pos: dict[str, int] = {}
    calls_of: dict[str, list[int]] = {s["id"]: [] for s in steps}
    for pos, a in enumerate(assignments):
        sid = a.get("step")
        if sid in calls_of:
            calls_of[sid].append(a["call"])
            first_pos.setdefault(sid, pos)
    # 按首次出现的顺序求最长递增子序列，不在其中的步骤视为乱序
    seq = sorted(first_pos, key=lambda s: first_pos[s])
    vals = [idx_of[s] for s in seq]
    lis_set = set(_lis(vals))
    in_order_ids = {seq[k] for k in lis_set}

    step_rows = []
    done = ooo = 0
    for s in steps:
        calls = calls_of[s["id"]]
        errors = sum(1 for c in calls if tool_by_idx.get(c, {}).get("is_error"))
        suspects = sum(1 for c in calls if tool_by_idx.get(c, {}).get("suspect_error"))
        tool_calls = [c for c in calls if c != "answer"]
        if not calls:
            status = "missing"
        elif s["id"] in in_order_ids:
            status = "done"
            done += 1
        else:
            status = "out_of_order"
            ooo += 1
        dur = sum((tool_by_idx.get(c, {}).get("duration_ms") or 0) for c in calls)
        step_rows.append({**s, "status": status, "calls": calls, "call_count": len(tool_calls), "errors": errors, "suspect_errors": suspects,
                          "retries": max(0, len(tool_calls) - 1), "duration_ms": dur,
                          "by_answer": "answer" in calls,
                          "first_order": seq.index(s["id"]) + 1 if s["id"] in seq else None})
    total = len(steps) or 1
    matched = done + ooo
    coverage = matched / total
    order = (done / matched) if matched else 0
    extra = sum(1 for a in assignments if a.get("kind") == "unmatched")
    score = round(100 * (0.7 * coverage + 0.3 * order) - min(10, extra * 2)) if steps else None
    return {
        "assignments": assignments,
        "steps": step_rows,
        "scores": {
            "compliance": max(0, score) if score is not None else None,
            "coverage": round(coverage * 100), "order": round(order * 100),
            "steps_total": len(steps), "steps_done": done, "steps_out_of_order": ooo,
            "steps_missing": len(steps) - matched, "extra_calls": extra,
        },
    }


def _lis(vals: list[int]) -> list[int]:
    """返回最长非降子序列的下标列表。"""
    if not vals:
        return []
    n = len(vals)
    dp = [1] * n
    prev = [-1] * n
    for i in range(n):
        for j in range(i):
            if vals[j] <= vals[i] and dp[j] + 1 > dp[i]:
                dp[i], prev[i] = dp[j] + 1, j
    k = max(range(n), key=lambda i: dp[i])
    out = []
    while k != -1:
        out.append(k)
        k = prev[k]
    return out[::-1]


def detect_skill(trace: dict, skill_names: list[str]) -> str | None:
    if not skill_names:
        return None
    counts: dict[str, int] = {s: 0 for s in skill_names}
    blobs: list[str] = []
    for c in trace["llm_calls"][:3]:
        blobs.append(c.get("system_prompt") or "")
    for t in trace["tools"]:
        blobs.append(_call_text(t))
    text = "\n".join(blobs)
    for s in skill_names:
        counts[s] = len(re.findall(re.escape(s), text))
    best = max(counts, key=lambda s: counts[s])
    return best if counts[best] > 0 else None


def compact_calls_for_llm(tools: list[dict], limit_in: int = 400, limit_out: int = 300) -> list[dict[str, Any]]:
    out = []
    for t in tools:
        out.append({
            "call": t["index"], "type": t["type"], "tool": t["name"],
            "input": (t.get("input_summary") or "")[:limit_in],
            "output": (str(t.get("output") or ""))[:limit_out],
            "error": bool(t.get("is_error")),
        })
    return out
