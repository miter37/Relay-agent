from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .errors import RelayError
from .util import utc_now

CURRENT_SCHEMA_VERSION = 2

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS jobs (
    job_id TEXT PRIMARY KEY,
    request_id TEXT,
    caller TEXT NOT NULL,
    submitted_via TEXT NOT NULL DEFAULT 'legacy',
    task_hash TEXT NOT NULL,
    task_text TEXT,
    task_preview TEXT,
    title TEXT,
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
    schedule_id TEXT,
    scheduled_for TEXT,
    replayable INTEGER NOT NULL DEFAULT 1,
    updated_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_jobs_request_id
    ON jobs(request_id) WHERE request_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_jobs_task_hash_created ON jobs(task_hash, created_at);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
CREATE INDEX IF NOT EXISTS idx_jobs_completed_at ON jobs(completed_at);
CREATE INDEX IF NOT EXISTS idx_jobs_submitted_via ON jobs(submitted_via);
CREATE INDEX IF NOT EXISTS idx_jobs_schedule ON jobs(schedule_id, created_at);

CREATE TABLE IF NOT EXISTS schedules (
    schedule_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    source_job_id TEXT NOT NULL REFERENCES jobs(job_id),
    rule_json TEXT NOT NULL,
    timezone TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    deleted_at TEXT,
    overlap_policy TEXT NOT NULL DEFAULT 'skip',
    missed_policy TEXT NOT NULL DEFAULT 'skip',
    missed_grace_seconds INTEGER NOT NULL DEFAULT 43200,
    starts_at_utc TEXT,
    ends_at_utc TEXT,
    input_root TEXT NOT NULL,
    output_root TEXT NOT NULL,
    retention_json TEXT NOT NULL,
    next_run_at_utc TEXT,
    last_occurrence_key TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_schedules_next_run ON schedules(enabled, next_run_at_utc);
CREATE INDEX IF NOT EXISTS idx_schedules_source_job ON schedules(source_job_id);

CREATE TABLE IF NOT EXISTS schedule_runs (
    run_id TEXT PRIMARY KEY,
    schedule_id TEXT NOT NULL REFERENCES schedules(schedule_id) ON DELETE CASCADE,
    occurrence_key TEXT NOT NULL,
    scheduled_for_utc TEXT NOT NULL,
    scheduled_for_local TEXT NOT NULL,
    trigger_type TEXT NOT NULL,
    status TEXT NOT NULL,
    job_id TEXT REFERENCES jobs(job_id),
    output_path TEXT,
    artifact_path TEXT,
    error_code TEXT,
    error_message TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(schedule_id, occurrence_key)
);
CREATE INDEX IF NOT EXISTS idx_schedule_runs_schedule ON schedule_runs(schedule_id, scheduled_for_utc);
CREATE INDEX IF NOT EXISTS idx_schedule_runs_job ON schedule_runs(job_id);

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

MIGRATION_0_TO_1 = """
ALTER TABLE jobs ADD COLUMN submitted_via TEXT NOT NULL DEFAULT 'legacy';
ALTER TABLE jobs ADD COLUMN task_preview TEXT;
ALTER TABLE jobs ADD COLUMN title TEXT;
ALTER TABLE jobs ADD COLUMN schedule_id TEXT;
ALTER TABLE jobs ADD COLUMN scheduled_for TEXT;
ALTER TABLE jobs ADD COLUMN replayable INTEGER NOT NULL DEFAULT 1;
CREATE INDEX IF NOT EXISTS idx_jobs_completed_at ON jobs(completed_at);
CREATE INDEX IF NOT EXISTS idx_jobs_submitted_via ON jobs(submitted_via);
CREATE INDEX IF NOT EXISTS idx_jobs_schedule ON jobs(schedule_id, created_at);
"""

MIGRATION_1_TO_2 = """
CREATE TABLE IF NOT EXISTS schedules (
    schedule_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    source_job_id TEXT NOT NULL REFERENCES jobs(job_id),
    rule_json TEXT NOT NULL,
    timezone TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    deleted_at TEXT,
    overlap_policy TEXT NOT NULL DEFAULT 'skip',
    missed_policy TEXT NOT NULL DEFAULT 'skip',
    missed_grace_seconds INTEGER NOT NULL DEFAULT 43200,
    starts_at_utc TEXT,
    ends_at_utc TEXT,
    input_root TEXT NOT NULL,
    output_root TEXT NOT NULL,
    retention_json TEXT NOT NULL,
    next_run_at_utc TEXT,
    last_occurrence_key TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_schedules_next_run ON schedules(enabled, next_run_at_utc);
CREATE INDEX IF NOT EXISTS idx_schedules_source_job ON schedules(source_job_id);
CREATE TABLE IF NOT EXISTS schedule_runs (
    run_id TEXT PRIMARY KEY,
    schedule_id TEXT NOT NULL REFERENCES schedules(schedule_id) ON DELETE CASCADE,
    occurrence_key TEXT NOT NULL,
    scheduled_for_utc TEXT NOT NULL,
    scheduled_for_local TEXT NOT NULL,
    trigger_type TEXT NOT NULL,
    status TEXT NOT NULL,
    job_id TEXT REFERENCES jobs(job_id),
    output_path TEXT,
    artifact_path TEXT,
    error_code TEXT,
    error_message TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(schedule_id, occurrence_key)
);
CREATE INDEX IF NOT EXISTS idx_schedule_runs_schedule ON schedule_runs(schedule_id, scheduled_for_utc);
CREATE INDEX IF NOT EXISTS idx_schedule_runs_job ON schedule_runs(job_id);
"""

LEGACY_JOB_COLUMNS = {
    "job_id",
    "request_id",
    "caller",
    "task_hash",
    "task_text",
    "requested_worker",
    "actual_worker",
    "format",
    "profile",
    "output_path",
    "artifact_path",
    "status",
    "result_status",
    "error_code",
    "error_message",
    "fallback_enabled",
    "request_json",
    "receipt_json",
    "created_at",
    "started_at",
    "completed_at",
    "updated_at",
}


class Database:
    def __init__(self, path: Path):
        self.path = path
        self.last_backup_path: Path | None = None
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
            version = int(conn.execute("PRAGMA user_version").fetchone()[0])
            tables = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
                ).fetchall()
            }
            if version > CURRENT_SCHEMA_VERSION:
                raise RelayError(
                    "DATABASE_TOO_NEW",
                    f"Database schema version {version} is newer than supported version {CURRENT_SCHEMA_VERSION}",
                )
            if not tables:
                conn.executescript(SCHEMA)
                conn.execute(f"PRAGMA user_version={CURRENT_SCHEMA_VERSION}")
                return
            if version == 0:
                self._validate_legacy_schema(conn, tables)
                self.last_backup_path = self._create_backup()
                try:
                    conn.execute("BEGIN")
                    for statement in MIGRATION_0_TO_1.split(";"):
                        if statement.strip():
                            conn.execute(statement)
                    for statement in MIGRATION_1_TO_2.split(";"):
                        if statement.strip():
                            conn.execute(statement)
                    conn.execute(f"PRAGMA user_version={CURRENT_SCHEMA_VERSION}")
                    self._backfill_job_metadata(conn)
                    conn.execute("COMMIT")
                except Exception as exc:
                    conn.rollback()
                    backup = f" Backup: {self.last_backup_path}" if self.last_backup_path else ""
                    raise RelayError("DATABASE_MIGRATION_FAILED", f"Database migration failed.{backup}") from exc
            elif version == 1:
                self.last_backup_path = self._create_backup()
                try:
                    conn.execute("BEGIN")
                    for statement in MIGRATION_1_TO_2.split(";"):
                        if statement.strip():
                            conn.execute(statement)
                    conn.execute(f"PRAGMA user_version={CURRENT_SCHEMA_VERSION}")
                    conn.execute("COMMIT")
                except Exception as exc:
                    conn.rollback()
                    backup = f" Backup: {self.last_backup_path}" if self.last_backup_path else ""
                    raise RelayError("DATABASE_MIGRATION_FAILED", f"Database migration failed.{backup}") from exc
            if version == CURRENT_SCHEMA_VERSION:
                self._backfill_job_metadata(conn)
                return

    def _validate_legacy_schema(self, conn: sqlite3.Connection, tables: set[str]) -> None:
        required_tables = {"jobs", "attempts", "artifacts", "events", "capability_audits"}
        missing_tables = required_tables - tables
        if missing_tables:
            missing = ", ".join(sorted(missing_tables))
            raise RelayError(
                "DATABASE_MIGRATION_FAILED",
                f"Database is not a supported Relay legacy schema; missing tables: {missing}",
            )
        columns = {row[1] for row in conn.execute("PRAGMA table_info(jobs)").fetchall()}
        missing_columns = LEGACY_JOB_COLUMNS - columns
        if missing_columns:
            missing = ", ".join(sorted(missing_columns))
            raise RelayError(
                "DATABASE_MIGRATION_FAILED",
                f"Database is not a supported Relay legacy schema; missing columns: {missing}",
            )

    def _create_backup(self) -> Path:
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        backup_path = self.path.with_name(f"{self.path.name}.backup-{stamp}.db")
        suffix = 1
        while backup_path.exists():
            backup_path = self.path.with_name(f"{self.path.name}.backup-{stamp}-{suffix}.db")
            suffix += 1
        source = sqlite3.connect(self.path)
        target = sqlite3.connect(backup_path)
        try:
            source.backup(target)
        finally:
            target.close()
            source.close()
        return backup_path

    @staticmethod
    def _title_from_task(task: str, job_id: str) -> str:
        first_line = next((line.strip() for line in task.splitlines() if line.strip()), "")
        value = " ".join((first_line or f"Job {job_id[:8]}").split())
        return value if len(value) <= 60 else value[:59].rstrip() + "…"

    def _backfill_job_metadata(self, conn: sqlite3.Connection) -> None:
        rows = conn.execute(
            "SELECT job_id,task_text,title,task_preview FROM jobs "
            "WHERE title IS NULL OR (task_preview IS NULL AND task_text IS NOT NULL)"
        ).fetchall()
        for row in rows:
            changes: list[str] = []
            values: list[Any] = []
            task = row[1]
            if row[2] is None:
                changes.append("title=?")
                values.append(self._title_from_task(task or "", row[0]))
            if row[3] is None and task is not None:
                changes.append("task_preview=?")
                normalized = " ".join(task.split())
                values.append(normalized if len(normalized) <= 240 else normalized[:239].rstrip() + "…")
            if changes:
                values.append(row[0])
                conn.execute(f"UPDATE jobs SET {','.join(changes)} WHERE job_id=?", values)

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

    def create_schedule(self, row: dict[str, Any]) -> None:
        now = utc_now()
        values = {"enabled": 1, **row, "created_at": row.get("created_at", now), "updated_at": now}
        keys = list(values)
        with self.connect() as conn:
            conn.execute(
                f"INSERT INTO schedules ({','.join(keys)}) VALUES ({','.join('?' for _ in keys)})",
                [values[key] for key in keys],
            )

    def get_schedule(self, schedule_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM schedules WHERE schedule_id=?", (schedule_id,)).fetchone()
            return dict(row) if row else None

    def list_schedules(self, *, include_deleted: bool = False) -> list[dict[str, Any]]:
        query = "SELECT * FROM schedules"
        if not include_deleted:
            query += " WHERE deleted_at IS NULL"
        query += " ORDER BY created_at DESC"
        with self.connect() as conn:
            return [dict(row) for row in conn.execute(query).fetchall()]

    def update_schedule(self, schedule_id: str, **changes: Any) -> None:
        if not changes:
            return
        changes["updated_at"] = utc_now()
        keys = list(changes)
        with self.connect() as conn:
            conn.execute(
                f"UPDATE schedules SET {','.join(f'{key}=?' for key in keys)} WHERE schedule_id=?",
                [changes[key] for key in keys] + [schedule_id],
            )

    def insert_schedule_run(self, schedule_id: str, row: dict[str, Any]) -> bool:
        now = utc_now()
        values = {"schedule_id": schedule_id, **row, "created_at": row.get("created_at", now), "updated_at": now}
        keys = list(values)
        with self.connect() as conn:
            conn.execute(
                f"INSERT INTO schedule_runs ({','.join(keys)}) VALUES ({','.join('?' for _ in keys)})",
                [values[key] for key in keys],
            )
        return True

    def get_schedule_run(self, run_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM schedule_runs WHERE run_id=?", (run_id,)).fetchone()
            return dict(row) if row else None

    def list_schedule_runs(self, schedule_id: str, *, limit: int = 100) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM schedule_runs WHERE schedule_id=? ORDER BY scheduled_for_utc DESC LIMIT ?",
                (schedule_id, limit),
            ).fetchall()
            return [dict(row) for row in rows]

    def active_jobs_for_schedule(self, schedule_id: str) -> list[dict[str, Any]]:
        statuses = ("QUEUED", "PREPARING", "RUNNING", "VALIDATING", "DELIVERING", "CANCEL_REQUESTED")
        placeholders = ",".join("?" for _ in statuses)
        with self.connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM jobs WHERE schedule_id=? AND status IN ({placeholders})",
                (schedule_id, *statuses),
            ).fetchall()
            return [dict(row) for row in rows]

    def link_schedule_run_job(self, run_id: str, job_id: str, *, status: str = "QUEUED") -> None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE schedule_runs SET job_id=?,status=?,updated_at=? WHERE run_id=?",
                (job_id, status, utc_now(), run_id),
            )

    def update_schedule_run(self, run_id: str, **changes: Any) -> None:
        if not changes:
            return
        changes["updated_at"] = utc_now()
        keys = list(changes)
        with self.connect() as conn:
            conn.execute(
                f"UPDATE schedule_runs SET {','.join(f'{key}=?' for key in keys)} WHERE run_id=?",
                [changes[key] for key in keys] + [run_id],
            )

    def list_jobs(self, status: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        with self.connect() as conn:
            if status:
                rows = conn.execute(
                    "SELECT * FROM jobs WHERE status=? ORDER BY created_at DESC LIMIT ?", (status, limit)
                ).fetchall()
            else:
                rows = conn.execute("SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
            return [dict(r) for r in rows]

    def list_jobs_page(
        self,
        *,
        bucket: str = "all",
        status: str | None = None,
        agent: str | None = None,
        submitted_via: str | None = None,
        query: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        limit: int = 50,
        cursor: tuple[str, str] | None = None,
    ) -> list[dict[str, Any]]:
        bucket_statuses = {
            "waiting": ("CREATED", "QUEUED"),
            "running": ("PREPARING", "RUNNING", "VALIDATING", "DELIVERING", "CANCEL_REQUESTED"),
            "finished": ("COMPLETED", "PARTIAL", "FAILED", "CANCELLED"),
            "all": (),
        }
        if bucket not in bucket_statuses:
            raise ValueError(f"Unsupported job bucket: {bucket}")
        if limit < 1 or limit > 200:
            raise ValueError("Job limit must be between 1 and 200")

        where: list[str] = []
        params: list[Any] = []
        statuses = bucket_statuses[bucket]
        if statuses:
            placeholders = ",".join("?" for _ in statuses)
            where.append(f"status IN ({placeholders})")
            params.extend(statuses)
        if status:
            where.append("status=?")
            params.append(status)
        if agent:
            where.append("(requested_worker=? OR actual_worker=?)")
            params.extend([agent, agent])
        if submitted_via:
            where.append("submitted_via=?")
            params.append(submitted_via)
        if query:
            escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            pattern = f"%{escaped}%"
            where.append(
                "(job_id LIKE ? ESCAPE '\\' COLLATE NOCASE "
                "OR title LIKE ? ESCAPE '\\' COLLATE NOCASE "
                "OR task_preview LIKE ? ESCAPE '\\' COLLATE NOCASE "
                "OR requested_worker LIKE ? ESCAPE '\\' COLLATE NOCASE "
                "OR actual_worker LIKE ? ESCAPE '\\' COLLATE NOCASE "
                "OR profile LIKE ? ESCAPE '\\' COLLATE NOCASE "
                "OR error_code LIKE ? ESCAPE '\\' COLLATE NOCASE "
                "OR COALESCE(json_extract(CASE WHEN json_valid(request_json) THEN request_json ELSE '{}' END, '$.model'), '') "
                "LIKE ? ESCAPE '\\' COLLATE NOCASE "
                "OR EXISTS (SELECT 1 FROM json_each(CASE WHEN json_valid(request_json) THEN request_json ELSE '{}' END, '$.attachments') "
                "WHERE CAST(value AS TEXT) LIKE ? ESCAPE '\\' COLLATE NOCASE))"
            )
            params.extend([pattern] * 9)
        if date_from:
            where.append("COALESCE(completed_at, created_at)>=?")
            params.append(date_from)
        if date_to:
            where.append("COALESCE(completed_at, created_at)<=?")
            params.append(date_to)

        sort_expression = {
            "waiting": "created_at",
            "running": "COALESCE(started_at, created_at)",
            "finished": "COALESCE(completed_at, created_at)",
            "all": "created_at",
        }[bucket]
        ascending = bucket == "waiting"
        if cursor:
            operator = ">" if ascending else "<"
            where.append(f"({sort_expression} {operator} ? OR ({sort_expression}=? AND job_id {operator} ?))")
            params.extend([cursor[0], cursor[0], cursor[1]])

        sql = "SELECT * FROM jobs"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += f" ORDER BY {sort_expression} {'ASC' if ascending else 'DESC'}, job_id {'ASC' if ascending else 'DESC'} LIMIT ?"
        params.append(limit + 1)
        with self.connect() as conn:
            rows = conn.execute(sql, params).fetchall()
            return [dict(row) for row in rows]

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
                (
                    job_id,
                    utc_now(),
                    event_type,
                    json.dumps(payload, ensure_ascii=False) if payload is not None else None,
                ),
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

    def add_audit(
        self,
        worker: str,
        version: str | None,
        test_name: str,
        result: str,
        details: Any = None,
        spec_hash: str | None = None,
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO capability_audits(worker,version,audit_time,test_name,result,details_json,spec_hash) "
                "VALUES(?,?,?,?,?,?,?)",
                (
                    worker,
                    version,
                    utc_now(),
                    test_name,
                    result,
                    json.dumps(details, ensure_ascii=False) if details is not None else None,
                    spec_hash,
                ),
            )

    def recover_interrupted(self) -> int:
        with self.connect() as conn:
            cursor = conn.execute(
                "UPDATE jobs SET status='FAILED',error_code='DAEMON_RESTARTED',"
                "error_message='Daemon restarted while job was active',completed_at=?,updated_at=?,"
                "request_json=CASE WHEN replayable=0 THEN '{}' ELSE request_json END,"
                "task_text=CASE WHEN replayable=0 THEN NULL ELSE task_text END,"
                "task_preview=CASE WHEN replayable=0 THEN NULL ELSE task_preview END "
                "WHERE status IN ('PREPARING','RUNNING','VALIDATING','DELIVERING','CANCEL_REQUESTED')",
                (utc_now(), utc_now()),
            )
            return cursor.rowcount

    def scrub_non_replayable(self, job_id: str) -> None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE jobs SET request_json='{}',task_text=NULL,task_preview=NULL,updated_at=? "
                "WHERE job_id=? AND replayable=0",
                (utc_now(), job_id),
            )

    def request_cancel(self, job_id: str) -> bool:
        with self.connect() as conn:
            cursor = conn.execute(
                "UPDATE jobs SET status=CASE WHEN status='QUEUED' THEN 'CANCELLED' "
                "ELSE 'CANCEL_REQUESTED' END, completed_at=CASE WHEN status='QUEUED' "
                "THEN ? ELSE completed_at END, updated_at=?,"
                "request_json=CASE WHEN status='QUEUED' AND replayable=0 THEN '{}' ELSE request_json END,"
                "task_text=CASE WHEN status='QUEUED' AND replayable=0 THEN NULL ELSE task_text END,"
                "task_preview=CASE WHEN status='QUEUED' AND replayable=0 THEN NULL ELSE task_preview END "
                "WHERE job_id=? AND status IN ('QUEUED','PREPARING','RUNNING','VALIDATING','DELIVERING')",
                (utc_now(), utc_now(), job_id),
            )
            return cursor.rowcount > 0
