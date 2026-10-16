"""模拟环境：Agent 问答接口 + OpenAI 兼容 LLM 接口。仅用于本地演示 / 测试。

- POST /api/chat           模拟 agent 执行已发布到 pod 的 skill，写 llm-calls.jsonl，返回 conversation_id
                           支持 header x-fault-inject: tool_timeout / tool_error / skip_step
- POST /v1/chat/completions 模拟本地 LLM（中译英、执行评审）

启动：python3 mock/agent_server.py  （默认端口 9100）
"""
from __future__ import annotations

import json
import os
import random
import re
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from app.services.align import parse_steps  # noqa: E402

MOCK_ROOT = Path(os.environ.get("MOCK_ROOT", ROOT / "data" / "mock"))
PODS = MOCK_ROOT / "pods"
app = FastAPI()


def iso(t: float) -> str:
    return datetime.fromtimestamp(t, timezone.utc).isoformat()


def pick_pod(name: str = "") -> Path | None:
    """name 指定 pod（测试用，header x-mock-pod）；否则在名称含 agent 的 pod 里随机选一个。"""
    if name:
        return PODS / name if (PODS / name).is_dir() else None
    pods = sorted(p for p in PODS.iterdir() if p.is_dir() and "agent" in p.name) if PODS.is_dir() else []
    return random.choice(pods) if pods else None


def load_skill(pod: Path, question: str) -> tuple[str, str]:
    base = pod / "opt/agent/skills/zh"
    skills = sorted(p for p in base.iterdir() if (p / "SKILL.md").exists()) if base.is_dir() else []
    if not skills:
        return "", ""
    chosen = skills[0]
    for s in skills:
        if any(k in question for k in ("延迟", "latency", "慢")) and "latency" in s.name:
            chosen = s
    return chosen.name, (chosen / "SKILL.md").read_text(encoding="utf-8")


def fill(cmd: str) -> str:
    return (cmd.replace("<namespace>", "prod").replace("<pod>", "order-svc-7d9f8-abcde")
            .replace("<service>", "order-svc"))


TOOLS = [
    {"type": "function", "function": {"name": "bash", "description": "Run a shell command",
                                      "parameters": {"type": "object", "properties": {"command": {"type": "string"}}}}},
    {"type": "function", "function": {"name": "http_request", "description": "Call HTTP API",
                                      "parameters": {"type": "object", "properties": {"method": {"type": "string"},
                                                                                      "url": {"type": "string"}}}}},
    {"type": "function", "function": {"name": "read_file", "description": "Read a file",
                                      "parameters": {"type": "object", "properties": {"path": {"type": "string"}}}}},
]


def simulate(question: str, faults: list[str], conv: str, pod: Path) -> tuple[list[dict], str]:
    rnd = random.Random(conv)
    skill, content = load_skill(pod, question)
    steps = parse_steps(content)["steps"] if content else []
    system = (f"You are an SRE agent. Available skills: {skill}. "
              f"Skill file: /opt/agent/skills/zh/{skill}/SKILL.md. Follow the skill steps strictly.")
    messages = [{"role": "system", "content": system}, {"role": "user", "content": question}]
    records = []
    t = time.time()
    plan: list[tuple[str, dict, str]] = []
    plan.append(("read_file", {"path": f"/opt/agent/skills/zh/{skill}/SKILL.md"}, content[:800]))
    for s in steps:
        if "skip_step" in faults and s["index"] == 3:
            continue
        cmds = [c for c in s["commands"] if c.startswith(("kubectl", "curl", "GET", "POST"))]
        if not cmds:
            continue
        c = fill(cmds[0])
        if c.startswith(("GET", "POST")):
            m, _, path = c.partition(" ")
            plan.append(("http_request", {"method": m, "url": "http://monitor" + path},
                         json.dumps({"error_rate": 0.031, "p99_ms": 820})))
        else:
            out = {"kubectl get": "NAME READY STATUS RESTARTS\norder-svc-7d9f8-abcde 0/1 CrashLoopBackOff 7",
                   "kubectl describe": "Events:\n  Warning  BackOff  kubelet  Back-off restarting failed container",
                   "kubectl logs": "java.lang.OutOfMemoryError: Java heap space\n\tat com.shop.order...",
                   "kubectl top": "NAME CPU(cores) MEMORY(bytes)\norder-svc-7d9f8-abcde 950m 1023Mi",
                   "curl": json.dumps({"p99_ms": 1820, "qps": 340})}
            res = next((v for k, v in out.items() if c.startswith(k)), "ok")
            plan.append(("bash", {"command": c}, res))
    # 故障注入：第 2 个工具调用失败
    fail_at = 2 if any(f in faults for f in ("tool_timeout", "tool_error")) and len(plan) > 2 else None

    i = 0
    call_no = 0
    while i <= len(plan):
        call_no += 1
        latency = rnd.uniform(900, 3800)
        prompt_tokens = 1200 + sum(len(json.dumps(m, ensure_ascii=False)) for m in messages) // 3
        if i < len(plan):
            name, args, result = plan[i]
            tc_id = f"call_{uuid.uuid4().hex[:8]}"
            msg = {"role": "assistant", "content": "" if rnd.random() < 0.6 else f"接下来执行：{name}",
                   "tool_calls": [{"id": tc_id, "type": "function",
                                   "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)}}]}
            finish = "tool_calls"
        else:
            msg = {"role": "assistant", "content": (
                "结论：order-svc 的 Pod 因 Java 堆内存不足（OOMKilled）进入 CrashLoopBackOff。"
                "建议将 -Xmx 调整为 limits 的 75%，并把 memory limit 提升到 2Gi。")}
            finish = "stop"
        completion_tokens = rnd.randint(60, 420)
        records.append({
            "timestamp": iso(t), "conversation_id": conv, "request_id": uuid.uuid4().hex, "latency_ms": round(latency, 1),
            "request": {"model": "qwen3-235b-a22b", "messages": json.loads(json.dumps(messages)), "tools": TOOLS},
            "response": {"id": f"chatcmpl-{uuid.uuid4().hex[:10]}", "model": "qwen3-235b-a22b",
                         "choices": [{"index": 0, "message": msg, "finish_reason": finish}],
                         "usage": {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens,
                                   "total_tokens": prompt_tokens + completion_tokens,
                                   "prompt_tokens_details": {"cached_tokens": int(prompt_tokens * 0.6) if call_no > 1 else 0}}},
        })
        t += latency / 1000
        if i >= len(plan):
            break
        messages.append(msg)
        tool_ms = rnd.uniform(200, 2500)
        if fail_at is not None and i == fail_at:
            err = "Error: command timed out after 30s" if "tool_timeout" in faults else "Error: connection refused (exit code 1)"
            messages.append({"role": "tool", "tool_call_id": tc_id, "content": err})
            tool_ms = 30000 if "tool_timeout" in faults else 300
            fail_at = None  # 重试一次
            t += tool_ms / 1000
            continue
        messages.append({"role": "tool", "tool_call_id": tc_id, "content": result})
        t += tool_ms / 1000
        i += 1
    return records, records[-1]["response"]["choices"][0]["message"]["content"]


@app.post("/api/chat")
async def chat(request: Request):
    body = await request.json()
    question = body.get("query") or body.get("question") or ""
    faults = [f.strip() for f in request.headers.get("x-fault-inject", "").split(",") if f.strip()]
    conv = request.headers.get("x-conversation-id") or f"conv-{uuid.uuid4().hex[:12]}"
    pod = pick_pod(request.headers.get("x-mock-pod", ""))
    if pod is None:
        return JSONResponse({"error": "no agent pod"}, status_code=503)
    records, answer = simulate(question, faults, conv, pod)
    log = pod / "var/log/agent/llm-calls.jsonl"
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("a", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    if body.get("stream"):
        def gen():
            yield f"data: {json.dumps({'conversation_id': conv}, ensure_ascii=False)}\n\n"
            for i in range(0, len(answer), 8):
                yield f"data: {json.dumps({'delta': answer[i:i + 8]}, ensure_ascii=False)}\n\n"
            yield "data: [DONE]\n\n"
        return StreamingResponse(gen(), media_type="text/event-stream", headers={"x-conversation-id": conv})
    return JSONResponse({"conversation_id": conv, "answer": answer, "pod": pod.name},
                        headers={"x-conversation-id": conv})


def _block(text: str, tag: str) -> str:
    m = re.search(f"<{tag}>\\n(.*?)\\n</{tag}>", text, re.S)
    return m.group(1) if m else ""


@app.post("/v1/chat/completions")
async def completions(request: Request):
    body = await request.json()
    prompt = body["messages"][-1]["content"]
    if "call_alignment" in prompt:
        steps = re.findall(r"- (S\d+): (.*)", prompt)
        calls = json.loads(prompt.split("## 工具调用序列（按时间顺序）")[1].split("## Agent")[0].strip())
        align, k = [], 0
        for c in calls:
            if "SKILL.md" in c["input"]:
                align.append({"call": c["call"], "step": None, "reason": "加载 skill"})
                continue
            sid = steps[min(k, len(steps) - 1)][0] if steps else None
            align.append({"call": c["call"], "step": sid, "reason": "按顺序对应"})
            if not c["error"]:
                k += 1
        done = {a["step"] for a in align if a["step"]}
        content = json.dumps({
            "call_alignment": align,
            "steps": [{"step": s, "status": "done" if s in done else "skipped", "comment": t[:30]} for s, t in steps],
            "score": int(100 * len(done) / max(1, len(steps))),
            "summary": "Agent 基本按 skill 步骤执行，个别步骤缺失。（mock LLM 评审）",
            "issues": ["工具失败后的重试没有说明原因"] if any(c["error"] for c in calls) else [],
            "suggestions": ["在步骤 5 中明确监控接口的完整 URL 与必填参数", "在注意事项中补充工具超时的处理方式"],
        }, ensure_ascii=False)
    elif "<en_current>" in prompt:
        en_old = _block(prompt, "en_current")
        zh_diff = _block(prompt, "zh_diff")
        added = [l[1:] for l in zh_diff.splitlines() if l.startswith("+") and not l.startswith("+++") and l[1:].strip()]
        lines = en_old.splitlines()
        lines += [f"[EN-mock] {a}" for a in added]
        content = "<en_file>\n" + "\n".join(lines) + "\n</en_file>"
    elif "Translate the following Chinese file" in prompt:
        zh = _block(prompt, "zh_new")
        content = "<en_file>\n" + "\n".join(f"[EN-mock] {l}" if l.strip() else l for l in zh.splitlines()) + "\n</en_file>"
    else:
        content = "OK"
    return {"id": "mock", "object": "chat.completion", "model": body.get("model"),
            "choices": [{"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": len(prompt) // 3, "completion_tokens": len(content) // 3}}


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=int(os.environ.get("MOCK_PORT", 9100)))
