"""业务流程测试：用 MOCK 数据把各条流程跑通。

每次运行都会在临时目录里搭一套隔离环境，不依赖、也不影响 data/ 目录和外部服务：
- mock/bin/kubectl：用本地目录模拟 pod（list / exec / cp）
- mock/agent_server.py：模拟环境问答接口（写 llm-calls.jsonl）和 OpenAI 兼容的本地 LLM
- 后端通过 FastAPI TestClient 调真实 HTTP API

运行：cd backend && python3 -m pytest -q tests/test_flows.py
"""
from __future__ import annotations

import importlib.util
import io
import json
import os
import shutil
import socket
import sys
import threading
import time
import zipfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SKILL = "k8s-pod-troubleshoot"
SKILL2 = "service-latency-check"
PODS = ["agent-a-0", "agent-a-1", "agent-b-0", "gateway-0"]

CONFIG_TEMPLATE = """
data_dir: ./data
skills:
  root: ./workspace/skills
llm:
  base_url: http://127.0.0.1:@PORT@/v1
  model: mock-llm
env:
  namespace: test
  commands:
    env_info: "bash @REPO@/scripts/env_info.sh {namespace}"
deploy:
  default_pod_filter: agent
  package_cmd: "bash @REPO@/scripts/package_skill.sh {staging} {out}"
  post_cmd: "mkdir -p /tmp && echo reloaded-{skill}-v{version} >> /tmp/reload.log"
chat:
  endpoints:
    - name: hdr
      url: http://127.0.0.1:@PORT@/api/chat
      body: {query: "{question}", stream: false}
    - name: sse
      url: http://127.0.0.1:@PORT@/api/chat
      body: {query: "{question}", stream: true}
    - name: sent
      url: http://127.0.0.1:@PORT@/api/chat
      body: {query: "{question}"}
      send_conversation_id_header: x-conversation-id
    - name: json_only
      url: http://127.0.0.1:@PORT@/api/chat
      body: {query: "{question}"}
      conversation_id_rules: ["json:conversation_id"]
    - name: regex_only
      url: http://127.0.0.1:@PORT@/api/chat
      body: {query: "{question}"}
      conversation_id_rules: ['regex:"conversation_id":\\s*"([\\w-]+)"']
    - name: none
      url: http://127.0.0.1:@PORT@/api/chat
      body: {query: "{question}"}
      conversation_id_rules: ["header:x-not-exist"]
    - name: down
      url: http://127.0.0.1:1/api/chat
      timeout: 5
  faults:
    - {name: tool_timeout, label: 工具超时, headers: {x-fault-inject: tool_timeout}}
    - {name: skip_step, label: 跳步, headers: {x-fault-inject: skip_step}}
    - name: pod_side
      label: pod 内注入
      headers: {x-fault-inject: tool_error}
      body: {debug: true}
      pod_filter: agent-b
      pod_cmd: "mkdir -p /tmp && echo on >> /tmp/fault.log"
      cleanup_cmd: "echo off >> /tmp/fault.log"
logs:
  pod_filter: agent
  paths: [/var/log/agent]
"""


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Lab:
    def __init__(self, client, root: Path, mock_root: Path):
        self.c = client
        self.root = root
        self.mock_root = mock_root

    # ---------------------------------------------------------------- HTTP
    def get(self, url, status=200, **kw):
        r = self.c.get("/api" + url, **kw)
        assert r.status_code == status, f"GET {url} -> {r.status_code}: {r.text[:500]}"
        return r.json() if "json" in r.headers.get("content-type", "") else r

    def post(self, url, body=None, status=200, **kw):
        r = self.c.post("/api" + url, json=body if body is not None else {}, **kw)
        assert r.status_code == status, f"POST {url} -> {r.status_code}: {r.text[:500]}"
        return r.json()

    def wait(self, job_id, timeout=60):
        t0 = time.time()
        while time.time() - t0 < timeout:
            j = self.get(f"/jobs/{job_id}")
            if j["status"] != "running":
                return j
            time.sleep(0.1)
        raise AssertionError(f"job {job_id} 超时")

    def job(self, url, body=None):
        return self.wait(self.post(url, body)["job_id"])

    # ---------------------------------------------------------------- 文件
    def pod_file(self, pod, path) -> Path:
        return self.mock_root / "pods" / pod / path.lstrip("/")

    def workspace(self, lang, skill, rel="SKILL.md") -> Path:
        return self.root / "workspace" / "skills" / lang / skill / rel

    def save(self, skill, changes, message="test"):
        return self.post(f"/skills/{skill}/save", {"changes": changes, "message": message})

    def read(self, skill, lang="zh", path="SKILL.md", ref=None):
        q = f"?lang={lang}&path={path}" + (f"&ref={ref}" if ref else "")
        return self.get(f"/skills/{skill}/file{q}")["content"]

    def ask(self, endpoint="hdr", question="order-svc 一直重启", pod="agent-a-0", faults=None, headers=None, **kw):
        hs = [{"key": "x-mock-pod", "value": pod}] + (headers or [])
        return self.post("/chat/ask", {"endpoint": endpoint, "question": question, "faults": faults or [],
                                       "headers": hs, **kw})

    def deploy(self, skill, pods=None, pod_filter="", langs=None, version=None):
        r = self.post("/deploy", {"skill": skill, "pods": pods, "pod_filter": pod_filter,
                                  "langs": langs or ["zh", "en"], "version": version})
        return r, self.wait(r["job_id"])


@pytest.fixture(scope="module")
def lab(tmp_path_factory):
    import uvicorn

    root = tmp_path_factory.mktemp("lab")
    mock_root = root / "mock"
    for p in PODS:
        (mock_root / "pods" / p).mkdir(parents=True)
    shutil.copytree(REPO / "mock" / "demo_skills", root / "workspace" / "skills")
    port = _free_port()
    (root / "config.yaml").write_text(
        CONFIG_TEMPLATE.replace("@PORT@", str(port)).replace("@REPO@", str(REPO)), encoding="utf-8")

    saved = {k: os.environ.get(k) for k in ("SKILL_LAB_CONFIG", "MOCK_ROOT", "PATH", "LLM_BASE_URL",
                                            "LLM_API_KEY", "LLM_MODEL")}
    os.environ.update({
        "SKILL_LAB_CONFIG": str(root / "config.yaml"),
        "MOCK_ROOT": str(mock_root),
        "PATH": f"{REPO / 'mock' / 'bin'}{os.pathsep}{os.environ.get('PATH', '')}",
        "LLM_BASE_URL": f"http://127.0.0.1:{port}/v1", "LLM_API_KEY": "mock-key", "LLM_MODEL": "mock-llm",
    })

    # 启动 mock 环境服务（问答 + LLM）
    spec = importlib.util.spec_from_file_location("mock_agent_server", REPO / "mock" / "agent_server.py")
    mock_mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mock_mod)
    server = uvicorn.Server(uvicorn.Config(mock_mod.app, host="127.0.0.1", port=port, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    assert server.started, "mock 服务启动失败"

    from fastapi.testclient import TestClient
    from app import config as app_config
    app_config.set_config(None)  # type: ignore[arg-type]
    for name in [m for m in sys.modules if m == "app.main"]:
        del sys.modules[name]
    import app.main as main

    with TestClient(main.app) as client:
        yield Lab(client, root, mock_root)

    server.should_exit = True
    for k, v in saved.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


# ======================================================================== 基础
def test_config_llm_and_env(lab):
    cfg = lab.get("/config")
    assert cfg["llm"]["api_key"] == "***", "前端不应看到 API Key"
    assert cfg["llm"]["model"] == "mock-llm"
    assert lab.get("/llm/ping")["ok"] is True

    names = lambda f: [p["name"] for p in lab.get(f"/env/pods?filter={f}")["pods"]]  # noqa: E731
    assert names("agent") == ["agent-a-0", "agent-a-1", "agent-b-0"]
    assert names("agent !a-1") == ["agent-a-0", "agent-b-0"]
    assert names("gateway, b-0") == ["agent-b-0", "gateway-0"]
    assert names("nothing") == []

    r = lab.post("/env/exec", {"pod": "agent-a-0", "cmd": "echo hello && ls /opt 2>/dev/null; true"})
    assert r["rc"] == 0 and "hello" in r["stdout"]
    assert lab.post("/env/exec", {"pod": "ghost", "cmd": "echo x"})["rc"] != 0

    info = lab.get("/env/info")
    assert info["ok"] and "agent-a-0" in info["output"]
    assert lab.post("/config/reload")["env"]["namespace"] == "test"


# ======================================================================== skill 编辑与多版本
def test_skill_edit_versions_and_rollback(lab):
    s = "flow-crud"
    lab.post("/skills", {"name": s, "description": "流程测试"})
    lab.post("/skills", {"name": s}, status=400)                       # 重名
    lab.post("/skills", {"name": "../evil"}, status=400)               # 非法名称
    d = lab.get(f"/skills/{s}")
    assert d["latest_seq"] == 1 and d["dirty"] is False and d["sync_pending"]["items"] == []
    assert {f["path"] for f in d["tree"]["zh"]} == {"SKILL.md"}

    v1_zh = lab.read(s)
    r = lab.save(s, {"zh": {"SKILL.md": v1_zh + "\n3. 第三步\n", "references/cmds.md": "kubectl get pods\n"}}, "加第三步")
    assert r["version"] == 2 and r["changed"] == 2
    assert lab.save(s, {"zh": {"SKILL.md": v1_zh + "\n3. 第三步\n"}})["version"] is None  # 内容没变不产生版本

    versions = lab.get(f"/skills/{s}/versions")
    assert [v["seq"] for v in versions] == [2, 1]
    assert versions[0]["changes"]["zh"] == {"added": 1, "deleted": 0, "modified": 1}
    assert versions[0]["message"] == "加第三步"

    diff = {(x["lang"], x["path"]): x for x in lab.get(f"/skills/{s}/diff?a=1&b=2")}
    assert set(diff) == {("zh", "SKILL.md"), ("zh", "references/cmds.md")}
    assert diff[("zh", "SKILL.md")]["additions"] == 2 and diff[("zh", "references/cmds.md")]["status"] == "added"
    assert lab.read(s, ref=1) == v1_zh                                # 读历史版本

    # 只回退中文到 v1：新文件被删除，英文不受影响，生成 v3
    en_before = lab.read(s, "en")
    assert lab.post(f"/skills/{s}/restore", {"seq": 1, "langs": ["zh"]})["version"] == 3
    assert lab.read(s) == v1_zh and lab.read(s, "en") == en_before
    assert not lab.workspace("zh", s, "references/cmds.md").exists()
    assert lab.get(f"/skills/{s}/versions")[0]["source"] == "rollback"
    # 再回到 v2（回退本身也可以回退）
    lab.post(f"/skills/{s}/restore", {"seq": 2})
    assert lab.workspace("zh", s, "references/cmds.md").exists()

    # 删除文件 / 非法路径
    lab.save(s, {"zh": {"references/cmds.md": None}}, "删除")
    assert not lab.workspace("zh", s, "references").exists(), "空目录应被清理"
    lab.get(f"/skills/{s}/file?lang=zh&path=../../config.yaml", status=400)
    r = lab.c.post(f"/api/skills/{s}/save", json={"changes": {"zh": {"../../escape.md": "x"}}})
    assert r.status_code == 400 and not (lab.root / "workspace" / "escape.md").exists()


# ======================================================================== LLM 同步英文
def test_llm_sync_with_hunk_confirmation(lab):
    s = "flow-sync"
    lab.post("/skills", {"name": s})
    zh = lab.read(s)
    lab.save(s, {"zh": {"SKILL.md": zh + "3. 检查监控\n", "notes.md": "备注一\n备注二\n"}}, "改中文")
    pending = {i["path"]: i["zh_status"] for i in lab.get(f"/skills/{s}")["sync_pending"]["items"]}
    assert pending == {"SKILL.md": "modified", "notes.md": "added"}

    job = lab.job(f"/skills/{s}/sync/propose", {})
    assert job["status"] == "success", job["logs"]
    items = {i["path"]: i for i in job["result"]["items"]}
    assert items["SKILL.md"]["action"] == "modify" and items["SKILL.md"]["hunks"]
    assert "3. 检查监控" in items["SKILL.md"]["zh_diff"]
    assert items["notes.md"]["action"] == "create" and items["notes.md"]["en_new"].startswith("[EN-mock]")

    # 人工确认：SKILL.md 只接受建议，notes.md 手动改写后再应用
    en_old = lab.read(s, "en")
    apply_items = [
        {**items["SKILL.md"], "selected": [h["id"] for h in items["SKILL.md"]["hunks"]]},
        {**items["notes.md"], "selected": [], "content": "Note one\nNote two\n"},
    ]
    r = lab.post(f"/skills/{s}/sync/apply", {"items": apply_items, "message": "同步英文"})
    assert r["ok"] and r["changed"] == 2
    en_new = lab.read(s, "en")
    assert en_new.startswith(en_old) and "[EN-mock] 3. 检查监控" in en_new
    assert lab.read(s, "en", "notes.md") == "Note one\nNote two\n"
    d = lab.get(f"/skills/{s}")
    assert d["sync_pending"]["items"] == [] and d["en_synced_seq"] == d["latest_seq"]

    # 不勾选任何块：英文不变，但仍可标记已对齐
    lab.save(s, {"zh": {"SKILL.md": lab.read(s) + "4. 不需要同步的改动\n"}})
    job = lab.job(f"/skills/{s}/sync/propose", {})
    items = job["result"]["items"]
    r = lab.post(f"/skills/{s}/sync/apply", {"items": [{**i, "selected": []} for i in items]})
    assert r["ok"] and r["changed"] == 0 and lab.read(s, "en") == en_new
    assert lab.get(f"/skills/{s}")["sync_pending"]["items"] == []

    # 只勾选部分修改块：人工在建议上又改了一处，两处修改中只接受后一处
    from app.services.sync import compute_hunks
    cur_en = lab.read(s, "en")
    lines = cur_en.splitlines(keepends=True)
    edited = "".join(["# CHANGED TITLE\n" if ln.startswith("# ") else ln for ln in lines]) + "6. extra line\n"
    hunks = compute_hunks(cur_en, edited)
    assert len(hunks) == 2
    import hashlib
    item = {"path": "SKILL.md", "action": "modify", "en_old": cur_en, "en_new": edited,
            "en_old_digest": hashlib.sha1(cur_en.encode()).hexdigest(), "selected": [hunks[1]["id"]]}
    assert lab.post(f"/skills/{s}/sync/apply", {"items": [item]})["changed"] == 1
    partial = lab.read(s, "en")
    assert partial.endswith("6. extra line\n") and "# CHANGED TITLE" not in partial
    en_new = partial

    # 冲突检测：生成建议后英文被别人改了，应用时拒绝
    lab.save(s, {"zh": {"SKILL.md": lab.read(s) + "5. 冲突测试\n"}})
    items = lab.job(f"/skills/{s}/sync/propose", {})["result"]["items"]
    lab.save(s, {"en": {"SKILL.md": en_new + "edited by someone\n"}})
    r = lab.post(f"/skills/{s}/sync/apply", {"items": [{**i, "selected": [h["id"] for h in i["hunks"]]} for i in items]})
    assert r["ok"] is False and r["conflicts"] == ["SKILL.md"]
    assert lab.post(f"/skills/{s}/sync/mark")["en_synced_seq"] == lab.get(f"/skills/{s}")["latest_seq"]

    # 中文删除文件 -> 建议删除英文
    lab.save(s, {"zh": {"notes.md": None}})
    items = {i["path"]: i for i in lab.job(f"/skills/{s}/sync/propose", {})["result"]["items"]}
    assert items["notes.md"]["action"] == "delete"
    lab.post(f"/skills/{s}/sync/apply", {"items": [{**items["notes.md"], "accept": True}]})
    assert not lab.workspace("en", s, "notes.md").exists()

    # 全量检查模式：对所有中文文件逐个核对
    full = lab.job(f"/skills/{s}/sync/propose", {"mode": "full"})["result"]
    assert full["mode"] == "full" and [i["zh_status"] for i in full["items"]] == ["check"]


# ======================================================================== 发布
def test_deploy_to_pods_by_substring(lab):
    r, job = lab.deploy(SKILL, pod_filter="agent-a")
    assert job["status"] == "success", job["logs"]
    seq = r["version"]
    for pod in ("agent-a-0", "agent-a-1"):
        for lang in ("zh", "en"):
            deployed = lab.pod_file(pod, f"/opt/agent/skills/{lang}/{SKILL}/SKILL.md")
            assert deployed.read_text(encoding="utf-8") == lab.workspace(lang, SKILL).read_text(encoding="utf-8")
        assert f"reloaded-{SKILL}-v{seq}" in lab.pod_file(pod, "/tmp/reload.log").read_text()
        assert not list(lab.pod_file(pod, "/tmp").glob("*.tar.gz")), "安装包应被清理"
    assert not lab.pod_file("agent-b-0", f"/opt/agent/skills/zh/{SKILL}").exists(), "未匹配的 pod 不应发布"
    assert any("packaged" in line for line in job["logs"]), "应使用配置的打包脚本"

    hist = lab.get(f"/deployments?skill={SKILL}")[0]
    assert hist["status"] == "success" and hist["version_seq"] == seq and len(hist["pods"]) == 2

    # 只发布中文
    _, job = lab.deploy(SKILL2, pods=["agent-b-0"], langs=["zh"])
    assert job["status"] == "success"
    assert lab.pod_file("agent-b-0", f"/opt/agent/skills/zh/{SKILL2}/SKILL.md").exists()
    assert not lab.pod_file("agent-b-0", f"/opt/agent/skills/en/{SKILL2}").exists()

    # 部分失败 / 全部失败 / 无匹配
    _, job = lab.deploy(SKILL, pods=["agent-a-0", "ghost-pod"])
    assert lab.get(f"/deployments?skill={SKILL}")[0]["status"] == "partial"
    assert any("ghost-pod" in line and "✗" in line for line in job["logs"])
    _, job = lab.deploy(SKILL, pods=["ghost-pod"])
    assert job["status"] == "failed"
    lab.post("/deploy", {"skill": SKILL, "pod_filter": "no-such-pod"}, status=400)

    # 工作区有未保存修改时发布「当前工作区」：自动存为新版本
    lab.workspace("zh", SKILL2).write_text(lab.workspace("zh", SKILL2).read_text(encoding="utf-8") + "\n<!-- wip -->\n",
                                           encoding="utf-8")
    before = lab.get(f"/skills/{SKILL2}/versions")[0]["seq"]
    r, job = lab.deploy(SKILL2, pods=["agent-b-0"])
    assert job["status"] == "success" and r["version"] == before + 1
    assert "wip" in lab.pod_file("agent-b-0", f"/opt/agent/skills/zh/{SKILL2}/SKILL.md").read_text(encoding="utf-8")


# ======================================================================== 从环境拉取 skill
def test_pull_skill_from_environment(lab):
    _, job = lab.deploy(SKILL2, pods=["agent-b-0"])
    assert job["status"] == "success"
    # 一致
    job = lab.job("/skills/env-pull", {"pod": "agent-b-0", "skills": [SKILL2]})
    assert job["status"] == "success" and [s["status"] for s in job["result"]["skills"]] == ["same"]

    # 环境上被人改了，还多了一个本地没有的 skill
    pod_md = lab.pod_file("agent-b-0", f"/opt/agent/skills/zh/{SKILL2}/SKILL.md")
    pod_md.write_text(pod_md.read_text(encoding="utf-8") + "\n## 第五步 环境上加的\n", encoding="utf-8")
    extra = lab.pod_file("agent-b-0", "/opt/agent/skills/zh/env-only-skill/SKILL.md")
    extra.parent.mkdir(parents=True)
    extra.write_text("# 环境独有\n1. 执行 `ls`\n", encoding="utf-8")

    job = lab.job("/skills/env-pull", {"pod": "agent-b-0", "skills": [SKILL2]})
    (cur,) = job["result"]["skills"]
    assert cur["status"] == "changed"
    assert [(f["lang"], f["path"], f["status"]) for f in cur["files"]] == [("zh", "SKILL.md", "modified")]

    job = lab.job("/skills/env-pull", {"pod": "agent-b-0"})       # 全部
    res = {s["name"]: s for s in job["result"]["skills"]}
    assert res["env-only-skill"]["status"] == "new" and res[SKILL2]["status"] == "changed"

    # 本地有未保存的修改：导入前必须先自动存成版本，不能丢
    lab.workspace("zh", SKILL2).write_text(lab.read(SKILL2) + "\n<!-- 本地未保存的修改 -->\n", encoding="utf-8")
    local_before = lab.read(SKILL2)
    r = lab.post("/skills/env-pull/apply", {"pull_id": job["result"]["pull_id"], "skills": [SKILL2, "env-only-skill"]})
    assert {x["skill"] for x in r["imported"]} == {SKILL2, "env-only-skill"}
    assert "环境上加的" in lab.read(SKILL2)
    assert lab.read("env-only-skill").startswith("# 环境独有")
    assert lab.get(f"/skills/{SKILL2}/versions")[0]["source"] == "env-pull"

    assert "本地未保存的修改" not in lab.read(SKILL2)
    # 导入后可以回退到导入前（包括导入前未保存的修改）
    vs = lab.get(f"/skills/{SKILL2}/versions")
    assert vs[1]["message"] == "从环境拉取前自动保存"
    prev = vs[1]["seq"]
    lab.post(f"/skills/{SKILL2}/restore", {"seq": prev})
    assert lab.read(SKILL2) == local_before

    # 异常：pod 不存在 / skill 不存在 / 拉取结果过期
    assert lab.job("/skills/env-pull", {"pod": "ghost-pod"})["status"] == "failed"
    assert lab.job("/skills/env-pull", {"pod": "agent-b-0", "skills": ["no-such-skill"]})["status"] == "failed"
    lab.post("/skills/env-pull/apply", {"pull_id": "expired", "skills": [SKILL2]}, status=400)
    lab.post("/skills/env-pull", {}, status=400)


# ======================================================================== 问答调试
def test_chat_headers_faults_and_conversation_id(lab):
    pv = lab.post("/chat/preview", {"endpoint": "hdr", "question": "你好", "faults": ["tool_timeout", "pod_side"],
                                     "headers": [{"key": "x-user", "value": "tester"}]})["request"]
    assert pv["headers"]["x-user"] == "tester" and pv["headers"]["Content-Type"] == "application/json"
    assert pv["headers"]["x-fault-inject"] == "tool_error"           # 后面的故障覆盖同名 header
    assert pv["body"] == {"query": "你好", "stream": False, "debug": True}

    r = lab.ask(faults=["tool_timeout"], headers=[{"key": "x-user", "value": "tester"}])
    assert r["status_code"] == 200 and r["error"] is None
    assert r["conversation_id"] and r["conversation_id"] == r["response_headers"]["x-conversation-id"]
    assert r["request"]["headers"]["x-user"] == "tester" and r["faults"] == ["tool_timeout"]
    assert "OOM" in r["answer"]

    sse = lab.ask("sse")
    assert sse["conversation_id"].startswith("conv-") and "OOM" in sse["answer"]

    sent = lab.ask("sent")
    assert sent["conversation_id"] == sent["request"]["headers"]["x-conversation-id"]
    assert lab.ask("json_only")["conversation_id"].startswith("conv-")
    assert lab.ask("regex_only")["conversation_id"].startswith("conv-")

    none = lab.ask("none")
    assert none["conversation_id"] is None
    fixed = lab.c.patch(f"/api/chats/{none['id']}", json={"conversation_id": "manual-123", "note": "手动"}).json()
    assert fixed["conversation_id"] == "manual-123" and fixed["note"] == "手动"

    down = lab.ask("down")
    assert down["status_code"] is None and down["error"]

    # pod 内故障命令：提问前执行 pod_cmd，提问后执行 cleanup_cmd，只作用于 pod_filter 匹配的 pod
    r = lab.ask(pod="agent-b-0", faults=["pod_side"])
    assert r["status_code"] == 200
    assert lab.pod_file("agent-b-0", "/tmp/fault.log").read_text().split() == ["on", "off"]
    assert not lab.pod_file("agent-a-0", "/tmp/fault.log").exists()
    assert len(r["request"]["fault_logs"]) == 2

    hist = lab.get("/chats?q=manual-123")
    assert [c["id"] for c in hist] == [none["id"]]
    assert lab.c.delete(f"/api/chats/{none['id']}").status_code == 200
    lab.get(f"/chats/{none['id']}", status=404)


# ======================================================================== 日志拉取与下载
def test_pull_and_download_llm_calls(lab):
    c1 = lab.ask(pod="agent-a-0", faults=["tool_timeout"])["conversation_id"]
    c2 = lab.ask(pod="agent-a-1", faults=["skip_step"])["conversation_id"]

    job = lab.job("/logs/pull", {"pod_filter": "agent-a"})
    assert job["status"] == "success", job["logs"]
    assert job["result"]["new_records"] > 0 and {c1, c2} <= set(job["result"]["conversations"])
    assert lab.job("/logs/pull", {"pod_filter": "agent-a"})["result"]["new_records"] == 0, "重复拉取应去重"

    convs = {c["conversation_id"]: c for c in lab.get("/conversations")}
    assert convs[c1]["calls"] > 0 and convs[c1]["pods"] == ["agent-a-0"] and convs[c1]["faults"] == ["tool_timeout"]

    # 按 ID 拉取：只在 pod 内 grep 该 ID
    job = lab.job("/logs/pull", {"pod_filter": "agent-a", "conversation_id": c1})
    assert job["status"] == "success"
    files = lab.get("/logs/files")
    full = [f for f in files if not f["conversation_id"] and f["pod"] == "agent-a-0"]
    part = [f for f in files if f["conversation_id"] == c1]
    assert full and part and full[0]["remote"] == "/var/log/agent/llm-calls.jsonl"

    # 下载单个原始文件：与 pod 上的内容一致
    r = lab.c.get("/api/logs/files/download", params={"rel": full[0]["rel"]})
    assert r.status_code == 200
    assert r.content == lab.pod_file("agent-a-0", "/var/log/agent/llm-calls.jsonl").read_bytes()
    assert "attachment" in r.headers["content-disposition"]

    # 按会话下载：每行都包含该 ID，且不重复
    r = lab.c.get(f"/api/conversations/{c1}/download")
    lines = r.text.strip().split("\n")
    assert r.status_code == 200 and len(lines) == convs[c1]["calls"] and len(set(lines)) == len(lines)
    assert all(c1 in line for line in lines) and all(json.loads(line)["conversation_id"] == c1 for line in lines)
    assert f"llm-calls-{c1}.jsonl" in r.headers["content-disposition"]
    assert lab.c.get("/api/conversations/not-exist/download").status_code == 404

    # 打包下载全部
    r = lab.c.get("/api/logs/files/archive")
    assert r.status_code == 200 and r.headers["content-type"] == "application/zip"
    assert sorted(zipfile.ZipFile(io.BytesIO(r.content)).namelist()) == sorted(f["rel"] for f in lab.get("/logs/files"))

    # 越权路径
    assert lab.c.get("/api/logs/files/download", params={"rel": "../skill-lab.db"}).status_code == 400

    # 导入本地文件（含非 JSON 行）
    content = '{"conversation_id":"up-1","x":1}\nnot json\n{"conversation_id":"up-1","x":2}\n'
    r = lab.c.post("/api/logs/upload", files={"file": ("my.jsonl", content.encode())}).json()
    assert r == {"new_records": 2, "conversations": ["up-1"]}

    # 清除本地索引不影响原始文件下载
    assert lab.c.delete(f"/api/conversations/{c2}").status_code == 200
    assert c2 not in {c["conversation_id"] for c in lab.get("/conversations") if c["calls"]}
    assert lab.c.get(f"/api/conversations/{c2}/download").status_code == 200

    # 只提问、尚未拉日志的会话也在列表里
    c3 = lab.ask(pod="agent-b-0")["conversation_id"]
    assert {c["conversation_id"]: c["calls"] for c in lab.get("/conversations")}[c3] == 0


# ======================================================================== 执行分析（菜单已隐藏，接口保留）
def test_analysis_on_mock_logs(lab):
    _, job = lab.deploy(SKILL, pods=["agent-a-1"])
    assert job["status"] == "success"
    seq = lab.get(f"/deployments?skill={SKILL}")[0]["version_seq"]
    conv = lab.ask(pod="agent-a-1", faults=["tool_timeout"])["conversation_id"]
    skip = lab.ask(pod="agent-a-1", faults=["skip_step"])["conversation_id"]
    lab.job("/logs/pull", {"pod_filter": "agent-a-1"})

    a = lab.get(f"/analysis/{conv}")
    assert a["skill"] == SKILL and a["version"] == seq and a["version_source"] == "deployed"
    m = a["metrics"]
    # 工具调用 = 读 skill 1 次 + 每个带命令的步骤 1 次 + 注入超时后重试 1 次；LLM 调用比工具多一次（最终回答）
    cmd_steps = sum(1 for s in a["alignment"]["steps"] if s["commands"])
    assert m["tool_calls"] == 1 + cmd_steps + 1 and m["llm_calls"] == m["tool_calls"] + 1
    assert m["usage"]["total"] > 0 and m["total_ms"] > 0 and m["tool_suspect_errors"] == 1
    assert len(a["timeline"]) == m["llm_calls"] + m["tool_calls"]
    steps = {s["id"]: s for s in a["alignment"]["steps"]}
    assert all(s["status"] == "done" for s in steps.values())
    assert steps["S2"]["retries"] == 1 and steps["S2"]["suspect_errors"] == 1   # 注入的超时 + 重试
    assert a["alignment"]["scores"]["compliance"] == 100
    t3 = next(t for t in a["tools"] if t["index"] == 3)
    assert t3["type"] == "CLI" and t3["output"].startswith("Error") and t3["duration_ms"] >= 29000

    b = lab.get(f"/analysis/{skip}")
    assert {s["id"]: s["status"] for s in b["alignment"]["steps"]}["S3"] == "missing"
    assert b["alignment"]["scores"]["compliance"] < 100

    review = lab.post(f"/analysis/{conv}/review", {})
    assert "score" in review and review["suggestions"] and review["alignment"]["steps"]
    assert lab.get(f"/analysis/{conv}")["llm_review"]["summary"] == review["summary"]
    assert lab.get(f"/analysis/{conv}?lang=en")["lang"] == "en"


# ======================================================================== 任务列表
def test_jobs_are_recorded(lab):
    kinds = {j["kind"] for j in lab.get("/jobs")}
    assert {"deploy", "sync", "logs", "env-pull"} <= kinds
    lab.get("/jobs/not-exist", status=404)
