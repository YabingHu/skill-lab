import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import shell  # noqa: E402
from app.config import Config  # noqa: E402
from app.db import DB  # noqa: E402
from app.services.align import align, parse_steps  # noqa: E402
from app.services.chat import extract_conversation_id, fill_template  # noqa: E402
from app.services.env import filter_pods  # noqa: E402
from app.services.logs import find_conversation_id, parse_ts  # noqa: E402
from app.services.skills import SkillStore  # noqa: E402
from app.services.sync import apply_hunks, compute_hunks, extract_en_file  # noqa: E402
from app.services.trace import TraceBuilder  # noqa: E402
from app.config import DEFAULTS  # noqa: E402


# ------------------------------------------------------------------ 中英同步 hunk
def test_hunks_select_subset():
    old = "a\nb\nc\nd\n"
    new = "a\nB\nc\nd\ne\n"
    hunks = compute_hunks(old, new)
    assert [h["tag"] for h in hunks] == ["replace", "insert"]
    assert apply_hunks(old, new, set()) == old
    assert apply_hunks(old, new, {h["id"] for h in hunks}) == new
    only_first = apply_hunks(old, new, {hunks[0]["id"]})
    assert only_first == "a\nB\nc\nd\n"


def test_hunk_without_trailing_newline():
    old = "x\ny\n"
    new = "x2\ny\n"
    h = compute_hunks(old, new)
    assert apply_hunks(old, new, {h[0]["id"]}) == new


def test_extract_en_file_with_code_fences():
    body = "# T\n\n```\nkubectl get pods\n```\n\nend\n"
    assert extract_en_file(f"好的\n<en_file>\n{body}</en_file>") == body.rstrip("\n")
    assert extract_en_file("```markdown\n# T\n```") == "# T"


# ------------------------------------------------------------------ 命令模板
def test_render_quotes_values():
    cmd = shell.render("kubectl exec -n {namespace} {pod} -- sh -c {cmd}", namespace="ns", pod="p-1",
                       cmd="cat '/a b.txt' && echo $HOME")
    assert cmd.startswith("kubectl exec -n ns p-1 -- sh -c ")
    r = shell.run("printf '%s' " + shell.render("{x}", x="a b'c"))
    assert r.stdout == "a b'c"
    assert shell.render("{unknown} {a}", a="1") == "{unknown} 1"


def test_filter_pods():
    pods = [{"name": n} for n in ["agent-0", "agent-worker-1", "gateway", "agent-canary"]]
    names = lambda f: [p["name"] for p in filter_pods(pods, f)]  # noqa: E731
    assert names("agent") == ["agent-0", "agent-worker-1", "agent-canary"]
    assert names("agent !canary") == ["agent-0", "agent-worker-1"]
    assert names("worker, gateway") == ["agent-worker-1", "gateway"]
    assert names("") == [p["name"] for p in pods]


# ------------------------------------------------------------------ conversation-id
def test_extract_conversation_id_rules():
    rules = ["header:x-conversation-id", "json:data.cid", r'regex:conv=([\w-]+)']
    assert extract_conversation_id(rules, {"X-Conversation-Id": "h1"}, [], "") == "h1"
    assert extract_conversation_id(rules, {}, [{"data": {"cid": "j1"}}], "") == "j1"
    assert extract_conversation_id(rules, {}, [], "xx conv=r-1 yy") == "r-1"
    assert extract_conversation_id(rules, {}, [], "nothing") is None


def test_fill_template_keeps_types():
    body = fill_template({"q": "{question}", "meta": {"t": "Q: {question}"}, "n": 1}, {"question": "hi"})
    assert body == {"q": "hi", "meta": {"t": "Q: hi"}, "n": 1}


def test_find_conversation_id_and_ts():
    assert find_conversation_id({"metadata": {"conversation_id": "c1"}}, ["metadata.conversation_id"]) == "c1"
    assert find_conversation_id({"extra": {"conversationId": "c2"}}, []) == "c2"
    assert find_conversation_id({"messages": [{"conversation_id": "no"}]}, []) is None
    assert parse_ts(1700000000000) == 1700000000.0
    assert abs(parse_ts("2024-01-01T00:00:00Z") - 1704067200) < 1


def test_suspect_error_heuristic():
    from app.services.trace import _looks_error
    assert _looks_error("Error: command timed out after 30s")
    assert _looks_error("bash: kubectl: command not found")
    assert _looks_error('{"stdout": "", "exit_code": 2}')
    assert not _looks_error("java.lang.OutOfMemoryError: Java heap space")
    assert not _looks_error("Events:\n  Warning BackOff kubelet Back-off restarting failed container")
    assert not _looks_error('{"error_rate": 0.03}')


# ------------------------------------------------------------------ 步骤解析
SKILL_LIST = """---
name: demo
---
# Demo

## 步骤

1. 查看 Pod：执行 `kubectl get pods -n prod`
2. 查看日志：执行 `kubectl logs mypod`
   - 如果重启过加 `--previous`
3. 给出结论

## 注意

1. 不要删除
2. 不要重启
"""

SKILL_HEADINGS = """# X
## 第一步 查询指标
```
curl -s http://monitor/api/latency
```
## 第二步 查看日志
```
kubectl logs deploy/svc | grep slow
```
## 附录
"""


def test_parse_steps_ordered_list():
    info = parse_steps(SKILL_LIST)
    assert info["strategy"] == "ordered-list"
    assert [s["title"][:4] for s in info["steps"]] == ["查看 P", "查看日志", "给出结论"]
    assert "kubectl logs mypod" in info["steps"][1]["commands"]
    assert info["meta"]["name"] == "demo"


def test_parse_steps_headings():
    info = parse_steps(SKILL_HEADINGS)
    assert info["strategy"] == "heading"
    assert len(info["steps"]) == 2
    assert info["steps"][0]["commands"] == ["curl -s http://monitor/api/latency"]


# ------------------------------------------------------------------ 轨迹归一化
def _openai_rows():
    base = 1700000000.0
    sys_msg = {"role": "system", "content": "skill demo"}
    user = {"role": "user", "content": "pod 挂了"}
    a1 = {"role": "assistant", "content": None, "tool_calls": [
        {"id": "c1", "type": "function", "function": {"name": "bash", "arguments": json.dumps({"command": "kubectl get pods -n prod"})}}]}
    t1 = {"role": "tool", "tool_call_id": "c1", "content": "pod-a CrashLoopBackOff"}
    a2 = {"role": "assistant", "content": None, "tool_calls": [
        {"id": "c2", "type": "function", "function": {"name": "bash", "arguments": json.dumps({"command": "kubectl logs pod-a"})}}]}
    t2 = {"role": "tool", "tool_call_id": "c2", "content": "Error: timeout"}
    recs = [
        {"timestamp": base, "latency_ms": 1000, "request": {"model": "m", "messages": [sys_msg, user]},
         "response": {"choices": [{"message": a1, "finish_reason": "tool_calls"}], "usage": {"prompt_tokens": 100, "completion_tokens": 10}}},
        {"timestamp": base + 3, "latency_ms": 1000, "request": {"model": "m", "messages": [sys_msg, user, a1, t1]},
         "response": {"choices": [{"message": a2}], "usage": {"prompt_tokens": 150, "completion_tokens": 10,
                                                             "prompt_tokens_details": {"cached_tokens": 100}}}},
        {"timestamp": base + 6, "latency_ms": 2000, "request": {"model": "m", "messages": [sys_msg, user, a1, t1, a2, t2]},
         "response": {"choices": [{"message": {"role": "assistant", "content": "结论：OOM"}}], "usage": {"prompt_tokens": 200, "completion_tokens": 30}}},
    ]
    return [{"id": i + 1, "pod": "p", "file": "f", "line_no": i + 1, "ts": r["timestamp"], "record": r} for i, r in enumerate(recs)]


def test_trace_openai_format():
    tr = TraceBuilder(DEFAULTS["analysis"]).build(_openai_rows())
    assert len(tr["llm_calls"]) == 3
    assert [t["input_summary"] for t in tr["tools"]] == ["kubectl get pods -n prod", "kubectl logs pod-a"]
    t1, t2 = tr["tools"]
    assert t1["type"] == "CLI" and t1["output"] == "pod-a CrashLoopBackOff" and t1["consumed_by"] == 2
    assert abs(t1["duration_ms"] - 2000) < 1  # LLM1 结束(+1s) -> LLM2 开始(+3s)
    assert t2["suspect_error"] and not t2["is_error"]
    assert tr["llm_calls"][1]["usage"]["cached"] == 100
    assert tr["final_answer"] == "结论：OOM"


def test_trace_anthropic_format():
    rec1 = {"start_time": "2024-01-01T00:00:00Z", "duration_ms": 500,
            "request": {"model": "claude", "system": "sys", "messages": [{"role": "user", "content": "hi"}]},
            "response": {"content": [{"type": "text", "text": "ok"},
                                     {"type": "tool_use", "id": "tu1", "name": "http_request", "input": {"method": "get", "url": "http://x/api"}}],
                         "stop_reason": "tool_use", "usage": {"input_tokens": 20, "output_tokens": 5}}}
    rec2 = {"start_time": "2024-01-01T00:00:02Z", "duration_ms": 500,
            "request": {"model": "claude", "messages": [
                {"role": "user", "content": "hi"},
                {"role": "assistant", "content": rec1["response"]["content"]},
                {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "tu1", "content": "200 OK", "is_error": True}]}]},
            "response": {"content": [{"type": "text", "text": "done"}], "usage": {"input_tokens": 40, "output_tokens": 3}}}
    rows = [{"id": i, "pod": "p", "file": "f", "line_no": i, "ts": None, "record": r} for i, r in enumerate([rec1, rec2])]
    tr = TraceBuilder(DEFAULTS["analysis"]).build(rows)
    assert tr["llm_calls"][0]["system_prompt"] == "sys"
    (t,) = tr["tools"]
    assert t["type"] == "API" and t["input_summary"] == "GET http://x/api" and t["is_error"]
    assert abs(t["duration_ms"] - 1500) < 1


def test_align_detects_missing_and_answer_step():
    steps = parse_steps(SKILL_LIST)["steps"]
    tr = TraceBuilder(DEFAULTS["analysis"]).build(_openai_rows())
    res = align(steps, tr["tools"], final_answer=tr["final_answer"])
    status = {s["id"]: s["status"] for s in res["steps"]}
    assert status == {"S1": "done", "S2": "done", "S3": "done"}
    res2 = align(steps, tr["tools"][:1], final_answer="")
    status2 = {s["id"]: s["status"] for s in res2["steps"]}
    assert status2 == {"S1": "done", "S2": "missing", "S3": "missing"}
    assert res2["scores"]["steps_missing"] == 2


def test_align_out_of_order():
    steps = parse_steps(SKILL_LIST)["steps"]
    tools = TraceBuilder(DEFAULTS["analysis"]).build(_openai_rows())["tools"]
    tools = [dict(tools[1], index=1), dict(tools[0], index=2)]
    res = align(steps, tools)
    st = {s["id"]: s["status"] for s in res["steps"]}
    assert "out_of_order" in (st["S1"], st["S2"])


# ------------------------------------------------------------------ 版本管理 & 配置
def test_versions_and_restore(tmp_path, monkeypatch):
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text("data_dir: ./data\nskills:\n  root: ./skills\n", encoding="utf-8")
    monkeypatch.setenv("LLM_MODEL", "from-env")
    cfg = Config(cfg_file)
    assert cfg["llm"]["model"] == "from-env"
    db = DB(cfg.data_dir / "t.db")
    store = SkillStore(cfg, db)
    store.create_skill("demo")
    assert store.latest_version("demo")["seq"] == 1
    store.apply_changes("demo", {"zh": {"SKILL.md": "v2 内容", "ref/a.md": "附录"}})
    assert store.create_version("demo", "改")["seq"] == 2
    assert store.create_version("demo", "无变化") is None
    diff = store.diff("demo", 1, 2)
    assert {(d["lang"], d["path"], d["status"]) for d in diff} == {("zh", "SKILL.md", "modified"), ("zh", "ref/a.md", "added")}
    store.restore("demo", 1, ["zh"])
    assert not (store.lang_dir("demo", "zh") / "ref/a.md").exists()
    assert store.latest_version("demo")["seq"] == 3
    assert store.list_versions("demo")[0]["source"] == "rollback"


def test_endpoint_defaults_filled(tmp_path):
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text("chat:\n  endpoints:\n    - name: x\n      url: http://a\n", encoding="utf-8")
    os.environ.pop("SKILL_LAB_CONFIG", None)
    cfg = Config(cfg_file)
    ep = cfg["chat"]["endpoints"][0]
    assert ep["url"] == "http://a" and ep["conversation_id_rules"]
