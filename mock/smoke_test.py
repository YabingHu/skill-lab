"""端到端冒烟测试：在 mock 环境下走一遍「改中文 → LLM 同步英文 → 发布 → 注入故障提问 → 拉日志 → 分析」。

先运行 bash scripts/dev_mock.sh --reset，再执行：python3 mock/smoke_test.py
"""
import json
import sys
import time
import urllib.error
import urllib.request

B = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8600/api"
S = "k8s-pod-troubleshoot"


def call(path, body=None, method=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(B + path, data=data, headers={"Content-Type": "application/json"},
                                 method=method or ("POST" if data is not None else "GET"))
    try:
        return json.load(urllib.request.urlopen(req))
    except urllib.error.HTTPError as e:
        raise SystemExit(f"{path} -> {e.code}: {e.read().decode()}")


def wait(job_id):
    while True:
        d = call(f"/jobs/{job_id}")
        if d["status"] != "running":
            return d
        time.sleep(0.5)


def check(cond, msg):
    print(("✓ " if cond else "✗ ") + msg)
    if not cond:
        raise SystemExit(1)


detail = call(f"/skills/{S}")
content = call(f"/skills/{S}/file?lang=zh&path=SKILL.md")["content"]
if "检查最近变更" not in content:
    content = content.replace("6. 给出结论", "6. 检查最近变更：执行 `kubectl rollout history deploy/<deploy> -n <namespace>`，确认是否有新发布。\n7. 给出结论")
    r = call(f"/skills/{S}/save", {"changes": {"zh": {"SKILL.md": content}}, "message": "增加检查最近变更步骤"})
    check(r["sync_pending"]["items"], f"保存中文生成 v{r['version']}，英文待同步 {len(r['sync_pending']['items'])} 个文件")

    job = wait(call(f"/skills/{S}/sync/propose", {})["job_id"])
    items = job["result"]["items"]
    check(job["status"] == "success" and items and items[0]["hunks"], f"LLM 生成英文修改建议 {len(items[0]['hunks'])} 处")
    applied = call(f"/skills/{S}/sync/apply", {"items": [{**it, "selected": [h["id"] for h in it["hunks"]]} for it in items]})
    check(applied["ok"], f"应用英文修改，生成 v{applied['version']}")

d = wait(call("/deploy", {"skill": S, "pod_filter": "agent", "langs": ["zh", "en"]})["job_id"])
check(d["status"] == "success", "发布到包含 agent 的 pod：" + d["logs"][-1])

r1 = call("/chat/ask", {"endpoint": "mock-agent", "question": "order-svc 一直重启，帮我看看",
                         "faults": ["tool_timeout"], "headers": [{"key": "x-user", "value": "tester"}]})
check(bool(r1["conversation_id"]), f"注入 tool_timeout 提问，conversation-id={r1['conversation_id']}")
r2 = call("/chat/ask", {"endpoint": "mock-agent-stream", "question": "order-svc 一直重启", "faults": ["skip_step"]})
check(bool(r2["conversation_id"]) and bool(r2["answer"]), f"SSE 流式提问，conversation-id={r2['conversation_id']}")

d = wait(call("/logs/pull", {})["job_id"])
check(d["status"] == "success", "拉取日志：" + d["logs"][-1])

import urllib.parse
raw = urllib.request.urlopen(B + "/conversations/" + urllib.parse.quote(r1["conversation_id"]) + "/download").read().decode()
check(raw.count("\n") >= 5 and all(r1["conversation_id"] in l for l in raw.splitlines()),
      f"下载会话 jsonl：{raw.count(chr(10))} 行")
files = call("/logs/files")
z = urllib.request.urlopen(B + "/logs/files/archive").read()
check(z[:2] == b"PK" and files, f"打包下载全部原始文件：{len(files)} 个文件，{len(z)} 字节")

for r, label in ((r1, "tool_timeout"), (r2, "skip_step")):
    a = call(f"/analysis/{r['conversation_id']}")
    sc = a["alignment"]["scores"]
    check(a["skill"] == S and a["metrics"]["tool_calls"] > 0,
          f"[{label}] 分析：skill={a['skill']} v{a['version']}（{a['version_source']}），LLM 调用 {a['metrics']['llm_calls']}，"
          f"工具调用 {a['metrics']['tool_calls']}，tokens {a['metrics']['usage']['total']}，遵从度 {sc['compliance']}，"
          f"步骤 {sc['steps_done']}/{sc['steps_total']}，缺失 {sc['steps_missing']}")
    for s in a["alignment"]["steps"]:
        print(f"    {s['id']} {s['status']:<13} calls={s['calls']} {s['title'][:40]}")

rv = call(f"/analysis/{r1['conversation_id']}/review", {})
check("score" in rv, f"LLM 评审：score={rv['score']} {rv['summary']}")
print("全部通过")
