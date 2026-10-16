"""SQLite 存储。单进程使用，所有写操作串行化。"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Iterable

SCHEMA = """
CREATE TABLE IF NOT EXISTS versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    skill TEXT NOT NULL,
    seq INTEGER NOT NULL,
    message TEXT,
    source TEXT,           -- manual / import / llm-sync / rollback
    author TEXT,
    parent_seq INTEGER,
    files_json TEXT,       -- {lang: {path: sha1}}
    created_at REAL,
    UNIQUE(skill, seq)
);
CREATE TABLE IF NOT EXISTS skill_meta (
    skill TEXT PRIMARY KEY,
    en_synced_seq INTEGER  -- 英文版最后一次与中文对齐时，所基于的版本号
);
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    kind TEXT,
    title TEXT,
    status TEXT,
    logs TEXT,
    result_json TEXT,
    created_at REAL,
    finished_at REAL
);
CREATE TABLE IF NOT EXISTS deployments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    skill TEXT,
    version_seq INTEGER,
    langs TEXT,
    namespace TEXT,
    pod_filter TEXT,
    pods_json TEXT,        -- [{pod, ok, message}]
    status TEXT,
    job_id TEXT,
    created_at REAL
);
CREATE TABLE IF NOT EXISTS chats (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    endpoint TEXT,
    question TEXT,
    request_json TEXT,     -- {url, headers, body}
    faults_json TEXT,
    status_code INTEGER,
    response_headers_json TEXT,
    response_text TEXT,
    answer TEXT,
    conversation_id TEXT,
    latency_ms REAL,
    error TEXT,
    note TEXT,
    created_at REAL
);
CREATE INDEX IF NOT EXISTS idx_chats_conv ON chats(conversation_id);
CREATE TABLE IF NOT EXISTS llm_calls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id TEXT,
    pod TEXT,
    file TEXT,
    line_no INTEGER,
    line_hash TEXT,
    ts REAL,
    record_json TEXT,
    fetched_at REAL,
    UNIQUE(pod, line_hash)
);
CREATE INDEX IF NOT EXISTS idx_calls_conv ON llm_calls(conversation_id);
CREATE TABLE IF NOT EXISTS analyses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id TEXT,
    skill TEXT,
    version_seq INTEGER,
    lang TEXT,
    kind TEXT,             -- llm-review
    result_json TEXT,
    created_at REAL
);
CREATE INDEX IF NOT EXISTS idx_analyses_conv ON analyses(conversation_id);
"""


class DB:
    def __init__(self, path: Path):
        self.path = path
        self.conn = sqlite3.connect(str(path), check_same_thread=False, timeout=30)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.lock = threading.RLock()
        with self.lock:
            self.conn.executescript(SCHEMA)
            self.conn.commit()

    def execute(self, sql: str, params: Iterable[Any] = ()) -> sqlite3.Cursor:
        with self.lock:
            cur = self.conn.execute(sql, tuple(params))
            self.conn.commit()
            return cur

    def executemany(self, sql: str, rows: list[tuple]) -> int:
        """返回实际写入的行数（INSERT OR IGNORE 被忽略的行不计）。"""
        with self.lock:
            before = self.conn.total_changes
            self.conn.executemany(sql, rows)
            self.conn.commit()
            return self.conn.total_changes - before

    def query(self, sql: str, params: Iterable[Any] = ()) -> list[dict]:
        with self.lock:
            return [dict(r) for r in self.conn.execute(sql, tuple(params)).fetchall()]

    def one(self, sql: str, params: Iterable[Any] = ()) -> dict | None:
        rows = self.query(sql, params)
        return rows[0] if rows else None


def dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False)


def loads(s: str | None, default: Any = None) -> Any:
    if not s:
        return default
    try:
        return json.loads(s)
    except Exception:
        return default


def now() -> float:
    return time.time()
