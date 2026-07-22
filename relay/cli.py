from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from . import __version__
from .cleanup import CleanupManager
from .config import Config
from .daemon import RelayDaemon
from .db import Database
from .doctor import Doctor
from .engine import RelayEngine
from .errors import RelayError
from .models import JobRequest
from .rpc import RPCClient
from .security import security_posture
from .util import entrypoint_command, json_load, safe_resolve


COMMANDS = {
    "run", "submit", "status", "wait", "result", "show", "logs", "cancel", "history", "rerun",
    "doctor", "config", "cleanup", "daemon", "version", "init", "security", "models", "model-check"
}


def _preprocess(argv: list[str]) -> list[str]:
    if not argv:
        return argv
    if argv[0] not in COMMANDS and not argv[0].startswith("-"):
        return ["run", *argv]
    return argv


def _add_request_args(parser: argparse.ArgumentParser, task_required: bool = False) -> None:
    parser.add_argument("task", nargs=None if task_required else "?", default="")
    parser.add_argument("--task-file")
    parser.add_argument("--worker", choices=["auto", "claude", "codex", "antigravity"], default="auto")
    parser.add_argument("--fallback", action="store_true", default=None)
    parser.add_argument("--no-fallback", action="store_false", dest="fallback")
    parser.add_argument("--format", dest="result_format", choices=["json", "txt"])
    parser.add_argument("--out", dest="output_path")
    parser.add_argument("--artifacts", dest="artifact_path")
    parser.add_argument("--profile")
    parser.add_argument("--timeout", dest="timeout_seconds", type=int)
    parser.add_argument("--caller", default="human")
    parser.add_argument("--request-id")
    parser.add_argument("--attach", action="append", default=[], dest="attachments")
    parser.add_argument("--workspace")
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--force-new", action="store_true")
    parser.add_argument("--model")
    parser.add_argument("--machine", action="store_true")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="relay", description="Reliable delegation broker for AI CLIs")
    parser.add_argument("--version", action="version", version=f"Relay {__version__}")
    sub = parser.add_subparsers(dest="command")

    run = sub.add_parser("run", help="Run a task synchronously")
    _add_request_args(run)

    submit = sub.add_parser("submit", help="Submit a background task to the daemon")
    _add_request_args(submit)

    for name in ("status", "result", "show", "logs", "cancel", "rerun"):
        p = sub.add_parser(name)
        p.add_argument("job_id")
        p.add_argument("--machine", action="store_true")

    wait = sub.add_parser("wait")
    wait.add_argument("job_id")
    wait.add_argument("--timeout", type=int, default=0)
    wait.add_argument("--interval", type=float, default=2.0)
    wait.add_argument("--machine", action="store_true")

    history = sub.add_parser("history")
    history.add_argument("--status")
    history.add_argument("--limit", type=int, default=50)
    history.add_argument("--machine", action="store_true")

    doctor = sub.add_parser("doctor")
    doctor.add_argument("--worker", choices=["claude", "codex", "antigravity"])
    doctor.add_argument("--deep", action="store_true")
    doctor.add_argument("--machine", action="store_true")

    config = sub.add_parser("config")
    config_sub = config.add_subparsers(dest="config_command", required=True)
    show_p = config_sub.add_parser("show")
    show_p.add_argument("--machine", action="store_true")
    set_p = config_sub.add_parser("set")
    set_p.add_argument("key")
    set_p.add_argument("value")
    set_p.add_argument("--machine", action="store_true")
    for action in ("enable-worker", "disable-worker"):
        p = config_sub.add_parser(action)
        p.add_argument("worker", choices=["claude", "codex", "antigravity"])
        p.add_argument("--machine", action="store_true")

    cleanup = sub.add_parser("cleanup")
    cleanup.add_argument("--days", type=int)
    cleanup.add_argument("--dry-run", action="store_true")
    cleanup.add_argument("--status", action="store_true", help="Show automatic cleanup policy and last run")
    cleanup.add_argument("--machine", action="store_true")

    daemon = sub.add_parser("daemon")
    daemon_sub = daemon.add_subparsers(dest="daemon_command", required=True)
    for name in ("serve", "start", "stop", "status"):
        dp = daemon_sub.add_parser(name)
        dp.add_argument("--machine", action="store_true")

    init = sub.add_parser("init")
    init.add_argument("--force", action="store_true")
    init.add_argument("--machine", action="store_true")

    security = sub.add_parser("security")
    security.add_argument("--machine", action="store_true")
    
    models = sub.add_parser("models", help="List models available to installed workers")
    models.add_argument("--worker", choices=["all", "claude", "codex", "antigravity"], default="all")
    models.add_argument("--refresh", action="store_true")
    models.add_argument("--include-hidden", action="store_true")
    models.add_argument("--verify", action="store_true")
    models.add_argument("--machine", action="store_true")

    model_check = sub.add_parser("model-check", help="Verify that a worker can run a specific model")
    model_check.add_argument("--worker", required=True, choices=["claude", "codex", "antigravity"])
    model_check.add_argument("--model", required=True)
    model_check.add_argument("--machine", action="store_true")

    sub.add_parser("version")
    return parser


def _request_from_args(args, config: Config) -> JobRequest:
    return JobRequest(
        task=args.task or "",
        task_file=args.task_file,
        worker=args.worker,
        fallback=args.fallback,
        result_format=args.result_format or str(config.get("default_format", "json")),
        output_path=args.output_path,
        artifact_path=args.artifact_path,
        profile=args.profile or str(config.get("default_profile", "web-research")),
        timeout_seconds=args.timeout_seconds,
        caller=args.caller,
        request_id=args.request_id,
        attachments=args.attachments,
        workspace=args.workspace,
        overwrite=args.overwrite,
        machine=args.machine,
        force_new=args.force_new,
        model=args.model,
    )


def _emit(value: Any, machine: bool = False) -> None:
    if machine:
        print(json.dumps(value, ensure_ascii=False, separators=(",", ":")))
        return
    if isinstance(value, dict):
        if value.get("ok") and value.get("status") in {"completed", "partial"}:
            print(f"Status: {value.get('status')}")
            print(f"Job: {value.get('job_id')}")
            if value.get("worker"):
                print(f"Worker: {value.get('worker')}")
            if value.get("result_path"):
                print(f"Result: {value.get('result_path')}")
            if value.get("artifact_path"):
                print(f"Artifacts: {value.get('artifact_path')}")
            if value.get("uncertainties_count"):
                print(f"Uncertainties: {value.get('uncertainties_count')}")
            return
        if value.get("status") in {"queued", "running", "created", "reused"}:
            print(f"Status: {value.get('status')}")
            print(f"Job: {value.get('job_id')}")
            return
    print(json.dumps(value, ensure_ascii=False, indent=2, default=str))


def _receipt_exit_code(value: Any) -> int:
    if not isinstance(value, dict):
        return 0
    if value.get("ok") is False or str(value.get("status", "")).lower() in {"failed", "cancelled"}:
        return 2
    return 0


def _parse_config_value(text: str) -> Any:
    lower = text.lower()
    if lower in {"true", "false"}:
        return lower == "true"
    if "," in text:
        return [item.strip() for item in text.split(",") if item.strip()]
    try:
        return int(text)
    except ValueError:
        pass
    try:
        return float(text)
    except ValueError:
        return text


def _start_daemon(config: Config) -> dict:
    client = RPCClient(config)
    if client.health():
        return {"ok": True, "status": "already_running"}
    runtime = config.path_value("runtime_root")
    runtime.mkdir(parents=True, exist_ok=True)
    log_path = runtime / "daemon.log"
    command = entrypoint_command(["daemon", "serve"])
    log_handle = log_path.open("ab")
    kwargs: dict[str, Any] = {
        "stdin": subprocess.DEVNULL,
        "stdout": log_handle,
        "stderr": subprocess.STDOUT,
        "cwd": str(config.home),
        "env": {**os.environ, "RELAY_HOME": str(config.home)},
    }
    if command[1:3] == ["-m", "relay"]:
        source_root = Path(__file__).resolve().parent.parent
        if (source_root / "relay").is_dir():
            inherited = kwargs["env"].get("PYTHONPATH", "")
            paths = [str(source_root), *([inherited] if inherited else [])]
            kwargs["env"]["PYTHONPATH"] = os.pathsep.join(paths)
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    try:
        subprocess.Popen(command, **kwargs)
    finally:
        log_handle.close()
    client = RPCClient(config)
    if not client.wait_until_healthy(12):
        raise RelayError("DAEMON_UNAVAILABLE", f"Daemon did not start. See {log_path}")
    return {"ok": True, "status": "started", "log_path": str(log_path)}


def _ensure_daemon(config: Config) -> RPCClient:
    client = RPCClient(config)
    if client.health():
        return client
    if not config.get("daemon_auto_start", True):
        raise RelayError("DAEMON_UNAVAILABLE", "Daemon is not running")
    _start_daemon(config)
    return RPCClient(config)


def _logs(engine: RelayEngine, job_id: str) -> dict:
    job = engine.db.get_job(job_id)
    if not job:
        raise RelayError("JOB_NOT_FOUND", f"Job not found: {job_id}")
    attempts = engine.db.attempts_for_job(job_id)
    result = []
    for attempt in attempts:
        item = {k: attempt.get(k) for k in ("attempt_id", "worker", "status", "failure_code", "stdout_path", "stderr_path")}
        for key in ("stdout_path", "stderr_path"):
            path = attempt.get(key)
            if path and Path(path).exists():
                text = Path(path).read_text(encoding="utf-8", errors="replace")
                item[key.replace("_path", "_tail")] = text[-8000:]
        result.append(item)
    return {"ok": True, "job_id": job_id, "attempts": result}


def main(argv: list[str] | None = None) -> int:
    argv = _preprocess(list(sys.argv[1:] if argv is None else argv))
    parser = build_parser()
    args = parser.parse_args(argv)
    config = Config()
    config.init()
    db = Database(config.path_value("database_path"))
    engine = RelayEngine(config, db)
    machine = bool(getattr(args, "machine", False))
    try:
        # Sync-only users still receive automatic retention cleanup when new work arrives.
        if args.command in {"run", "submit"}:
            cleanup_manager = CleanupManager(config, db)
            if cleanup_manager.due():
                cleanup_manager.run()
        if args.command in (None, "version"):
            _emit({"ok": True, "version": __version__}, machine)
        elif args.command == "init":
            _emit({"ok": True, "config": str(config.init(force=args.force)), "home": str(config.home)}, machine)
        elif args.command == "run":
            result = engine.run(_request_from_args(args, config))
            _emit(result, machine)
            return _receipt_exit_code(result)
        elif args.command == "submit":
            client = _ensure_daemon(config)
            request = _request_from_args(args, config)
            _emit(client.request("POST", "/submit", request.to_dict()), machine)
        elif args.command in {"status", "result", "show"}:
            try:
                client = RPCClient(config)
                value = client.request("GET", f"/{args.command}/{args.job_id}") if client.health() else (
                    engine.show(args.job_id) if args.command == "show" else engine.receipt(args.job_id)
                )
            except RelayError:
                value = engine.show(args.job_id) if args.command == "show" else engine.receipt(args.job_id)
            _emit(value, machine)
            if args.command in {"status", "result"}:
                return _receipt_exit_code(value)
        elif args.command == "wait":
            deadline = time.monotonic() + args.timeout if args.timeout else None
            while True:
                value = engine.receipt(args.job_id)
                if value.get("status") in {"completed", "partial", "failed", "cancelled"}:
                    _emit(value, machine)
                    return _receipt_exit_code(value)
                if deadline and time.monotonic() >= deadline:
                    raise RelayError("TIMEOUT", "Wait command timed out", True)
                time.sleep(args.interval)
        elif args.command == "cancel":
            client = _ensure_daemon(config)
            _emit(client.request("POST", f"/cancel/{args.job_id}"), machine)
        elif args.command == "logs":
            _emit(_logs(engine, args.job_id), machine)
        elif args.command == "history":
            _emit({"ok": True, "jobs": db.list_jobs(args.status, args.limit)}, machine)
        elif args.command == "rerun":
            _emit(engine.rerun(args.job_id), machine)
        elif args.command == "doctor":
            workers = [args.worker] if args.worker else ["claude", "codex", "antigravity"]
            _emit(Doctor(config, db).audit(workers, deep=args.deep), machine)
        elif args.command == "config":
            if args.config_command == "show":
                _emit({"ok": True, "config_path": str(config.path), "config": config.data}, machine)
            elif args.config_command == "set":
                config.set(args.key, _parse_config_value(args.value))
                _emit({"ok": True, "key": args.key, "value": config.get(args.key)}, machine)
            elif args.config_command in {"enable-worker", "disable-worker"}:
                enable = args.config_command == "enable-worker"
                if enable:
                    adapter = __import__("relay.adapters", fromlist=["get_adapter"]).get_adapter(
                        args.worker, config.worker(args.worker), config.path_value("adapter_spec_root")
                    )
                    adapter.require_verified()
                    if args.worker == "antigravity" and not config.get("workers.antigravity.security_verified", False):
                        raise RelayError(
                            "PERMISSION_BLOCKED",
                            "Antigravity activation requires OS isolation review. "
                            "After verifying the dedicated account and ACLs, run: "
                            "relay config set workers.antigravity.security_verified true",
                        )
                config.set(f"workers.{args.worker}.enabled", enable)
                _emit({"ok": True, "worker": args.worker, "enabled": enable}, machine)
        elif args.command == "cleanup":
            manager = CleanupManager(config, db)
            if args.status:
                _emit({"ok": True, **manager.status()}, machine)
            else:
                _emit(manager.run(override_days=args.days, dry_run=args.dry_run), machine)
        elif args.command == "daemon":
            if args.daemon_command == "serve":
                RelayDaemon(config).serve()
                return 0
            if args.daemon_command == "start":
                _emit(_start_daemon(config), machine)
            elif args.daemon_command == "status":
                client = RPCClient(config)
                _emit(client.request("GET", "/health") if client.health() else {"ok": False, "status": "stopped"}, machine)
            elif args.daemon_command == "stop":
                client = RPCClient(config)
                _emit(client.request("POST", "/shutdown") if client.health() else {"ok": True, "status": "already_stopped"}, machine)
        elif args.command == "security":
            _emit({"ok": True, **security_posture(config)}, machine)
        elif args.command == "models":
            from .model_discovery import get_model_catalog
            workers = ["claude", "codex", "antigravity"] if args.worker == "all" else [args.worker]
            results = []
            
            for w in workers:
                try:
                    adapter = __import__("relay.adapters", fromlist=["get_adapter"]).get_adapter(
                        w, config.worker(w), config.path_value("adapter_spec_root")
                    )
                    catalog = get_model_catalog(config, adapter, refresh=args.refresh, include_hidden=args.include_hidden, verify=args.verify)
                    results.append(catalog.to_dict())
                except RelayError as err:
                    results.append({
                        "worker": w,
                        "status": "error",
                        "error_code": err.code,
                        "error_message": err.message,
                    })
                except Exception as exc:
                    results.append({
                        "worker": w,
                        "status": "error",
                        "error_message": str(exc),
                    })
                    
            from .util import utc_now
            response = {
                "schema_version": "1.0",
                "generated_at": utc_now(),
                "workers": results,
            }
            if not machine:
                for w_data in results:
                    print(f"\n{w_data.get('worker', 'Unknown').capitalize()}")
                    if w_data.get("status") == "error":
                        print(f"  Error: {w_data.get('error_message')}")
                        continue
                    for m in w_data.get("models", []):
                        tags = []
                        if m.get("availability"): tags.append(m["availability"])
                        if m.get("is_default"): tags.append("default")
                        tag_str = ", ".join(tags)
                        print(f"  {m.get('id'):<30} {tag_str}")
                    for w in w_data.get("warnings", []):
                        print(f"  Warning: {w}")
            else:
                _emit(response, machine=True)
                
        elif args.command == "model-check":
            from .model_discovery import probe_claude_model
            # for codex and antigravity, we check catalog. For claude we actually probe if requested.
            adapter = __import__("relay.adapters", fromlist=["get_adapter"]).get_adapter(
                args.worker, config.worker(args.worker), config.path_value("adapter_spec_root")
            )
            exe = adapter.executable()
            if not exe:
                raise RelayError("WORKER_NOT_INSTALLED", f"{args.worker} executable not found")
                
            is_ok = False
            if args.worker == "claude":
                is_ok = probe_claude_model(exe, args.model)
            else:
                # for others, check catalog
                from .model_discovery import get_model_catalog
                cat = get_model_catalog(config, adapter, refresh=False)
                is_ok = any(m.id == args.model or m.selectable_name == args.model for m in cat.models)
                
            _emit({"ok": is_ok, "worker": args.worker, "model": args.model}, machine)
        return 0
    except RelayError as err:
        _emit({"ok": False, "status": "failed", "error_code": err.code, "error_message": err.message, "details": err.details}, machine)
        return 2
    except KeyboardInterrupt:
        _emit({"ok": False, "status": "failed", "error_code": "CANCELLED", "error_message": "Interrupted"}, machine)
        return 130
    except Exception as exc:
        _emit({"ok": False, "status": "failed", "error_code": "INTERNAL_ERROR", "error_message": str(exc)}, machine)
        return 1
