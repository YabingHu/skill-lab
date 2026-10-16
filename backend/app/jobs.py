"""后台任务：发布、拉日志、LLM 同步等耗时操作在线程里执行，前端轮询日志。"""
from __future__ import annotations

import threading
import time
import traceback
import uuid
from typing import Any, Callable

from .db import DB, dumps, loads, now


class Job:
    def __init__(self, manager: "JobManager", kind: str, title: str):
        self.id = uuid.uuid4().hex[:12]
        self.kind = kind
        self.title = title
        self.status = "running"
        self.logs: list[str] = []
        self.result: Any = None
        self.created_at = now()
        self.finished_at: float | None = None
        self._m = manager

    def log(self, msg: str) -> None:
        stamp = time.strftime("%H:%M:%S")
        for line in str(msg).splitlines() or [""]:
            self.logs.append(f"[{stamp}] {line}")

    def to_dict(self, offset: int = 0) -> dict:
        return {
            "id": self.id, "kind": self.kind, "title": self.title, "status": self.status,
            "logs": self.logs[offset:], "offset": len(self.logs), "result": self.result,
            "created_at": self.created_at, "finished_at": self.finished_at,
        }


class JobManager:
    def __init__(self, db: DB):
        self.db = db
        self.jobs: dict[str, Job] = {}
        self.lock = threading.Lock()

    def start(self, kind: str, title: str, fn: Callable[[Job], Any]) -> Job:
        job = Job(self, kind, title)
        with self.lock:
            self.jobs[job.id] = job
        self.db.execute(
            "INSERT INTO jobs(id, kind, title, status, created_at) VALUES (?,?,?,?,?)",
            (job.id, kind, title, job.status, job.created_at),
        )

        def runner() -> None:
            try:
                job.result = fn(job)
                if job.status == "running":
                    job.status = "success"
            except Exception as e:  # noqa: BLE001
                job.log(f"任务失败：{e}")
                job.log(traceback.format_exc())
                job.status = "failed"
            job.finished_at = now()
            self.db.execute(
                "UPDATE jobs SET status=?, logs=?, result_json=?, finished_at=? WHERE id=?",
                (job.status, "\n".join(job.logs), dumps(job.result), job.finished_at, job.id),
            )

        threading.Thread(target=runner, daemon=True).start()
        return job

    def get(self, job_id: str, offset: int = 0) -> dict | None:
        job = self.jobs.get(job_id)
        if job:
            return job.to_dict(offset)
        row = self.db.one("SELECT * FROM jobs WHERE id=?", (job_id,))
        if not row:
            return None
        logs = (row.get("logs") or "").splitlines()
        return {
            "id": row["id"], "kind": row["kind"], "title": row["title"], "status": row["status"],
            "logs": logs[offset:], "offset": len(logs), "result": loads(row.get("result_json")),
            "created_at": row["created_at"], "finished_at": row["finished_at"],
        }

    def list(self, kind: str | None = None, limit: int = 50) -> list[dict]:
        if kind:
            rows = self.db.query("SELECT id, kind, title, status, created_at, finished_at FROM jobs "
                                 "WHERE kind=? ORDER BY created_at DESC LIMIT ?", (kind, limit))
        else:
            rows = self.db.query("SELECT id, kind, title, status, created_at, finished_at FROM jobs "
                                 "ORDER BY created_at DESC LIMIT ?", (limit,))
        for r in rows:
            j = self.jobs.get(r["id"])
            if j:
                r["status"] = j.status
        return rows
