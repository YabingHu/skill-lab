"""Skill Lab 后端入口。

启动：uvicorn app.main:app --host 0.0.0.0 --port 8600   （在 backend/ 目录下）
前端构建产物（frontend/dist）存在时由本服务直接托管。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import Body, FastAPI, File, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from starlette.background import BackgroundTask
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from .config import PROJECT_ROOT, get_config
from .db import DB, loads
from .jobs import JobManager
from .services.analysis import AnalysisService
from .services.chat import ChatService
from .services.deploy import DeployService
from .services.env import EnvService
from .services.env_pull import EnvPullService
from .services.llm import LLMClient, LLMError
from .services.logs import LogService
from .services.skills import SkillError, SkillStore
from .services.sync import SyncService

cfg = get_config()
db = DB(cfg.data_dir / "skill-lab.db")
jobs = JobManager(db)
store = SkillStore(cfg, db)
llm = LLMClient(cfg)
sync = SyncService(store, llm)
env = EnvService(cfg)
deployer = DeployService(cfg, db, store, env)
env_pull = EnvPullService(cfg, store, env)
chat = ChatService(cfg, db, env)
logs = LogService(cfg, db, env)
analysis = AnalysisService(cfg, db, store, logs, deployer, llm)

app = FastAPI(title="Skill Lab", version="0.1.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


@app.exception_handler(SkillError)
async def _skill_error(_, exc: SkillError):
    return JSONResponse(status_code=400, content={"detail": str(exc)})


@app.exception_handler(LLMError)
async def _llm_error(_, exc: LLMError):
    return JSONResponse(status_code=502, content={"detail": str(exc)})


@app.exception_handler(ValueError)
async def _value_error(_, exc: ValueError):
    return JSONResponse(status_code=400, content={"detail": str(exc)})


# ====================================================================== 系统
@app.get("/api/config")
def get_cfg():
    return cfg.public()


@app.post("/api/config/reload")
def reload_cfg():
    cfg.reload()
    return cfg.public()


@app.get("/api/llm/ping")
def llm_ping():
    return llm.ping()


@app.get("/api/jobs")
def list_jobs(kind: str | None = None):
    return jobs.list(kind)


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str, offset: int = 0):
    j = jobs.get(job_id, offset)
    if not j:
        raise HTTPException(404, "任务不存在")
    return j


# ====================================================================== Skill
@app.get("/api/skills")
def list_skills():
    return store.list_skills()


@app.post("/api/skills")
def create_skill(body: dict = Body(...)):
    store.create_skill(body["name"].strip(), body.get("description", ""))
    return {"ok": True}


@app.post("/api/skills/env-pull")
def skills_env_pull(body: dict = Body(...)):
    """从环境 pod 拉取 skill：body {pod, namespace, skills?: [...], remote_dir?}，返回任务 id，结果里带差异"""
    pod = body.get("pod")
    if not pod:
        raise HTTPException(400, "请选择 pod")
    skills = body.get("skills") or None
    job = jobs.start("env-pull", f"从 {pod} 拉取 skill" + (f"（{', '.join(skills)}）" if skills else "（全部）"),
                     lambda j: env_pull.pull(j, pod, body.get("namespace"), skills, body.get("remote_dir", "")))
    return {"job_id": job.id}


@app.post("/api/skills/env-pull/apply")
def skills_env_pull_apply(body: dict = Body(...)):
    """body {pull_id, skills: [...], message?}：把选中的 skill 用环境内容替换工作区并生成新版本"""
    if not body.get("skills"):
        raise HTTPException(400, "请选择要导入的 skill")
    return env_pull.apply(body["pull_id"], body["skills"], body.get("message", ""))


@app.get("/api/skills/{skill}")
def skill_detail(skill: str):
    store.ensure_initial_version(skill)
    latest = store.latest_version(skill)
    return {
        "name": skill,
        "tree": store.tree(skill),
        "latest_seq": latest["seq"] if latest else None,
        "dirty": store.is_dirty(skill),
        "en_synced_seq": store.en_synced_seq(skill),
        "sync_pending": sync.pending(skill),
        "main_file": cfg["skills"]["main_file"],
    }


@app.get("/api/skills/{skill}/file")
def read_file(skill: str, lang: str, path: str, ref: str | None = None):
    return {"content": store.read_file(skill, lang, path, ref)}


@app.post("/api/skills/{skill}/save")
def save_skill(skill: str, body: dict = Body(...)):
    """body: {changes: {lang: {path: content|null}}, message, author}"""
    n = store.apply_changes(skill, body.get("changes") or {})
    v = store.create_version(skill, body.get("message") or "网页编辑", source="manual", author=body.get("author", ""))
    return {"changed": n, "version": v["seq"] if v else None, "sync_pending": sync.pending(skill)}


@app.get("/api/skills/{skill}/versions")
def versions(skill: str):
    store.ensure_initial_version(skill)
    return store.list_versions(skill)


@app.get("/api/skills/{skill}/diff")
def diff(skill: str, a: str | None = None, b: str | None = "working"):
    return store.diff(skill, a, b)


@app.post("/api/skills/{skill}/restore")
def restore(skill: str, body: dict = Body(...)):
    v = store.restore(skill, int(body["seq"]), body.get("langs"), body.get("message", ""))
    return {"version": v["seq"] if v else None}


@app.post("/api/skills/{skill}/sync/propose")
def sync_propose(skill: str, body: dict = Body(default={})):
    store.create_version(skill, "同步前自动保存", source="manual")
    job = jobs.start("sync", f"LLM 同步英文：{skill}",
                     lambda j: sync.propose(j, skill, body.get("paths"), body.get("mode", "diff")))
    return {"job_id": job.id}


@app.post("/api/skills/{skill}/sync/apply")
def sync_apply(skill: str, body: dict = Body(...)):
    return sync.apply(skill, body.get("items") or [], body.get("message", ""), body.get("mark_synced", True))


@app.post("/api/skills/{skill}/sync/mark")
def sync_mark(skill: str):
    store.create_version(skill, "标记中英文已对齐", source="manual")
    latest = store.latest_version(skill)
    store.set_en_synced(skill, latest["seq"] if latest else None)
    return {"en_synced_seq": latest["seq"] if latest else None}


# ====================================================================== 环境 / 发布
@app.get("/api/env/pods")
def pods(filter: str = "", namespace: str | None = None):  # noqa: A002
    return env.list_pods(namespace, filter)


@app.get("/api/env/info")
def env_info(namespace: str | None = None):
    return env.env_info(namespace)


@app.post("/api/env/exec")
def env_exec(body: dict = Body(...)):
    r = env.exec(body["pod"], body["cmd"], body.get("namespace"))
    return {"rc": r.rc, "stdout": r.stdout[-200000:], "stderr": r.stderr[-20000:], "cmd": r.cmd}


@app.post("/api/deploy")
def deploy(body: dict = Body(...)):
    """body: {skill, version, langs, pods:[...], pod_filter, namespace}"""
    skill = body["skill"]
    seq = body.get("version")
    if not seq:
        store.create_version(skill, "发布前自动保存", source="manual")
        seq = store.latest_version(skill)["seq"]
    pods_ = body.get("pods")
    if not pods_:
        res = env.list_pods(body.get("namespace"), body.get("pod_filter", ""))
        if not res["ok"]:
            raise HTTPException(400, f"列 pod 失败：{res['error']}")
        pods_ = [p["name"] for p in res["pods"]]
    if not pods_:
        raise HTTPException(400, "没有匹配的 pod")
    langs = body.get("langs") or list(cfg["skills"]["langs"])
    job = jobs.start("deploy", f"发布 {skill} v{seq} → {len(pods_)} 个 pod",
                     lambda j: deployer.deploy(j, skill, int(seq), langs, pods_, body.get("namespace"),
                                               body.get("pod_filter", "")))
    return {"job_id": job.id, "version": seq}


@app.get("/api/deployments")
def deployments(skill: str | None = None):
    return deployer.history(skill)


# ====================================================================== 问答
@app.get("/api/chat/options")
def chat_options():
    return {"endpoints": chat.endpoints(), "faults": chat.faults()}


@app.post("/api/chat/preview")
def chat_preview(body: dict = Body(...)):
    built, _, faults = chat.build_request(body)
    return {"request": built, "faults": faults}


@app.post("/api/chat/ask")
def chat_ask(body: dict = Body(...)):
    return chat.ask(body)


@app.get("/api/chats")
def chats(q: str = "", limit: int = 200):
    return chat.list(limit, q)


@app.get("/api/chats/{chat_id}")
def chat_get(chat_id: int):
    c = chat.get(chat_id)
    if not c:
        raise HTTPException(404, "记录不存在")
    return c


@app.patch("/api/chats/{chat_id}")
def chat_update(chat_id: int, body: dict = Body(...)):
    return chat.update(chat_id, body.get("conversation_id"), body.get("note"))


@app.delete("/api/chats/{chat_id}")
def chat_delete(chat_id: int):
    chat.delete(chat_id)
    return {"ok": True}


# ====================================================================== 日志
@app.post("/api/logs/pull")
def logs_pull(body: dict = Body(default={})):
    pf = body.get("pod_filter") or cfg["logs"]["pod_filter"]
    conv = (body.get("conversation_id") or "").strip()
    job = jobs.start("logs", f"拉取日志 {pf or '全部 pod'}" + (f"（{conv}）" if conv else ""),
                     lambda j: logs.pull(j, pf, body.get("namespace"), conv, body.get("paths"), body.get("glob", "")))
    return {"job_id": job.id}


@app.post("/api/logs/upload")
async def logs_upload(file: UploadFile = File(...), pod: str = Query("upload")):
    text = (await file.read()).decode("utf-8", "replace")
    return logs.import_upload(file.filename or "upload.jsonl", text, pod)


@app.get("/api/logs/stats")
def logs_stats():
    return logs.stats()


@app.get("/api/logs/files")
def logs_files():
    return logs.list_files()


@app.get("/api/logs/files/download")
def logs_file_download(rel: str):
    return FileResponse(logs.file_path(rel), filename=logs.download_name(rel), media_type="application/x-ndjson")


@app.get("/api/logs/files/archive")
def logs_archive(rels: list[str] | None = Query(None)):
    import os
    import time
    path = logs.build_archive(rels)
    return FileResponse(path, filename=time.strftime("llm-calls-%Y%m%d-%H%M%S.zip"), media_type="application/zip",
                        background=BackgroundTask(os.remove, path))


@app.get("/api/conversations/{conversation_id}/download")
def conversation_download(conversation_id: str):
    lines = logs.conversation_lines(conversation_id)
    if not lines:
        raise HTTPException(404, "本地没有找到该会话的日志，请先拉取")
    safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in conversation_id)[:80]
    return Response("\n".join(lines) + "\n", media_type="application/x-ndjson",
                    headers={"Content-Disposition": f'attachment; filename="llm-calls-{safe}.jsonl"'})


@app.get("/api/conversations")
def conversations(q: str = ""):
    return logs.conversations(q)


@app.delete("/api/conversations/{conversation_id}")
def conversation_delete(conversation_id: str):
    logs.delete_conversation(conversation_id)
    return {"ok": True}


@app.get("/api/records/{record_id}")
def record(record_id: int):
    row = db.one("SELECT * FROM llm_calls WHERE id=?", (record_id,))
    if not row:
        raise HTTPException(404, "记录不存在")
    row["record"] = loads(row.pop("record_json"), {})
    return row


# ====================================================================== 分析
@app.get("/api/analysis/{conversation_id}")
def analyze(conversation_id: str, skill: str | None = None, version: int | None = None, lang: str | None = None):
    return analysis.analyze(conversation_id, skill or None, version or None, lang or None)


@app.post("/api/analysis/{conversation_id}/review")
def review(conversation_id: str, body: dict = Body(default={})):
    return analysis.review(conversation_id, body.get("skill") or None, body.get("version") or None,
                           body.get("lang") or None)


# ====================================================================== 前端
DIST = PROJECT_ROOT / "frontend" / "dist"
if DIST.is_dir():
    app.mount("/assets", StaticFiles(directory=DIST / "assets"), name="assets")

    @app.get("/{full_path:path}")
    def spa(full_path: str) -> Any:
        target = (DIST / full_path).resolve()
        if full_path and target.is_file() and DIST.resolve() in target.parents:
            return FileResponse(target)
        return FileResponse(DIST / "index.html")
