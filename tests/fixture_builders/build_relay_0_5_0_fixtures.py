"""Build deterministic SQLite fixtures from the Relay 0.5.0 schema."""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

LEGACY_SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;
CREATE TABLE jobs (
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
CREATE UNIQUE INDEX idx_jobs_request_id ON jobs(request_id) WHERE request_id IS NOT NULL;
CREATE INDEX idx_jobs_task_hash_created ON jobs(task_hash, created_at);
CREATE INDEX idx_jobs_status ON jobs(status);

CREATE TABLE attempts (
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
CREATE INDEX idx_attempts_job ON attempts(job_id);

CREATE TABLE artifacts (
    artifact_id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT NOT NULL REFERENCES jobs(job_id) ON DELETE CASCADE,
    relative_path TEXT NOT NULL,
    final_path TEXT NOT NULL,
    mime_type TEXT,
    size INTEGER NOT NULL,
    sha256 TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE events (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT NOT NULL REFERENCES jobs(job_id) ON DELETE CASCADE,
    timestamp TEXT NOT NULL,
    event_type TEXT NOT NULL,
    payload_json TEXT
);
CREATE INDEX idx_events_job ON events(job_id, event_id);

CREATE TABLE capability_audits (
    audit_id INTEGER PRIMARY KEY AUTOINCREMENT,
    worker TEXT NOT NULL,
    version TEXT,
    audit_time TEXT NOT NULL,
    test_name TEXT NOT NULL,
    result TEXT NOT NULL,
    details_json TEXT,
    spec_hash TEXT
);
CREATE INDEX idx_audits_worker ON capability_audits(worker, audit_time);
"""


def _insert_populated_data(conn: sqlite3.Connection) -> None:
    jobs = [
        (
            "fixture-completed",
            "fixture-request-1",
            "human",
            "hash-completed",
            "Research the semiconductor market",
            "codex",
            "codex",
            "json",
            "web-research",
            "D:/RelayFixture/results/completed.json",
            "D:/RelayFixture/artifacts/fixture-completed",
            "COMPLETED",
            "complete",
            None,
            None,
            0,
            json.dumps({"task": "Research the semiconductor market", "worker": "codex"}),
            json.dumps({"ok": True, "status": "completed", "job_id": "fixture-completed"}),
            "2026-07-22T09:00:00+00:00",
            "2026-07-22T09:00:01+00:00",
            "2026-07-22T09:01:00+00:00",
            "2026-07-22T09:01:00+00:00",
        ),
        (
            "fixture-failed",
            None,
            "hermes",
            "hash-failed",
            None,
            "claude",
            None,
            "txt",
            "web-research",
            "D:/RelayFixture/results/failed.txt",
            "D:/RelayFixture/artifacts/fixture-failed",
            "FAILED",
            "failed",
            "AUTH_REQUIRED",
            "Authentication is required",
            1,
            json.dumps({"task": "Check the protected report", "worker": "claude"}),
            json.dumps({"ok": False, "status": "failed", "job_id": "fixture-failed"}),
            "2026-07-22T10:00:00+00:00",
            "2026-07-22T10:00:01+00:00",
            "2026-07-22T10:00:05+00:00",
            "2026-07-22T10:00:05+00:00",
        ),
        (
            "fixture-cancelled",
            "fixture-request-3",
            "human",
            "hash-cancelled",
            "Review the cancelled task",
            "codex",
            None,
            "json",
            "web-research",
            "D:/RelayFixture/results/cancelled.json",
            "D:/RelayFixture/artifacts/fixture-cancelled",
            "CANCELLED",
            "cancelled",
            "CANCELLED",
            None,
            0,
            json.dumps({"task": "Review the cancelled task", "worker": "codex"}),
            json.dumps({"ok": False, "status": "cancelled", "job_id": "fixture-cancelled"}),
            "2026-07-22T11:00:00+00:00",
            "2026-07-22T11:00:01+00:00",
            "2026-07-22T11:00:02+00:00",
            "2026-07-22T11:00:02+00:00",
        ),
    ]
    conn.executemany(
        "INSERT INTO jobs(job_id,request_id,caller,task_hash,task_text,requested_worker,actual_worker,format,profile,"
        "output_path,artifact_path,status,result_status,error_code,error_message,fallback_enabled,request_json,"
        "receipt_json,created_at,started_at,completed_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        jobs,
    )
    conn.executemany(
        "INSERT INTO attempts(job_id,worker,worker_version,adapter_spec_hash,permission_mode,sandbox_mode,"
        "unattended_verified,started_at,completed_at,exit_code,status,failure_code,failure_message,"
        "stdout_path,stderr_path,command_json,fallback_reason) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [
            (
                "fixture-completed",
                "codex",
                "0.144.1",
                "fixture-spec-hash",
                "never",
                "workspace-write",
                1,
                "2026-07-22T09:00:01+00:00",
                "2026-07-22T09:01:00+00:00",
                0,
                "COMPLETED",
                None,
                None,
                "D:/RelayFixture/logs/completed.stdout",
                "D:/RelayFixture/logs/completed.stderr",
                json.dumps(["codex", "--model", "o4-mini"]),
                None,
            ),
            (
                "fixture-failed",
                "claude",
                "2.1.217",
                "fixture-claude-hash",
                "bypassPermissions",
                None,
                0,
                "2026-07-22T10:00:01+00:00",
                "2026-07-22T10:00:05+00:00",
                1,
                "FAILED",
                "AUTH_REQUIRED",
                "Authentication is required",
                "D:/RelayFixture/logs/failed.stdout",
                "D:/RelayFixture/logs/failed.stderr",
                json.dumps(["claude", "--output-format", "json"]),
                None,
            ),
            (
                "fixture-failed",
                "codex",
                "0.144.1",
                "fixture-spec-hash",
                "never",
                "workspace-write",
                1,
                "2026-07-22T10:00:06+00:00",
                "2026-07-22T10:00:07+00:00",
                1,
                "FAILED",
                "TIMEOUT",
                "Timed out",
                "D:/RelayFixture/logs/fallback.stdout",
                "D:/RelayFixture/logs/fallback.stderr",
                json.dumps(["codex"]),
                "AUTH_REQUIRED",
            ),
        ],
    )
    conn.execute(
        "INSERT INTO artifacts(job_id,relative_path,final_path,mime_type,size,sha256,created_at) VALUES(?,?,?,?,?,?,?)",
        (
            "fixture-completed",
            "summary.txt",
            "D:/RelayFixture/artifacts/fixture-completed/summary.txt",
            "text/plain",
            14,
            "fixture-artifact-sha256",
            "2026-07-22T09:01:00+00:00",
        ),
    )
    conn.executemany(
        "INSERT INTO events(job_id,timestamp,event_type,payload_json) VALUES(?,?,?,?)",
        [
            ("fixture-completed", "2026-07-22T09:00:00+00:00", "JOB_CREATED", '{"queued":false}'),
            ("fixture-completed", "2026-07-22T09:00:01+00:00", "ATTEMPT_STARTED", '{"worker":"codex"}'),
            ("fixture-failed", "2026-07-22T10:00:00+00:00", "JOB_CREATED", '{"queued":false}'),
            ("fixture-cancelled", "2026-07-22T11:00:02+00:00", "JOB_CANCELLED", None),
        ],
    )
    conn.execute(
        "INSERT INTO capability_audits(worker,version,audit_time,test_name,result,details_json,spec_hash) "
        "VALUES(?,?,?,?,?,?,?)",
        (
            "codex",
            "0.144.1",
            "2026-07-22T08:00:00+00:00",
            "deep",
            "passed",
            '{"output":true,"artifacts":true}',
            "fixture-spec-hash",
        ),
    )


def build(path: Path, populated: bool, force: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if not force:
            raise FileExistsError(f"Refusing to overwrite existing fixture: {path}")
        path.unlink()
    conn = sqlite3.connect(path)
    try:
        conn.executescript(LEGACY_SCHEMA)
        if populated:
            _insert_populated_data(conn)
        conn.execute("PRAGMA user_version=0")
        conn.commit()
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1] / "fixtures"
    build(root / "relay-0.5.0-empty.db", populated=False, force=args.force)
    build(root / "relay-0.5.0-populated.db", populated=True, force=args.force)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
