from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .errors import RelayError
from .search import artifact_mime, artifact_search_content, fts_query, result_summary
from .util import new_artifact_uid, utc_now

CURRENT_SCHEMA_VERSION = 10

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
    trigger_type TEXT NOT NULL DEFAULT 'manual',
    task_id TEXT,
    task_snapshot_json TEXT,
    input_manifest_json TEXT,
    updated_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_jobs_request_id
    ON jobs(request_id) WHERE request_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_jobs_task_hash_created ON jobs(task_hash, created_at);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
CREATE INDEX IF NOT EXISTS idx_jobs_completed_at ON jobs(completed_at);
CREATE INDEX IF NOT EXISTS idx_jobs_submitted_via ON jobs(submitted_via);
CREATE INDEX IF NOT EXISTS idx_jobs_schedule ON jobs(schedule_id, created_at);
CREATE INDEX IF NOT EXISTS idx_jobs_trigger ON jobs(trigger_type, created_at);

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
    artifact_uid TEXT,
    role TEXT NOT NULL DEFAULT 'output',
    producer_attempt_id INTEGER,
    created_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_artifacts_uid
    ON artifacts(artifact_uid) WHERE artifact_uid IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_artifacts_job_role ON artifacts(job_id, role);

CREATE TABLE IF NOT EXISTS artifact_lineage (
    lineage_id INTEGER PRIMARY KEY AUTOINCREMENT,
    consumer_job_id TEXT NOT NULL REFERENCES jobs(job_id) ON DELETE CASCADE,
    source_artifact_uid TEXT NOT NULL,
    source_job_id TEXT NOT NULL REFERENCES jobs(job_id),
    alias TEXT NOT NULL,
    binding_mode TEXT NOT NULL DEFAULT 'snapshot',
    source_relative_path TEXT NOT NULL,
    source_sha256 TEXT NOT NULL,
    source_size INTEGER NOT NULL,
    snapshot_relative_path TEXT NOT NULL,
    snapshot_sha256 TEXT NOT NULL,
    snapshot_size INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(consumer_job_id, alias)
);
CREATE INDEX IF NOT EXISTS idx_lineage_source_artifact ON artifact_lineage(source_artifact_uid);
CREATE INDEX IF NOT EXISTS idx_lineage_source_job ON artifact_lineage(source_job_id);
CREATE INDEX IF NOT EXISTS idx_lineage_consumer_job ON artifact_lineage(consumer_job_id);

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

CREATE TABLE IF NOT EXISTS tasks (
    task_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT,
    instructions TEXT,
    default_worker TEXT,
    fallback_enabled INTEGER NOT NULL DEFAULT 1,
    timeout_seconds INTEGER,
    profile TEXT,
    result_format TEXT,
    input_schema TEXT,
    output_contract TEXT,
    validation_policy TEXT,
    version INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tasks_created ON tasks(created_at);
CREATE INDEX IF NOT EXISTS idx_jobs_task ON jobs(task_id, created_at);

CREATE TABLE IF NOT EXISTS projects (
    project_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT,
    version INTEGER NOT NULL DEFAULT 1,
    definition_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    deleted_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_projects_created ON projects(created_at);

CREATE TABLE IF NOT EXISTS project_runs (
    project_run_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(project_id),
    project_version INTEGER NOT NULL,
    project_snapshot_json TEXT NOT NULL,
    status TEXT NOT NULL,
    trigger_type TEXT NOT NULL,
    submitted_via TEXT NOT NULL,
    final_artifact_ids_json TEXT,
    warnings_json TEXT,
    receipt_json TEXT,
    created_at TEXT NOT NULL,
    started_at TEXT,
    completed_at TEXT,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_project_runs_project ON project_runs(project_id, created_at);
CREATE INDEX IF NOT EXISTS idx_project_runs_status ON project_runs(status);

CREATE TABLE IF NOT EXISTS project_run_steps (
    project_run_id TEXT NOT NULL REFERENCES project_runs(project_run_id) ON DELETE CASCADE,
    node_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    task_version INTEGER NOT NULL,
    status TEXT NOT NULL,
    active_task_run_id TEXT,
    input_manifest_json TEXT,
    resolved_connections_json TEXT,
    error_code TEXT,
    error_message TEXT,
    started_at TEXT,
    completed_at TEXT,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (project_run_id, node_id)
);
CREATE INDEX IF NOT EXISTS idx_project_run_steps_active ON project_run_steps(active_task_run_id);

CREATE TABLE IF NOT EXISTS project_step_runs (
    project_run_id TEXT NOT NULL,
    node_id TEXT NOT NULL,
    step_attempt INTEGER NOT NULL,
    task_run_id TEXT NOT NULL,
    worker_override TEXT,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    completed_at TEXT,
    PRIMARY KEY (project_run_id, node_id, step_attempt),
    UNIQUE (task_run_id),
    FOREIGN KEY (project_run_id, node_id) REFERENCES project_run_steps(project_run_id, node_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS routines (
    routine_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    target_type TEXT NOT NULL,
    target_id TEXT NOT NULL,
    rule_json TEXT NOT NULL,
    timezone TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    deleted_at TEXT,
    overlap_policy TEXT NOT NULL DEFAULT 'skip',
    missed_policy TEXT NOT NULL DEFAULT 'skip',
    missed_grace_seconds INTEGER NOT NULL DEFAULT 43200,
    version_policy TEXT NOT NULL DEFAULT 'latest',
    pinned_version INTEGER,
    input_policy_json TEXT,
    notification_policy_json TEXT,
    starts_at_utc TEXT,
    ends_at_utc TEXT,
    next_run_at_utc TEXT,
    last_occurrence_key TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_routines_next_run ON routines(enabled, next_run_at_utc);

CREATE TABLE IF NOT EXISTS routine_runs (
    run_id TEXT PRIMARY KEY,
    routine_id TEXT NOT NULL REFERENCES routines(routine_id) ON DELETE CASCADE,
    occurrence_key TEXT NOT NULL,
    scheduled_for_utc TEXT NOT NULL,
    scheduled_for_local TEXT NOT NULL,
    trigger_type TEXT NOT NULL,
    status TEXT NOT NULL,
    target_type TEXT NOT NULL,
    task_run_id TEXT REFERENCES jobs(job_id),
    project_run_id TEXT REFERENCES project_runs(project_run_id),
    error_code TEXT,
    error_message TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(routine_id, occurrence_key)
);
CREATE INDEX IF NOT EXISTS idx_routine_runs_routine ON routine_runs(routine_id, scheduled_for_utc);
CREATE INDEX IF NOT EXISTS idx_routine_runs_task ON routine_runs(task_run_id);
CREATE INDEX IF NOT EXISTS idx_routine_runs_project ON routine_runs(project_run_id);

ALTER TABLE jobs ADD COLUMN routine_id TEXT;
ALTER TABLE project_runs ADD COLUMN routine_id TEXT;
CREATE INDEX IF NOT EXISTS idx_jobs_routine ON jobs(routine_id);
CREATE INDEX IF NOT EXISTS idx_project_runs_routine ON project_runs(routine_id);

CREATE TABLE IF NOT EXISTS approvals (
    approval_id TEXT PRIMARY KEY,
    project_run_id TEXT NOT NULL REFERENCES project_runs(project_run_id) ON DELETE CASCADE,
    node_id TEXT NOT NULL,
    token TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL DEFAULT 'pending',
    reviewer TEXT,
    reason TEXT,
    edited_artifact_uid TEXT,
    created_at TEXT NOT NULL,
    decided_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_approvals_project_run ON approvals(project_run_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_approvals_token ON approvals(token);

CREATE TABLE IF NOT EXISTS deliveries (
    delivery_id TEXT PRIMARY KEY,
    project_run_id TEXT NOT NULL REFERENCES project_runs(project_run_id) ON DELETE CASCADE,
    approval_id TEXT REFERENCES approvals(approval_id),
    kind TEXT NOT NULL DEFAULT 'folder',
    target_path TEXT NOT NULL,
    artifact_uid TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'completed',
    error TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_deliveries_project_run ON deliveries(project_run_id);

CREATE TABLE IF NOT EXISTS notification_events (
    event_id TEXT PRIMARY KEY,
    routine_id TEXT,
    project_run_id TEXT,
    trigger_type TEXT NOT NULL,
    sink_url TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'delivered',
    status_code INTEGER,
    attempt INTEGER NOT NULL DEFAULT 1,
    error TEXT,
    payload_hash TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_notification_events_routine ON notification_events(routine_id);
CREATE INDEX IF NOT EXISTS idx_notification_events_project ON notification_events(project_run_id);
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

MIGRATION_2_TO_3 = """
ALTER TABLE jobs ADD COLUMN trigger_type TEXT NOT NULL DEFAULT 'manual';
ALTER TABLE jobs ADD COLUMN task_id TEXT;
ALTER TABLE jobs ADD COLUMN task_snapshot_json TEXT;
ALTER TABLE artifacts ADD COLUMN artifact_uid TEXT;
ALTER TABLE artifacts ADD COLUMN role TEXT NOT NULL DEFAULT 'output';
ALTER TABLE artifacts ADD COLUMN producer_attempt_id INTEGER;
CREATE INDEX IF NOT EXISTS idx_jobs_trigger ON jobs(trigger_type, created_at);
CREATE UNIQUE INDEX IF NOT EXISTS idx_artifacts_uid
    ON artifacts(artifact_uid) WHERE artifact_uid IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_artifacts_job_role ON artifacts(job_id, role);
"""

MIGRATION_3_TO_4 = """
ALTER TABLE jobs ADD COLUMN input_manifest_json TEXT;
CREATE TABLE IF NOT EXISTS artifact_lineage (
    lineage_id INTEGER PRIMARY KEY AUTOINCREMENT,
    consumer_job_id TEXT NOT NULL REFERENCES jobs(job_id) ON DELETE CASCADE,
    source_artifact_uid TEXT NOT NULL,
    source_job_id TEXT NOT NULL REFERENCES jobs(job_id),
    alias TEXT NOT NULL,
    binding_mode TEXT NOT NULL DEFAULT 'snapshot',
    source_relative_path TEXT NOT NULL,
    source_sha256 TEXT NOT NULL,
    source_size INTEGER NOT NULL,
    snapshot_relative_path TEXT NOT NULL,
    snapshot_sha256 TEXT NOT NULL,
    snapshot_size INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(consumer_job_id, alias)
);
CREATE INDEX IF NOT EXISTS idx_lineage_source_artifact ON artifact_lineage(source_artifact_uid);
CREATE INDEX IF NOT EXISTS idx_lineage_source_job ON artifact_lineage(source_job_id);
CREATE INDEX IF NOT EXISTS idx_lineage_consumer_job ON artifact_lineage(consumer_job_id);
"""

MIGRATION_4_TO_5 = """
-- FTS5 tables are created opportunistically after the schema migration.
"""

MIGRATION_9_TO_10 = """
CREATE TABLE IF NOT EXISTS notification_events (
    event_id TEXT PRIMARY KEY,
    routine_id TEXT,
    project_run_id TEXT,
    trigger_type TEXT NOT NULL,
    sink_url TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'delivered',
    status_code INTEGER,
    attempt INTEGER NOT NULL DEFAULT 1,
    error TEXT,
    payload_hash TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_notification_events_routine ON notification_events(routine_id);
CREATE INDEX IF NOT EXISTS idx_notification_events_project ON notification_events(project_run_id);
"""

MIGRATION_8_TO_9 = """
CREATE TABLE IF NOT EXISTS approvals (
    approval_id TEXT PRIMARY KEY,
    project_run_id TEXT NOT NULL REFERENCES project_runs(project_run_id) ON DELETE CASCADE,
    node_id TEXT NOT NULL,
    token TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL DEFAULT 'pending',
    reviewer TEXT,
    reason TEXT,
    edited_artifact_uid TEXT,
    created_at TEXT NOT NULL,
    decided_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_approvals_project_run ON approvals(project_run_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_approvals_token ON approvals(token);

CREATE TABLE IF NOT EXISTS deliveries (
    delivery_id TEXT PRIMARY KEY,
    project_run_id TEXT NOT NULL REFERENCES project_runs(project_run_id) ON DELETE CASCADE,
    approval_id TEXT REFERENCES approvals(approval_id),
    kind TEXT NOT NULL DEFAULT 'folder',
    target_path TEXT NOT NULL,
    artifact_uid TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'completed',
    error TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_deliveries_project_run ON deliveries(project_run_id);
"""

MIGRATION_7_TO_8 = """
CREATE TABLE IF NOT EXISTS routines (
    routine_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    target_type TEXT NOT NULL,
    target_id TEXT NOT NULL,
    rule_json TEXT NOT NULL,
    timezone TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    deleted_at TEXT,
    overlap_policy TEXT NOT NULL DEFAULT 'skip',
    missed_policy TEXT NOT NULL DEFAULT 'skip',
    missed_grace_seconds INTEGER NOT NULL DEFAULT 43200,
    version_policy TEXT NOT NULL DEFAULT 'latest',
    pinned_version INTEGER,
    input_policy_json TEXT,
    notification_policy_json TEXT,
    starts_at_utc TEXT,
    ends_at_utc TEXT,
    next_run_at_utc TEXT,
    last_occurrence_key TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_routines_next_run ON routines(enabled, next_run_at_utc);

CREATE TABLE IF NOT EXISTS routine_runs (
    run_id TEXT PRIMARY KEY,
    routine_id TEXT NOT NULL REFERENCES routines(routine_id) ON DELETE CASCADE,
    occurrence_key TEXT NOT NULL,
    scheduled_for_utc TEXT NOT NULL,
    scheduled_for_local TEXT NOT NULL,
    trigger_type TEXT NOT NULL,
    status TEXT NOT NULL,
    target_type TEXT NOT NULL,
    task_run_id TEXT REFERENCES jobs(job_id),
    project_run_id TEXT REFERENCES project_runs(project_run_id),
    error_code TEXT,
    error_message TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(routine_id, occurrence_key)
);
CREATE INDEX IF NOT EXISTS idx_routine_runs_routine ON routine_runs(routine_id, scheduled_for_utc);
CREATE INDEX IF NOT EXISTS idx_routine_runs_task ON routine_runs(task_run_id);
CREATE INDEX IF NOT EXISTS idx_routine_runs_project ON routine_runs(project_run_id);

ALTER TABLE jobs ADD COLUMN routine_id TEXT;
ALTER TABLE project_runs ADD COLUMN routine_id TEXT;
CREATE INDEX IF NOT EXISTS idx_jobs_routine ON jobs(routine_id);
CREATE INDEX IF NOT EXISTS idx_project_runs_routine ON project_runs(routine_id);
"""

MIGRATION_6_TO_7 = """
CREATE TABLE IF NOT EXISTS projects (
    project_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT,
    version INTEGER NOT NULL DEFAULT 1,
    definition_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    deleted_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_projects_created ON projects(created_at);

CREATE TABLE IF NOT EXISTS project_runs (
    project_run_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(project_id),
    project_version INTEGER NOT NULL,
    project_snapshot_json TEXT NOT NULL,
    status TEXT NOT NULL,
    trigger_type TEXT NOT NULL,
    submitted_via TEXT NOT NULL,
    final_artifact_ids_json TEXT,
    warnings_json TEXT,
    receipt_json TEXT,
    created_at TEXT NOT NULL,
    started_at TEXT,
    completed_at TEXT,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_project_runs_project ON project_runs(project_id, created_at);
CREATE INDEX IF NOT EXISTS idx_project_runs_status ON project_runs(status);

CREATE TABLE IF NOT EXISTS project_run_steps (
    project_run_id TEXT NOT NULL REFERENCES project_runs(project_run_id) ON DELETE CASCADE,
    node_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    task_version INTEGER NOT NULL,
    status TEXT NOT NULL,
    active_task_run_id TEXT,
    input_manifest_json TEXT,
    resolved_connections_json TEXT,
    error_code TEXT,
    error_message TEXT,
    started_at TEXT,
    completed_at TEXT,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (project_run_id, node_id)
);
CREATE INDEX IF NOT EXISTS idx_project_run_steps_active ON project_run_steps(active_task_run_id);

CREATE TABLE IF NOT EXISTS project_step_runs (
    project_run_id TEXT NOT NULL,
    node_id TEXT NOT NULL,
    step_attempt INTEGER NOT NULL,
    task_run_id TEXT NOT NULL,
    worker_override TEXT,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    completed_at TEXT,
    PRIMARY KEY (project_run_id, node_id, step_attempt),
    UNIQUE (task_run_id),
    FOREIGN KEY (project_run_id, node_id) REFERENCES project_run_steps(project_run_id, node_id) ON DELETE CASCADE
);
"""

MIGRATION_5_TO_6 = """CREATE TABLE IF NOT EXISTS tasks (
    task_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT,
    instructions TEXT,
    default_worker TEXT,
    fallback_enabled INTEGER NOT NULL DEFAULT 1,
    timeout_seconds INTEGER,
    profile TEXT,
    result_format TEXT,
    input_schema TEXT,
    output_contract TEXT,
    validation_policy TEXT,
    version INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tasks_created ON tasks(created_at);
CREATE INDEX IF NOT EXISTS idx_jobs_task ON jobs(task_id, created_at);
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
                self._ensure_search_tables(conn)
                self.rebuild_search_index()
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
                    for statement in MIGRATION_2_TO_3.split(";"):
                        if statement.strip():
                            conn.execute(statement)
                    for statement in MIGRATION_3_TO_4.split(";"):
                        if statement.strip():
                            conn.execute(statement)
                    for statement in MIGRATION_4_TO_5.split(";"):
                        if statement.strip():
                            conn.execute(statement)
                    for statement in MIGRATION_5_TO_6.split(";"):
                        if statement.strip():
                            conn.execute(statement)
                    for statement in MIGRATION_6_TO_7.split(";"):
                        if statement.strip():
                            conn.execute(statement)
                    self._backfill_artifact_uids(conn)
                    conn.execute(f"PRAGMA user_version={CURRENT_SCHEMA_VERSION}")
                    self._backfill_job_metadata(conn)
                    conn.execute("COMMIT")
                except Exception as exc:
                    conn.rollback()
                    backup = f" Backup: {self.last_backup_path}" if self.last_backup_path else ""
                    raise RelayError("DATABASE_MIGRATION_FAILED", f"Database migration failed.{backup}") from exc
            elif version in {1, 2, 3, 4, 5, 6, 7, 8, 9}:
                self.last_backup_path = self._create_backup()
                try:
                    conn.execute("BEGIN")
                    if version == 1:
                        for statement in MIGRATION_1_TO_2.split(";"):
                            if statement.strip():
                                conn.execute(statement)
                    if version in {1, 2}:
                        for statement in MIGRATION_2_TO_3.split(";"):
                            if statement.strip():
                                conn.execute(statement)
                    if version in {1, 2, 3}:
                        for statement in MIGRATION_3_TO_4.split(";"):
                            if statement.strip():
                                conn.execute(statement)
                    for statement in MIGRATION_4_TO_5.split(";"):
                        if statement.strip():
                            conn.execute(statement)
                    for statement in MIGRATION_5_TO_6.split(";"):
                        if statement.strip():
                            try:
                                conn.execute(statement)
                            except sqlite3.OperationalError as exc:
                                if "duplicate column" not in str(exc):
                                    raise
                    for statement in MIGRATION_6_TO_7.split(";"):
                        if statement.strip():
                            try:
                                conn.execute(statement)
                            except sqlite3.OperationalError as exc:
                                if "duplicate column" not in str(exc):
                                    raise
                    for statement in MIGRATION_7_TO_8.split(";"):
                        if statement.strip():
                            try:
                                conn.execute(statement)
                            except sqlite3.OperationalError as exc:
                                if "duplicate column" not in str(exc):
                                    raise
                    for statement in MIGRATION_8_TO_9.split(";"):
                        if statement.strip():
                            try:
                                conn.execute(statement)
                            except sqlite3.OperationalError as exc:
                                if "duplicate column" not in str(exc):
                                    raise
                    for statement in MIGRATION_9_TO_10.split(";"):
                        if statement.strip():
                            try:
                                conn.execute(statement)
                            except sqlite3.OperationalError as exc:
                                if "duplicate column" not in str(exc):
                                    raise
                    self._backfill_artifact_uids(conn)
                    conn.execute(f"PRAGMA user_version={CURRENT_SCHEMA_VERSION}")
                    conn.execute("COMMIT")
                except Exception as exc:
                    conn.rollback()
                    backup = f" Backup: {self.last_backup_path}" if self.last_backup_path else ""
                    raise RelayError("DATABASE_MIGRATION_FAILED", f"Database migration failed.{backup}") from exc

            elif version == 7:
                self.last_backup_path = self._create_backup()
                try:
                    conn.execute("BEGIN")
                    for statement in MIGRATION_7_TO_8.split(";"):
                        if statement.strip():
                            try:
                                conn.execute(statement)
                            except sqlite3.OperationalError as exc:
                                if "duplicate column" not in str(exc):
                                    raise
                    for statement in MIGRATION_8_TO_9.split(";"):
                        if statement.strip():
                            try:
                                conn.execute(statement)
                            except sqlite3.OperationalError as exc:
                                if "duplicate column" not in str(exc):
                                    raise
                    for statement in MIGRATION_9_TO_10.split(";"):
                        if statement.strip():
                            try:
                                conn.execute(statement)
                            except sqlite3.OperationalError as exc:
                                if "duplicate column" not in str(exc):
                                    raise
                    conn.execute(f"PRAGMA user_version={CURRENT_SCHEMA_VERSION}")
                    conn.execute("COMMIT")
                except Exception as exc:
                    conn.rollback()
                    backup = f" Backup: {self.last_backup_path}" if self.last_backup_path else ""
                    raise RelayError("DATABASE_MIGRATION_FAILED", f"Database migration failed.{backup}") from exc
            if version == CURRENT_SCHEMA_VERSION:
                self._backfill_job_metadata(conn)
                self._ensure_search_tables(conn)
                return

    @staticmethod
    def _ensure_search_tables(conn: sqlite3.Connection) -> bool:
        if not Database._fts_available(conn):
            return False
        conn.execute(
            "CREATE VIRTUAL TABLE IF NOT EXISTS run_search USING fts5("
            "run_id UNINDEXED,title,task_text,summary,worker,profile,trigger_type)"
        )
        conn.execute(
            "CREATE VIRTUAL TABLE IF NOT EXISTS artifact_search USING fts5("
            "artifact_uid UNINDEXED,run_id UNINDEXED,name,role,mime_type,content_text)"
        )
        return True

    @staticmethod
    def _fts_available(conn: sqlite3.Connection) -> bool:
        try:
            conn.execute("CREATE VIRTUAL TABLE temp.relay_fts_probe USING fts5(value)")
            conn.execute("DROP TABLE temp.relay_fts_probe")
            return True
        except sqlite3.OperationalError:
            return False

    def search_capabilities(self) -> dict[str, bool]:
        with self.connect() as conn:
            return {"fts5_available": self._fts_available(conn)}

    def _run_index_values(self, conn: sqlite3.Connection, job_id: str) -> tuple[Any, ...] | None:
        row = conn.execute("SELECT * FROM jobs WHERE job_id=?", (job_id,)).fetchone()
        if not row:
            return None
        job = dict(row)
        summary = result_summary(job)
        task_text = job.get("task_text") or job.get("task_preview") or ""
        if not task_text and bool(job.get("replayable", 1)):
            try:
                request = json.loads(job.get("request_json") or "{}")
            except (TypeError, json.JSONDecodeError):
                request = {}
            if isinstance(request, dict):
                task_text = str(request.get("task") or "")
        return (
            job_id,
            job.get("title") or "" if bool(job.get("replayable", 1)) else "",
            task_text,
            summary or "",
            job.get("requested_worker") or "",
            job.get("profile") or "",
            job.get("trigger_type") or "manual",
        )

    def index_run(self, job_id: str) -> bool:
        with self.connect() as conn:
            if not self._ensure_search_tables(conn):
                return False
            values = self._run_index_values(conn, job_id)
            if values is None:
                return False
            conn.execute("DELETE FROM run_search WHERE run_id=?", (job_id,))
            conn.execute(
                "INSERT INTO run_search(run_id,title,task_text,summary,worker,profile,trigger_type) VALUES(?,?,?,?,?,?,?)",
                values,
            )
            return True

    def index_artifact(self, artifact_uid: str) -> bool:
        with self.connect() as conn:
            if not self._ensure_search_tables(conn):
                return False
            row = conn.execute("SELECT * FROM artifacts WHERE artifact_uid=?", (artifact_uid,)).fetchone()
            if not row:
                return False
            artifact = dict(row)
            content, _available = artifact_search_content(artifact, max_bytes=1024 * 1024)
            conn.execute("DELETE FROM artifact_search WHERE artifact_uid=?", (artifact_uid,))
            conn.execute(
                "INSERT INTO artifact_search(artifact_uid,run_id,name,role,mime_type,content_text) VALUES(?,?,?,?,?,?)",
                (
                    artifact_uid,
                    artifact["job_id"],
                    artifact.get("relative_path") or "",
                    artifact.get("role") or "output",
                    artifact_mime(artifact) or "",
                    content or "",
                ),
            )
            return True

    def rebuild_search_index(self) -> bool:
        with self.connect() as conn:
            if not self._ensure_search_tables(conn):
                return False
            conn.execute("DELETE FROM run_search")
            conn.execute("DELETE FROM artifact_search")
            for row in conn.execute("SELECT job_id FROM jobs ORDER BY job_id").fetchall():
                values = self._run_index_values(conn, row[0])
                if values:
                    conn.execute(
                        "INSERT INTO run_search(run_id,title,task_text,summary,worker,profile,trigger_type) VALUES(?,?,?,?,?,?,?)",
                        values,
                    )
            for row in conn.execute(
                "SELECT * FROM artifacts WHERE artifact_uid IS NOT NULL ORDER BY artifact_id"
            ).fetchall():
                artifact = dict(row)
                content, _available = artifact_search_content(artifact, max_bytes=1024 * 1024)
                conn.execute(
                    "INSERT INTO artifact_search(artifact_uid,run_id,name,role,mime_type,content_text) VALUES(?,?,?,?,?,?)",
                    (
                        artifact["artifact_uid"],
                        artifact["job_id"],
                        artifact.get("relative_path") or "",
                        artifact.get("role") or "output",
                        artifact_mime(artifact) or "",
                        content or "",
                    ),
                )
            return True

    def search_runs(
        self,
        query: str | None = None,
        *,
        status: str | None = None,
        worker: str | None = None,
        submitted_via: str | None = None,
        trigger_type: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        role: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        from .search import normalize_limit

        limit = normalize_limit(limit)
        with self.connect() as conn:
            if not self._fts_available(conn):
                raise RelayError("SEARCH_UNAVAILABLE", "SQLite FTS5 is not available.")
            where = ["1=1"]
            params: list[Any] = []
            join = ""
            if query:
                where.append("run_search MATCH ?")
                params.append(fts_query(query))
            if status:
                where.append("j.status=?")
                params.append(status.upper())
            if worker:
                where.append("(j.requested_worker=? OR j.actual_worker=?)")
                params.extend([worker, worker])
            if submitted_via:
                where.append("j.submitted_via=?")
                params.append(submitted_via)
            if trigger_type:
                where.append("j.trigger_type=?")
                params.append(trigger_type)
            if date_from:
                where.append("COALESCE(j.completed_at,j.created_at)>=?")
                params.append(date_from)
            if date_to:
                where.append("COALESCE(j.completed_at,j.created_at)<=?")
                params.append(date_to)
            if role:
                where.append("EXISTS (SELECT 1 FROM artifacts ar WHERE ar.job_id=j.job_id AND ar.role=?)")
                params.append(role)
            if query:
                select_rank = "bm25(run_search) AS relevance"
                join = "JOIN run_search ON run_search.run_id=j.job_id"
            else:
                select_rank = "0.0 AS relevance"
            sql = (
                f"SELECT j.*, {select_rank} FROM jobs j {join} WHERE {' AND '.join(where)} "
                "ORDER BY relevance ASC, COALESCE(j.completed_at,j.created_at) DESC, j.job_id DESC LIMIT ? OFFSET ?"
            )
            params.extend([limit, max(0, int(offset))])
            return [dict(row) for row in conn.execute(sql, params).fetchall()]

    def search_artifacts(
        self,
        query: str | None = None,
        *,
        role: str | None = None,
        mime_type: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        from .search import normalize_limit

        limit = normalize_limit(limit)
        with self.connect() as conn:
            if not self._fts_available(conn):
                raise RelayError("SEARCH_UNAVAILABLE", "SQLite FTS5 is not available.")
            where = ["1=1"]
            params: list[Any] = []
            join = ""
            if query:
                where.append("artifact_search MATCH ?")
                params.append(fts_query(query))
                join = "JOIN artifact_search ON artifact_search.artifact_uid=ar.artifact_uid"
            if role:
                where.append("ar.role=?")
                params.append(role)
            if mime_type:
                where.append("ar.mime_type=?")
                params.append(mime_type)
            if date_from:
                where.append("ar.created_at>=?")
                params.append(date_from)
            if date_to:
                where.append("ar.created_at<=?")
                params.append(date_to)
            select_rank = "bm25(artifact_search) AS relevance" if query else "0.0 AS relevance"
            sql = (
                f"SELECT ar.*, {select_rank} FROM artifacts ar {join} WHERE {' AND '.join(where)} "
                "ORDER BY relevance ASC, ar.created_at DESC, ar.artifact_id DESC LIMIT ? OFFSET ?"
            )
            params.extend([limit, max(0, int(offset))])
            return [dict(row) for row in conn.execute(sql, params).fetchall()]

    def search_has_more(self, kind: str, query: str | None, offset: int, **filters: Any) -> bool:
        rows = (
            self.search_runs(query, limit=1, offset=offset + 1, **filters)
            if kind == "runs"
            else self.search_artifacts(query, limit=1, offset=offset + 1, **filters)
        )
        return bool(rows)

    def artifact_content(self, artifact_uid: str, max_bytes: int) -> dict[str, Any]:
        from .search import normalize_max_bytes

        max_bytes = normalize_max_bytes(max_bytes)
        artifact = self.artifact_by_uid(artifact_uid)
        if not artifact:
            raise RelayError("ARTIFACT_NOT_FOUND", f"Artifact not found: {artifact_uid}")
        path = Path(str(artifact["final_path"]))
        if not path.is_file():
            return {"ok": True, "artifact_uid": artifact_uid, "available": False, "text": ""}
        content, available = artifact_search_content(artifact, max_bytes=max_bytes)
        if not available:
            return {"ok": True, "artifact_uid": artifact_uid, "available": False, "text": ""}
        raw = path.read_bytes()[:max_bytes]
        return {
            "ok": True,
            "artifact_uid": artifact_uid,
            "available": True,
            "text": content,
            "size": path.stat().st_size,
            "truncated": path.stat().st_size > len(raw),
            "mime_type": artifact_mime(artifact),
        }

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

    def _backfill_artifact_uids(self, conn: sqlite3.Connection) -> None:
        rows = conn.execute(
            "SELECT artifact_id FROM artifacts WHERE artifact_uid IS NULL ORDER BY artifact_id"
        ).fetchall()
        for row in rows:
            conn.execute("UPDATE artifacts SET artifact_uid=? WHERE artifact_id=?", (new_artifact_uid(), row[0]))

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

    def link_schedule_run_job(
        self,
        run_id: str,
        job_id: str,
        *,
        status: str = "QUEUED",
        output_path: str | None = None,
        artifact_path: str | None = None,
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE schedule_runs SET job_id=?,status=?,output_path=COALESCE(?,output_path),"
                "artifact_path=COALESCE(?,artifact_path),updated_at=? WHERE run_id=?",
                (job_id, status, output_path, artifact_path, utc_now(), run_id),
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
            "active": (
                "CREATED",
                "QUEUED",
                "PREPARING",
                "RUNNING",
                "VALIDATING",
                "DELIVERING",
                "CANCEL_REQUESTED",
            ),
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
                "OR COALESCE(json_extract(CASE WHEN json_valid(request_json) THEN request_json ELSE '{}' END, '$.task'), '') "
                "LIKE ? ESCAPE '\\' COLLATE NOCASE "
                "OR COALESCE(json_extract(CASE WHEN json_valid(request_json) THEN request_json ELSE '{}' END, '$.model'), '') "
                "LIKE ? ESCAPE '\\' COLLATE NOCASE "
                "OR EXISTS (SELECT 1 FROM json_each(CASE WHEN json_valid(request_json) THEN request_json ELSE '{}' END, '$.attachments') "
                "WHERE CAST(value AS TEXT) LIKE ? ESCAPE '\\' COLLATE NOCASE))"
            )
            params.extend([pattern] * 10)
        if date_from:
            where.append("COALESCE(completed_at, created_at)>=?")
            params.append(date_from)
        if date_to:
            where.append("COALESCE(completed_at, created_at)<=?")
            params.append(date_to)

        sort_expression = {
            "waiting": "created_at",
            "running": "COALESCE(started_at, created_at)",
            "active": "created_at",
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

    def artifact_by_uid(self, artifact_uid: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM artifacts WHERE artifact_uid=?", (artifact_uid,)).fetchone()
            return dict(row) if row else None

    def add_lineage(self, row: dict[str, Any]) -> int:
        values = {"created_at": utc_now(), "binding_mode": "snapshot", **row}
        keys = list(values)
        with self.connect() as conn:
            cursor = conn.execute(
                f"INSERT INTO artifact_lineage ({','.join(keys)}) VALUES ({','.join('?' for _ in keys)})",
                [values[key] for key in keys],
            )
            return int(cursor.lastrowid)

    def lineage_for_job(self, job_id: str) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM artifact_lineage WHERE consumer_job_id=? ORDER BY lineage_id", (job_id,)
            ).fetchall()
            return [dict(row) for row in rows]

    def lineage_for_artifact(self, artifact_uid: str) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM artifact_lineage WHERE source_artifact_uid=? ORDER BY lineage_id", (artifact_uid,)
            ).fetchall()
            return [dict(row) for row in rows]

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

    def create_task(self, row: dict[str, Any]) -> None:
        now = utc_now()
        values = {"version": 1, **row, "created_at": row.get("created_at", now), "updated_at": now}
        keys = list(values)
        with self.connect() as conn:
            conn.execute(
                f"INSERT INTO tasks ({','.join(keys)}) VALUES ({','.join('?' for _ in keys)})",
                [values[key] for key in keys],
            )

    def get_task(self, task_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM tasks WHERE task_id=?", (task_id,)).fetchone()
            return dict(row) if row else None

    def list_tasks(self, *, name: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        query = "SELECT * FROM tasks"
        params: list[Any] = []
        if name:
            query += " WHERE name LIKE ?"
            params.append(f"%{name}%")
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        with self.connect() as conn:
            return [dict(row) for row in conn.execute(query, params).fetchall()]

    def update_task(self, task_id: str, **changes: Any) -> None:
        if not changes:
            return
        task = self.get_task(task_id)
        if not task:
            raise RelayError("TASK_NOT_FOUND", f"Task not found: {task_id}")
        changes["version"] = task["version"] + 1
        changes["updated_at"] = utc_now()
        keys = list(changes)
        with self.connect() as conn:
            conn.execute(
                f"UPDATE tasks SET {','.join(f'{key}=?' for key in keys)} WHERE task_id=?",
                [changes[key] for key in keys] + [task_id],
            )

    def delete_task(self, task_id: str) -> bool:
        with self.connect() as conn:
            cur = conn.execute("DELETE FROM tasks WHERE task_id=?", (task_id,))
            return cur.rowcount > 0

    def runs_for_task(self, task_id: str, *, limit: int = 50) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM jobs WHERE task_id=? ORDER BY created_at DESC LIMIT ?",
                (task_id, limit),
            ).fetchall()
            return [dict(row) for row in rows]

    def create_project(self, row: dict[str, Any]) -> None:
        now = utc_now()
        values = {
            "version": 1,
            **row,
            "deleted_at": None,
            "created_at": row.get("created_at", now),
            "updated_at": row.get("updated_at", now),
        }
        keys = list(values)
        with self.connect() as conn:
            conn.execute(
                f"INSERT INTO projects ({','.join(keys)}) VALUES ({','.join('?' for _ in keys)})",
                [values[key] for key in keys],
            )

    def get_project(self, project_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM projects WHERE project_id=?", (project_id,)).fetchone()
            return dict(row) if row else None

    def list_projects(
        self, *, name: str | None = None, include_deleted: bool = False, limit: int = 50
    ) -> list[dict[str, Any]]:
        query = "SELECT * FROM projects"
        params: list[Any] = []
        where: list[str] = []
        if not include_deleted:
            where.append("deleted_at IS NULL")
        if name:
            where.append("name LIKE ?")
            params.append(f"%{name}%")
        if where:
            query += " WHERE " + " AND ".join(where)
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        with self.connect() as conn:
            return [dict(row) for row in conn.execute(query, params).fetchall()]

    def update_project(self, project_id: str, **changes: Any) -> None:
        if not changes:
            return
        existing = self.get_project(project_id)
        if not existing:
            raise RelayError("PROJECT_NOT_FOUND", f"Project not found: {project_id}")
        if existing.get("deleted_at") is not None:
            raise RelayError("PROJECT_NOT_FOUND", f"Project not found: {project_id}")
        changes["version"] = existing["version"] + 1
        changes["updated_at"] = utc_now()
        keys = list(changes)
        with self.connect() as conn:
            conn.execute(
                f"UPDATE projects SET {','.join(f'{key}=?' for key in keys)} WHERE project_id=?",
                [changes[key] for key in keys] + [project_id],
            )

    def soft_delete_project(self, project_id: str) -> bool:
        existing = self.get_project(project_id)
        if not existing:
            raise RelayError("PROJECT_NOT_FOUND", f"Project not found: {project_id}")
        if existing.get("deleted_at") is not None:
            return False
        with self.connect() as conn:
            conn.execute(
                "UPDATE projects SET deleted_at=?, version=version+1, updated_at=? WHERE project_id=?",
                (utc_now(), utc_now(), project_id),
            )
        return True

    def create_project_run(self, row: dict[str, Any]) -> None:
        now = utc_now()
        values = {"status": "accepted", **row, "created_at": row.get("created_at", now), "updated_at": now}
        keys = list(values)
        with self.connect() as conn:
            conn.execute(
                f"INSERT INTO project_runs ({','.join(keys)}) VALUES ({','.join('?' for _ in keys)})",
                [values[key] for key in keys],
            )

    def get_project_run(self, project_run_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM project_runs WHERE project_run_id=?", (project_run_id,)).fetchone()
            return dict(row) if row else None

    def update_project_run(self, project_run_id: str, **changes: Any) -> None:
        if not changes:
            return
        changes["updated_at"] = utc_now()
        keys = list(changes)
        with self.connect() as conn:
            conn.execute(
                f"UPDATE project_runs SET {','.join(f'{key}=?' for key in keys)} WHERE project_run_id=?",
                [changes[key] for key in keys] + [project_run_id],
            )

    def list_project_runs(
        self, *, project_id: str | None = None, status: str | None = None, limit: int = 50
    ) -> list[dict[str, Any]]:
        query = "SELECT * FROM project_runs"
        params: list[Any] = []
        where: list[str] = []
        if project_id:
            where.append("project_id=?")
            params.append(project_id)
        if status:
            where.append("status=?")
            params.append(status)
        if where:
            query += " WHERE " + " AND ".join(where)
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        with self.connect() as conn:
            return [dict(row) for row in conn.execute(query, params).fetchall()]

    def create_or_update_project_step(self, row: dict[str, Any]) -> dict[str, Any]:
        now = utc_now()
        values = {"updated_at": now, **row}
        keys = list(values)
        placeholders = ",".join("?" for _ in keys)
        update_clause = ",".join(f"{k}=excluded.{k}" for k in keys if k != "project_run_id" and k != "node_id")
        with self.connect() as conn:
            conn.execute(
                f"INSERT INTO project_run_steps ({','.join(keys)}) VALUES ({placeholders}) "
                f"ON CONFLICT(project_run_id, node_id) DO UPDATE SET {update_clause}",
                [values[k] for k in keys],
            )
        return self.get_project_step(row["project_run_id"], row["node_id"])

    def get_project_step(self, project_run_id: str, node_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM project_run_steps WHERE project_run_id=? AND node_id=?",
                (project_run_id, node_id),
            ).fetchone()
            return dict(row) if row else None

    def list_project_steps(self, project_run_id: str) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM project_run_steps WHERE project_run_id=? ORDER BY node_id",
                (project_run_id,),
            ).fetchall()
            return [dict(row) for row in rows]

    def update_project_step(self, project_run_id: str, node_id: str, **changes: Any) -> None:
        changes["updated_at"] = utc_now()
        keys = list(changes)
        with self.connect() as conn:
            conn.execute(
                f"UPDATE project_run_steps SET {','.join(f'{key}=?' for key in keys)} WHERE project_run_id=? AND node_id=?",
                [changes[key] for key in keys] + [project_run_id, node_id],
            )

    def append_project_step_run(
        self, project_run_id: str, node_id: str, task_run_id: str, worker_override: str | None
    ) -> int:
        with self.connect() as conn:
            existing = conn.execute("SELECT 1 FROM project_step_runs WHERE task_run_id=?", (task_run_id,)).fetchone()
            if existing:
                raise RelayError("STEP_RUN_DUPLICATE", f"Task Run already attached: {task_run_id}")
            attempt_row = conn.execute(
                "SELECT COALESCE(MAX(step_attempt), 0) + 1 FROM project_step_runs WHERE project_run_id=? AND node_id=?",
                (project_run_id, node_id),
            ).fetchone()
            attempt = int(attempt_row[0])
            conn.execute(
                "INSERT INTO project_step_runs (project_run_id, node_id, step_attempt, task_run_id, worker_override, status, created_at) "
                "VALUES (?, ?, ?, ?, ?, 'queued', ?)",
                (project_run_id, node_id, attempt, task_run_id, worker_override, utc_now()),
            )
            return attempt

    def list_project_step_runs(
        self, project_run_id: str | None = None, node_id: str | None = None, *, task_run_id: str | None = None
    ) -> list[dict[str, Any]]:
        query = "SELECT * FROM project_step_runs"
        params: list[Any] = []
        where: list[str] = []
        if project_run_id:
            where.append("project_run_id=?")
            params.append(project_run_id)
        if node_id:
            where.append("node_id=?")
            params.append(node_id)
        if task_run_id:
            where.append("task_run_id=?")
            params.append(task_run_id)
        if where:
            query += " WHERE " + " AND ".join(where)
        query += " ORDER BY project_run_id, node_id, step_attempt"
        with self.connect() as conn:
            return [dict(row) for row in conn.execute(query, params).fetchall()]

    def claim_ready_steps(self, project_run_id: str, runnable: str, claimed: str) -> list[tuple[str, str]]:
        claimed_list: list[tuple[str, str]] = []
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                rows = conn.execute(
                    "SELECT project_run_id, node_id FROM project_run_steps WHERE project_run_id=? AND status=?",
                    (project_run_id, runnable),
                ).fetchall()
                for row in rows:
                    cur = conn.execute(
                        "UPDATE project_run_steps SET status=?, updated_at=? "
                        "WHERE project_run_id=? AND node_id=? AND status=?",
                        (claimed, utc_now(), row["project_run_id"], row["node_id"], runnable),
                    )
                    if cur.rowcount > 0:
                        claimed_list.append((row["project_run_id"], row["node_id"]))
                conn.execute("COMMIT")
            except Exception:
                conn.rollback()
                raise
        return claimed_list

    def create_routine(self, row):
        from .util import utc_now

        now = utc_now()
        values = {
            "enabled": 1,
            "deleted_at": None,
            "missed_grace_seconds": 43200,
            "overlap_policy": "skip",
            "missed_policy": "skip",
            "version_policy": "latest",
            **row,
            "created_at": row.get("created_at", now),
            "updated_at": row.get("updated_at", now),
        }
        keys = list(values)
        with self.connect() as conn:
            conn.execute(
                f"INSERT INTO routines ({','.join(keys)}) VALUES ({','.join('?' for _ in keys)})",
                [values[key] for key in keys],
            )

    def get_routine(self, routine_id):
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM routines WHERE routine_id=?", (routine_id,)).fetchone()
            return dict(row) if row else None

    def list_routines(self, *, include_deleted=False, name=None, limit=200):
        query = "SELECT * FROM routines"
        params = []
        where = []
        if not include_deleted:
            where.append("deleted_at IS NULL")
        if name:
            where.append("name LIKE ?")
            params.append(f"%{name}%")
        if where:
            query += " WHERE " + " AND ".join(where)
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        with self.connect() as conn:
            return [dict(row) for row in conn.execute(query, params).fetchall()]

    def update_routine(self, routine_id, **changes):
        from .util import utc_now

        if not changes:
            return
        changes["updated_at"] = utc_now()
        keys = list(changes)
        with self.connect() as conn:
            conn.execute(
                f"UPDATE routines SET {','.join(f'{key}=?' for key in keys)} WHERE routine_id=?",
                [changes[key] for key in keys] + [routine_id],
            )

    def soft_delete_routine(self, routine_id):
        from .util import utc_now

        existing = self.get_routine(routine_id)
        if not existing:
            from .errors import RelayError

            raise RelayError("ROUTINE_NOT_FOUND", f"Routine not found: {routine_id}")
        if existing.get("deleted_at") is not None:
            return False
        with self.connect() as conn:
            conn.execute(
                "UPDATE routines SET deleted_at=?, updated_at=? WHERE routine_id=?",
                (utc_now(), utc_now(), routine_id),
            )
        return True

    def claim_routine_occurrence(self, routine_id, run_row):
        import sqlite3 as _sq

        from .util import utc_now

        now = utc_now()
        values = {**run_row, "routine_id": routine_id, "created_at": now, "updated_at": now}
        keys = list(values)
        with self.connect() as conn:
            try:
                conn.execute(
                    f"INSERT INTO routine_runs ({','.join(keys)}) VALUES ({','.join('?' for _ in keys)})",
                    [values[key] for key in keys],
                )
            except _sq.IntegrityError:
                return False
        return True

    def get_routine_run(self, run_id):
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM routine_runs WHERE run_id=?", (run_id,)).fetchone()
            return dict(row) if row else None

    def update_routine_run(self, run_id, **changes):
        from .util import utc_now

        if not changes:
            return
        changes["updated_at"] = utc_now()
        keys = list(changes)
        with self.connect() as conn:
            conn.execute(
                f"UPDATE routine_runs SET {','.join(f'{key}=?' for key in keys)} WHERE run_id=?",
                [changes[key] for key in keys] + [run_id],
            )

    def list_routine_runs(self, *, routine_id=None, status=None, limit=100):
        query = "SELECT * FROM routine_runs"
        params = []
        where = []
        if routine_id:
            where.append("routine_id=?")
            params.append(routine_id)
        if status:
            where.append("status=?")
            params.append(status)
        if where:
            query += " WHERE " + " AND ".join(where)
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        with self.connect() as conn:
            return [dict(row) for row in conn.execute(query, params).fetchall()]

    def active_runs_for_routine(self, routine_id):
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM routine_runs WHERE routine_id=? AND status NOT IN ('completed', 'failed', 'cancelled', 'skipped')",
                (routine_id,),
            ).fetchall()
            return [dict(row) for row in rows]

    def create_approval(self, row: dict[str, Any]) -> None:
        now = utc_now()
        values = {
            "status": "pending",
            "reviewer": None,
            "reason": None,
            "edited_artifact_uid": None,
            "decided_at": None,
            **row,
            "created_at": row.get("created_at", now),
        }
        keys = list(values)
        with self.connect() as conn:
            conn.execute(
                f"INSERT INTO approvals ({','.join(keys)}) VALUES ({','.join('?' for _ in keys)})",
                [values[key] for key in keys],
            )

    def get_approval(self, token: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM approvals WHERE token=?", (token,)).fetchone()
            return dict(row) if row else None

    def list_approvals(self, project_run_id: str) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM approvals WHERE project_run_id=? ORDER BY created_at",
                (project_run_id,),
            ).fetchall()
            return [dict(row) for row in rows]

    def update_approval(self, token: str, **changes: Any) -> None:
        if not changes:
            return
        keys = list(changes)
        with self.connect() as conn:
            conn.execute(
                f"UPDATE approvals SET {','.join(f'{key}=?' for key in keys)} WHERE token=?",
                [changes[key] for key in keys] + [token],
            )

    def create_delivery(self, row: dict[str, Any]) -> None:
        now = utc_now()
        values = {
            "kind": "folder",
            "status": "completed",
            "error": None,
            "approval_id": None,
            **row,
            "created_at": row.get("created_at", now),
        }
        keys = list(values)
        with self.connect() as conn:
            conn.execute(
                f"INSERT INTO deliveries ({','.join(keys)}) VALUES ({','.join('?' for _ in keys)})",
                [values[key] for key in keys],
            )

    def list_deliveries(self, project_run_id: str) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM deliveries WHERE project_run_id=? ORDER BY created_at",
                (project_run_id,),
            ).fetchall()
            return [dict(row) for row in rows]

    def create_notification_event(self, row: dict[str, Any]) -> None:
        now = utc_now()
        values = {
            "status": "delivered",
            "status_code": None,
            "attempt": 1,
            "error": None,
            "payload_hash": None,
            "routine_id": None,
            "project_run_id": None,
            **row,
            "created_at": row.get("created_at", now),
        }
        keys = list(values)
        with self.connect() as conn:
            conn.execute(
                f"INSERT INTO notification_events ({','.join(keys)}) VALUES ({','.join('?' for _ in keys)})",
                [values[key] for key in keys],
            )

    def list_notification_events(
        self, *, routine_id: str | None = None, project_run_id: str | None = None, limit: int = 100
    ) -> list[dict[str, Any]]:
        query = "SELECT * FROM notification_events"
        params: list[Any] = []
        where: list[str] = []
        if routine_id:
            where.append("routine_id=?")
            params.append(routine_id)
        if project_run_id:
            where.append("project_run_id=?")
            params.append(project_run_id)
        if where:
            query += " WHERE " + " AND ".join(where)
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        with self.connect() as conn:
            return [dict(row) for row in conn.execute(query, params).fetchall()]
