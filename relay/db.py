from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from .util import utc_now


SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS jobs (
    job_id TEXT PRIMARY KEY,
    request_id TEXT,
    caller TEXT NOT NULL,
    task_hash TEXT NOT NULL,
    task_text TEXT,
    requested_worker TEXT NOT NULL,
    actual_worker TEXT,
    format TEXT NOT NULL,
    profile TEXT NOT NULL,
    output_path TEXT NOT NULL,
    artifact_path TEXT NOT NULL,
    status TEXT NOT NULL,
    result_status TEXT,
    error_code TEXT,
    error_message TEXT,
    fallback_enabled INTEGER NOT NULL DEFAULT 0,
    request_json TEXT NOT NULL,
    receipt_json TEXT,
    created_at TEXT NOT NULL,
    started_at TEXT,
    completed_at TEXT,
    updated_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_jobs_request_id
    ON jobs(request_id) WHERE request_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_jobs_task_hash_created ON jobs(task_hash, created_at);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);

CREATE TABLE IF NOT EXISTS attempts (
    attempt_id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT NOT NULL REFERENCES jobs(job_id) ON DELETE CASCADE,
    worker TEXT NOT NULL,
    worker_version TEXT,
    adapter_spec_hash TEXT,
    permission_mode TEXT,
    sandbox_mode TEXT,
    unattended_verified INTEGER NOT NULL DEFAULT 0,
    started_at TEXT NOT NULL,
    completed_at TEXT,
    exit_code INTEGER,
    status TEXT NOT NULL,
    failure_code TEXT,
    failure_message TEXT,
    stdout_path TEXT,
    stderr_path TEXT,
    command_json TEXT,
    fallback_reason TEXT
);
CREATE INDEX IF NOT EXISTS idx_attempts_job ON attempts(job_id);

CREATE TABLE IF NOT EXISTS artifacts (
    artifact_id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT NOT NULL REFERENCES jobs(job_id) ON DELETE CASCADE,
    relative_path TEXT NOT NULL,
    final_path TEXT NOT NULL,
    mime_type TEXT,
    size INTEGER NOT NULL,
    sha256 TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT NOT NULL REFERENCES jobs(job_id) ON DELETE CASCADE,
    timestamp TEXT NOT NULL,
    event_type TEXT NOT NULL,
    payload_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_job ON events(job_id, event_id);

CREATE TABLE IF NOT EXISTS capability_audits (
    audit_id INTEGER PRIMARY KEY AUTOINCREMENT,
    worker TEXT NOT NULL,
    version TEXT,
    audit_time TEXT NOT NULL,
    test_name TEXT NOT NULL,
    result TEXT NOT NULL,
    details_json TEXT,
    spec_hash TEXT
);
CREATE INDEX IF NOT EXISTS idx_audits_worker ON capability_audits(worker, audit_time);
"""


class Database:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.migrate()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=30000")
        try:
            yield conn
        finally:
            conn.close()

    def migrate(self) -> None:
        with self.connect() as conn:
            conn.executescript(SCHEMA)

    def create_job(self, row: dict[str, Any]) -> None:
        now = utc_now()
        values = {
            **row,
            "created_at": row.get("created_at", now),
            "updated_at": now,
            "status": row.get("status", "CREATED"),
        }
        keys = list(values)
        with self.connect() as conn:
            conn.execute(
                f"INSERT INTO jobs ({','.join(keys)}) VALUES ({','.join('?' for _ in keys)})",
                [values[k] for k in keys],
            )

    def update_job(self, job_id: str, **changes: Any) -> None:
        if not changes:
            return
        changes["updated_at"] = utc_now()
        keys = list(changes)
        with self.connect() as conn:
            conn.execute(
                f"UPDATE jobs SET {','.join(f'{k}=?' for k in keys)} WHERE job_id=?",
                [changes[k] for k in keys] + [job_id],
            )

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM jobs WHERE job_id=?", (job_id,)).fetchone()
            return dict(row) if row else None

    def get_by_request_id(self, request_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM jobs WHERE request_id=?", (request_id,)).fetchone()
            return dict(row) if row else None

    def find_recent_task(self, task_hash: str, since_iso: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM jobs WHERE task_hash=? AND created_at>=? "
                "AND status NOT IN ('FAILED','CANCELLED') ORDER BY created_at DESC LIMIT 1",
                (task_hash, since_iso),
            ).fetchone()
            return dict(row) if row else None

    def queued_jobs(self, limit: int = 20) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM jobs WHERE status='QUEUED' ORDER BY created_at LIMIT ?", (limit,)
            ).fetchall()
            return [dict(r) for r in rows]

    def list_jobs(self, status: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        with self.connect() as conn:
            if status:
                rows = conn.execute(
                    "SELECT * FROM jobs WHERE status=? ORDER BY created_at DESC LIMIT ?", (status, limit)
                ).fetchall()
            else:
                rows = conn.execute("SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
            return [dict(r) for r in rows]

    def create_attempt(self, job_id: str, worker: str, **values: Any) -> int:
        row = {
            "job_id": job_id,
            "worker": worker,
            "started_at": utc_now(),
            "status": "STARTING",
            **values,
        }
        keys = list(row)
        with self.connect() as conn:
            cursor = conn.execute(
                f"INSERT INTO attempts ({','.join(keys)}) VALUES ({','.join('?' for _ in keys)})",
                [row[k] for k in keys],
            )
            return int(cursor.lastrowid)

    def update_attempt(self, attempt_id: int, **changes: Any) -> None:
        if not changes:
            return
        keys = list(changes)
        with self.connect() as conn:
            conn.execute(
                f"UPDATE attempts SET {','.join(f'{k}=?' for k in keys)} WHERE attempt_id=?",
                [changes[k] for k in keys] + [attempt_id],
            )

    def attempts_for_job(self, job_id: str) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM attempts WHERE job_id=? ORDER BY attempt_id", (job_id,)).fetchall()
            return [dict(r) for r in rows]

    def add_event(self, job_id: str, event_type: str, payload: Any = None) -> None:
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO events(job_id,timestamp,event_type,payload_json) VALUES(?,?,?,?)",
                (job_id, utc_now(), event_type, json.dumps(payload, ensure_ascii=False) if payload is not None else None),
            )

    def events_for_job(self, job_id: str) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM events WHERE job_id=? ORDER BY event_id", (job_id,)).fetchall()
            return [dict(r) for r in rows]

    def add_artifact(self, job_id: str, **values: Any) -> None:
        row = {"job_id": job_id, "created_at": utc_now(), **values}
        keys = list(row)
        with self.connect() as conn:
            conn.execute(
                f"INSERT INTO artifacts ({','.join(keys)}) VALUES ({','.join('?' for _ in keys)})",
                [row[k] for k in keys],
            )

    def artifacts_for_job(self, job_id: str) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM artifacts WHERE job_id=? ORDER BY artifact_id", (job_id,)).fetchall()
            return [dict(r) for r in rows]

    def add_audit(self, worker: str, version: str | None, test_name: str, result: str,
                  details: Any = None, spec_hash: str | None = None) -> None:
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO capability_audits(worker,version,audit_time,test_name,result,details_json,spec_hash) "
                "VALUES(?,?,?,?,?,?,?)",
                (worker, version, utc_now(), test_name, result,
                 json.dumps(details, ensure_ascii=False) if details is not None else None, spec_hash),
            )

    def recover_interrupted(self) -> int:
        with self.connect() as conn:
            cursor = conn.execute(
                "UPDATE jobs SET status='FAILED',error_code='DAEMON_RESTARTED',"
                "error_message='Daemon restarted while job was active',completed_at=?,updated_at=? "
                "WHERE status IN ('PREPARING','RUNNING','VALIDATING','DELIVERING','CANCEL_REQUESTED')",
                (utc_now(), utc_now()),
            )
            return cursor.rowcount

    def request_cancel(self, job_id: str) -> bool:
        with self.connect() as conn:
            cursor = conn.execute(
                "UPDATE jobs SET status=CASE WHEN status='QUEUED' THEN 'CANCELLED' "
                "ELSE 'CANCEL_REQUESTED' END, completed_at=CASE WHEN status='QUEUED' "
                "THEN ? ELSE completed_at END, updated_at=? "
                "WHERE job_id=? AND status IN ('QUEUED','PREPARING','RUNNING')",
                (utc_now(), utc_now(), job_id),
            )
            return cursor.rowcount > 0
