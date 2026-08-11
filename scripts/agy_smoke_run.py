"""One-shot real antigravity Task Run smoke test."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path

from relay.config import Config
from relay.db import Database
from relay.doctor import Doctor
from relay.engine import RelayEngine
from relay.models import JobRequest, TaskSpec

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    agy = shutil.which("agy") or str(Path.home() / "AppData/Local/agy/bin/agy.exe")
    temp = tempfile.TemporaryDirectory()
    home = Path(temp.name) / "relay-home"
    print("home", home, flush=True)
    print("agy", agy, flush=True)
    os.environ["PATH"] = str(Path(agy).parent) + os.pathsep + os.environ.get("PATH", "")
    config = Config(home)
    config.init()
    config.set("workers.antigravity.command", agy)
    config.set("workers.antigravity.enabled", True)
    config.set("workers.antigravity.security_verified", True)
    config.set("workers.antigravity.full_access_mode", True)
    config.set("workers.antigravity.require_deep_doctor", True)
    config.set("workers.claude.enabled", False)
    config.set("workers.codex.enabled", False)
    config.set("service_isolation_acknowledged", True)
    config.set("default_worker", "antigravity")
    config.set("fallback_enabled", False)
    config.set("soft_stall_seconds", 300)
    config.set("hard_stall_seconds", 900)
    config.set("timeout_seconds", 600)
    config.set("poll_interval_seconds", 2)
    db = Database(config.path_value("database_path"))
    engine = RelayEngine(config, db)
    audit = Doctor(config, db).audit(["antigravity"], deep=True)
    print("doctor", json.dumps(audit, ensure_ascii=False)[:500], flush=True)
    if not audit.get("ok"):
        return 2
    task = engine.create_task(
        TaskSpec(
            name="agy smoke",
            instructions=(
                "Write Relay JSON result with status complete, answer containing SMOKEAGY, "
                "empty sources/uncertainties/missing_items, and one utf-8 artifact notes.txt content SMOKEAGY."
            ),
            task_summary="smoke",
            default_worker="antigravity",
            fallback_enabled=False,
            result_format="json",
        )
    )
    job, _, _ = engine.run_task(
        task["task_id"],
        request=JobRequest(task="", worker="antigravity", fallback=False, result_format="json", force_new=True),
        queued=True,
        submitted_via="cli",
    )
    print("job", job["job_id"], flush=True)
    try:
        receipt = engine.execute_job(job["job_id"])
    except Exception as exc:
        print("execute exception", type(exc), exc, flush=True)
        # dump logs if present
        for path in sorted((config.path_value("workspace_root")).rglob("*")):
            if path.is_file() and path.suffix in {".log", ".json", ".partial", ".md"}:
                print("FILE", path, "size", path.stat().st_size, flush=True)
        raise
    print(json.dumps(receipt, ensure_ascii=False, indent=2, default=str)[:2000])
    ws = config.path_value("workspace_root")
    for path in sorted(ws.rglob("*")):
        if path.is_file() and path.name in {"stdout.log", "stderr.log", "command.json"}:
            print("---", path, flush=True)
            print(path.read_text(encoding="utf-8", errors="replace")[:1500], flush=True)
    temp.cleanup()
    return 0 if receipt.get("status") in {"completed", "partial"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
