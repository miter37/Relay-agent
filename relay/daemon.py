from __future__ import annotations

import json
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlsplit

from . import __version__
from .agent_apps import AgentAppService
from .api import (
    approve_checkpoint,
    artifact_content,
    artifact_detail,
    artifact_lineage,
    attention_inbox_api,
    check_job_progress,
    compare_runs,
    create_project,
    create_routine,
    create_task,
    delete_project,
    delete_routine,
    delete_task,
    diff_artifacts,
    edit_checkpoint,
    get_agent,
    get_approval,
    get_project,
    get_routine,
    get_task,
    job_artifacts,
    job_detail,
    job_events,
    job_logs,
    job_result,
    list_agents,
    list_approvals,
    list_jobs,
    list_projects,
    list_routines,
    list_runs,
    list_tasks,
    notify_test_api,
    operations_projects_api,
    operations_routines_api,
    partial_reexecute_project_run,
    preview_routine,
    project_run,
    project_run_cancel,
    project_run_receipt,
    project_run_retry,
    project_run_steps,
    project_runs,
    quality_attention_api,
    reject_checkpoint,
    routine_runs,
    run_artifacts,
    run_detail,
    run_events,
    run_lineage,
    run_logs,
    run_progress,
    run_project,
    run_quality_api,
    run_result,
    run_routine_now,
    run_task,
    runs_for_task,
    save_run_as_task,
    search_artifacts,
    search_runs,
    semantic_search_api,
    update_project,
    update_routine,
    update_task,
)
from .autostart import AutoStartManager
from .cleanup import CleanupManager
from .compatibility import relay_home_id
from .config import Config
from .db import Database
from .engine import RelayEngine
from .errors import RelayError
from .models import JobRequest
from .projects.runtime import ProjectRuntime
from .projects.service import ProjectService
from .routines.runtime import RoutineRuntime
from .routines.service import RoutineService
from .schedules.retention import ScheduleRetentionManager
from .schedules.runtime import ScheduleRuntime
from .schedules.service import ScheduleService
from .security import (
    activate_antigravity,
    antigravity_setup,
    enabled_worker_health,
    security_posture,
    set_full_access_mode,
)
from .util import ensure_dir, json_dump, random_token, utc_now


class Scheduler:
    def __init__(self, engine: RelayEngine):
        self.engine = engine
        self.config = engine.config
        self.stop_event = threading.Event()
        self.executor = ThreadPoolExecutor(max_workers=int(self.config.get("max_concurrent_jobs", 2)))
        self.active: set[str] = set()
        self.lock = threading.Lock()
        self.thread: threading.Thread | None = None

    def start(self) -> None:
        self.thread = threading.Thread(target=self.loop, name="relay-scheduler", daemon=True)
        self.thread.start()

    def loop(self) -> None:
        while not self.stop_event.is_set():
            for job in self.engine.db.queued_jobs(limit=50):
                job_id = job["job_id"]
                with self.lock:
                    if job_id in self.active or len(self.active) >= int(self.config.get("max_concurrent_jobs", 2)):
                        continue
                    self.active.add(job_id)
                self.executor.submit(self._run, job_id)
            self.stop_event.wait(0.5)

    def _run(self, job_id: str) -> None:
        try:
            self.engine.execute_job(job_id)
        except Exception as exc:
            self.engine._fail_job(job_id, "INTERNAL_ERROR", str(exc), [])
        finally:
            with self.lock:
                self.active.discard(job_id)

    def stop(self) -> None:
        self.stop_event.set()
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=5)
        self.executor.shutdown(wait=True, cancel_futures=True)


class ScheduleLoop:
    def __init__(self, runtime: ScheduleRuntime, interval_seconds: float = 1.0):
        self.runtime = runtime
        self.interval_seconds = interval_seconds
        self.stop_event = threading.Event()
        self.thread: threading.Thread | None = None

    def start(self) -> None:
        self.thread = threading.Thread(target=self.loop, name="relay-schedule-loop", daemon=True)
        self.thread.start()

    def loop(self) -> None:
        while not self.stop_event.is_set():
            try:
                self.runtime.tick()
            except Exception:
                pass
            self.stop_event.wait(self.interval_seconds)

    def stop(self) -> None:
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=5)


class MaintenanceLoop:
    def __init__(self, config: Config, db: Database):
        self.config = config
        self.manager = CleanupManager(config, db)
        self.schedule_manager = ScheduleRetentionManager(config, db)
        self.stop_event = threading.Event()
        self.thread: threading.Thread | None = None

    def start(self) -> None:
        if not bool(self.config.get("cleanup_enabled", True)) and not bool(
            self.config.get("schedule_retention_enabled", True)
        ):
            return
        self.thread = threading.Thread(target=self.loop, name="relay-maintenance", daemon=True)
        self.thread.start()

    def loop(self) -> None:
        # Check hourly at most; the persisted last-run state enforces the configured interval.
        while not self.stop_event.is_set():
            try:
                if self.manager.due():
                    self.manager.run()
                if self.schedule_manager.due():
                    self.schedule_manager.run()
            except Exception:
                # Maintenance must never terminate the job daemon. The report will be retried next cycle.
                pass
            self.stop_event.wait(3600)

    def stop(self) -> None:
        self.stop_event.set()
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=3)


class RelayRequestHandler(BaseHTTPRequestHandler):
    server_version = "RelayDaemon/0.5"

    @property
    def daemon(self) -> RelayDaemon:
        return self.server.relay_daemon  # type: ignore[attr-defined]

    def log_message(self, fmt: str, *args) -> None:
        return

    def _json(self, status: int, value: Any) -> None:
        body = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _authorized(self) -> bool:
        return self.headers.get("X-Relay-Token", "") == self.daemon.token

    def _api_error(self, status: int, code: str, message: str, *, details: dict | None = None) -> None:
        self._json(
            status,
            {
                "ok": False,
                "error_code": code,
                "message": message,
                "action": None,
                "details": details or {},
                "retryable": status >= 500,
            },
        )

    def _body(self) -> dict:
        if hasattr(self, "_parsed_body"):
            return self._parsed_body
        length = int(self.headers.get("Content-Length", "0"))
        self._parsed_body = json.loads(self.rfile.read(length).decode("utf-8")) if length else {}
        return self._parsed_body

    def do_GET(self) -> None:
        if not self._authorized():
            self._json(HTTPStatus.UNAUTHORIZED, {"ok": False, "error": "unauthorized"})
            return
        parsed = urlsplit(self.path)
        path = parsed.path
        params = parse_qs(parsed.query, keep_blank_values=True)
        if path == "/health":
            self._json(
                200,
                {
                    "ok": True,
                    "status": "running",
                    "started_at": self.daemon.started_at,
                    "cleanup": self.daemon.maintenance.manager.status(),
                    "schedule_retention": self.daemon.maintenance.schedule_manager.status(),
                    "worker_health": enabled_worker_health(self.daemon.engine),
                    "daemon_version": __version__,
                    "api_versions": ["v1"],
                    "api_schema_revision": 5,
                    "capabilities": [
                        "runs-alias",
                        "artifact-uid",
                        "run-trigger",
                        "task-snapshot",
                        "artifact-lineage",
                        "artifact-input-snapshot",
                        "fts5-search",
                        "run-search",
                        "artifact-search",
                        "artifact-content-read",
                        "task-registry",
                        "task-run",
                        "project-runtime",
                        "routine-runtime",
                    ],
                    "min_gui_version": "1.1.0",
                    "relay_home_id": relay_home_id(self.daemon.config.home),
                },
            )
            return
        if path == "/v1/tasks":
            self._json(HTTPStatus.OK, list_tasks(self.daemon.engine))
            return
        if path.startswith("/v1/tasks/"):
            suffix = path[len("/v1/tasks/") :]
            try:
                if suffix.endswith("/runs"):
                    limit = int(params.get("limit", ["50"])[0])
                    self._json(HTTPStatus.OK, runs_for_task(self.daemon.engine, suffix[: -len("/runs")], limit=limit))
                else:
                    self._json(HTTPStatus.OK, get_task(self.daemon.engine, suffix))
            except RelayError as err:
                self._api_error(HTTPStatus.NOT_FOUND, err.code, err.message, details=err.details)
            return
        if path == "/v1/projects":
            self._json(HTTPStatus.OK, list_projects(self.daemon.engine))
            return
        if path == "/v1/routines":
            self._json(HTTPStatus.OK, list_routines(self.daemon.engine))
            return
        if path == "/v1/routines/preview":
            try:
                self._json(HTTPStatus.OK, preview_routine(self.daemon.engine, self._body()))
            except RelayError as err:
                self._api_error(HTTPStatus.BAD_REQUEST, err.code, err.message, details=err.details)
            return
        if path.startswith("/v1/projects/"):
            suffix = path[len("/v1/projects/") :]
            try:
                if suffix.endswith("/runs"):
                    pid = suffix[: -len("/runs")]
                    self._json(HTTPStatus.OK, project_runs(self.daemon.engine, pid))
                else:
                    self._json(HTTPStatus.OK, get_project(self.daemon.engine, suffix))
            except RelayError as err:
                self._api_error(HTTPStatus.NOT_FOUND, err.code, err.message, details=err.details)
            return
        if path.startswith("/v1/routines/"):
            suffix = path[len("/v1/routines/") :]
            try:
                if suffix.endswith("/runs"):
                    rid = suffix[: -len("/runs")]
                    self._json(HTTPStatus.OK, routine_runs(self.daemon.engine, rid))
                else:
                    self._json(HTTPStatus.OK, get_routine(self.daemon.engine, suffix))
            except RelayError as err:
                self._api_error(HTTPStatus.NOT_FOUND, err.code, err.message, details=err.details)
            return
        if path.startswith("/v1/project-runs/") and path.endswith("/approvals"):
            prid = path[len("/v1/project-runs/") : -len("/approvals")]
            self._json(HTTPStatus.OK, list_approvals(self.daemon.engine, prid))
            return
        if path.startswith("/v1/approvals/"):
            token = path[len("/v1/approvals/") :]
            self._json(HTTPStatus.OK, get_approval(self.daemon.engine, token))
            return
        if path.startswith("/v1/project-runs/"):
            suffix = path[len("/v1/project-runs/") :]
            try:
                if suffix.endswith("/steps"):
                    prid = suffix[: -len("/steps")]
                    self._json(HTTPStatus.OK, project_run_steps(self.daemon.engine, prid))
                elif suffix.endswith("/receipt"):
                    prid = suffix[: -len("/receipt")]
                    self._json(HTTPStatus.OK, project_run_receipt(self.daemon.engine, prid))
                elif suffix.endswith("/retry"):
                    prid = suffix[: -len("/retry")]
                    self._json(HTTPStatus.OK, project_run_retry(self.daemon.engine, prid, self._body()))
                elif suffix.endswith("/partial-reexecute"):
                    prid = suffix[: -len("/partial-reexecute")]
                    self._json(HTTPStatus.OK, partial_reexecute_project_run(self.daemon.engine, prid, self._body()))
                    return
                elif suffix.endswith("/cancel"):
                    prid = suffix[: -len("/cancel")]
                    self._json(HTTPStatus.OK, project_run_cancel(self.daemon.engine, prid))
                else:
                    self._json(HTTPStatus.OK, project_run(self.daemon.engine, suffix))
            except RelayError as err:
                self._api_error(HTTPStatus.NOT_FOUND, err.code, err.message, details=err.details)
            return
        if path == "/v1/attention":
            kind = (params.get("kind") or [None])[0]
            limit = int((params.get("limit") or ["50"])[0])
            self._json(HTTPStatus.OK, attention_inbox_api(self.daemon.engine, kind=kind, limit=limit))
            return
        if path == "/v1/operations/routines":
            limit = int((params.get("limit") or ["50"])[0])
            self._json(HTTPStatus.OK, operations_routines_api(self.daemon.engine, limit=limit))
            return
        if path == "/v1/operations/projects":
            limit = int((params.get("limit") or ["50"])[0])
            self._json(HTTPStatus.OK, operations_projects_api(self.daemon.engine, limit=limit))
            return
        if path == "/v1/jobs":
            try:

                def value(name: str) -> str | None:
                    values = params.get(name, [])
                    return values[0] if values else None

                limit_value = value("limit") or "50"
                limit = int(limit_value)
                payload = list_jobs(
                    self.daemon.db,
                    bucket=value("bucket") or "all",
                    status=value("status") or value("result"),
                    agent=value("agent"),
                    submitted_via=value("source"),
                    query=value("q"),
                    date_from=value("from"),
                    date_to=value("to"),
                    limit=limit,
                    cursor=value("cursor"),
                    hide_task=self.daemon.engine._history_display_mode() != "full",
                )
                self._json(HTTPStatus.OK, payload)
            except (ValueError, RelayError) as err:
                if isinstance(err, RelayError):
                    code, message = err.code, err.message
                else:
                    code, message = "INVALID_REQUEST", str(err)
                self._api_error(HTTPStatus.BAD_REQUEST, code, message)
            except Exception as exc:
                self._api_error(HTTPStatus.INTERNAL_SERVER_ERROR, "INTERNAL_ERROR", str(exc))
            return
        if path == "/v1/search/runs":
            try:
                values = {key: (items[0] if items else None) for key, items in params.items()}
                self._json(
                    HTTPStatus.OK,
                    search_runs(
                        self.daemon.db,
                        query=values.get("q"),
                        status=values.get("status"),
                        worker=values.get("worker"),
                        submitted_via=values.get("source"),
                        trigger_type=values.get("trigger_type"),
                        date_from=values.get("date_from"),
                        date_to=values.get("date_to"),
                        role=values.get("role"),
                        limit=int(values.get("limit") or "20"),
                        offset=int(values.get("offset") or "0"),
                    ),
                )
            except (ValueError, RelayError) as err:
                code = err.code if isinstance(err, RelayError) else "INVALID_REQUEST"
                message = err.message if isinstance(err, RelayError) else str(err)
                self._api_error(HTTPStatus.BAD_REQUEST, code, message)
            return
        if path == "/v1/search/artifacts":
            try:
                values = {key: (items[0] if items else None) for key, items in params.items()}
                self._json(
                    HTTPStatus.OK,
                    search_artifacts(
                        self.daemon.db,
                        query=values.get("q"),
                        role=values.get("role"),
                        mime_type=values.get("mime_type"),
                        date_from=values.get("date_from"),
                        date_to=values.get("date_to"),
                        limit=int(values.get("limit") or "20"),
                        offset=int(values.get("offset") or "0"),
                    ),
                )
            except (ValueError, RelayError) as err:
                code = err.code if isinstance(err, RelayError) else "INVALID_REQUEST"
                message = err.message if isinstance(err, RelayError) else str(err)
                self._api_error(HTTPStatus.BAD_REQUEST, code, message)
            return
        if path == "/v1/runs/compare":
            try:
                a_id = (params.get("a") or [None])[0]
                b_id = (params.get("b") or [None])[0]
                if not a_id or not b_id:
                    raise RelayError("INVALID_REQUEST", "Parameters 'a' and 'b' are required.")
                self._json(HTTPStatus.OK, compare_runs(self.daemon.engine, a_id, b_id))
            except RelayError as err:
                self._api_error(HTTPStatus.BAD_REQUEST, err.code, err.message, details=err.details)
            return
        if path == "/v1/runs":
            try:

                def value(name: str) -> str | None:
                    values = params.get(name, [])
                    return values[0] if values else None

                payload = list_runs(
                    self.daemon.db,
                    bucket=value("bucket") or "all",
                    status=value("status") or value("result"),
                    agent=value("agent"),
                    submitted_via=value("source"),
                    query=value("q"),
                    date_from=value("from"),
                    date_to=value("to"),
                    limit=int(value("limit") or "50"),
                    cursor=value("cursor"),
                    hide_task=self.daemon.engine._history_display_mode() != "full",
                )
                self._json(HTTPStatus.OK, payload)
            except (ValueError, RelayError) as err:
                if isinstance(err, RelayError):
                    code, message = err.code, err.message
                else:
                    code, message = "INVALID_REQUEST", str(err)
                self._api_error(HTTPStatus.BAD_REQUEST, code, message)
            except Exception as exc:
                self._api_error(HTTPStatus.INTERNAL_SERVER_ERROR, "INTERNAL_ERROR", str(exc))
            return
        if path == "/v1/agents":
            self._json(HTTPStatus.OK, list_agents(self.daemon.engine))
            return
        if path == "/v1/agents/antigravity/setup":
            self._json(
                HTTPStatus.OK, {"ok": True, "antigravity": antigravity_setup(self.daemon.config, self.daemon.engine)}
            )
            return
        if path == "/v1/autostart":
            self._json(HTTPStatus.OK, {"ok": True, "autostart": self.daemon.autostart_manager.status()})
            return
        if path == "/v1/security":
            self._json(HTTPStatus.OK, {"ok": True, **security_posture(self.daemon.config)})
            return
        if path.startswith("/v1/agents/"):
            try:
                self._json(HTTPStatus.OK, get_agent(self.daemon.engine, path[len("/v1/agents/") :]))
            except RelayError as err:
                self._api_error(HTTPStatus.NOT_FOUND, err.code, err.message, details=err.details)
            return
        if path == "/v1/agent-apps":
            self._json(HTTPStatus.OK, {"ok": True, "agent_apps": self.daemon.agent_app_service.list()})
            return
        if path.startswith("/v1/agent-apps/"):
            try:
                self._json(
                    HTTPStatus.OK,
                    {"ok": True, "agent": self.daemon.agent_app_service.show(path[len("/v1/agent-apps/") :])},
                )
            except RelayError as err:
                self._api_error(HTTPStatus.NOT_FOUND, err.code, err.message, details=err.details)
            return
        if path == "/v1/schedules":
            self._json(HTTPStatus.OK, {"ok": True, "schedules": self.daemon.schedule_service.list()})
            return

        if path.startswith("/v1/schedules/"):
            suffix = path[len("/v1/schedules/") :]
            try:
                if suffix.endswith("/runs"):
                    schedule_id = suffix[: -len("/runs")]
                    self._json(
                        HTTPStatus.OK,
                        {
                            "ok": True,
                            "schedule_id": schedule_id,
                            "runs": self.daemon.schedule_service.runs(schedule_id),
                        },
                    )
                else:
                    self._json(HTTPStatus.OK, {"ok": True, "schedule": self.daemon.schedule_service.show(suffix)})
            except RelayError as err:
                self._api_error(HTTPStatus.NOT_FOUND, err.code, err.message, details=err.details)
            return
        if path == "/v1/artifacts/diff":
            try:
                a_uid = (params.get("a") or [None])[0]
                b_uid = (params.get("b") or [None])[0]
                if not a_uid or not b_uid:
                    raise RelayError("INVALID_REQUEST", "Parameters 'a' and 'b' are required.")
                mb_str = (params.get("max_bytes") or ["262144"])[0]
                max_bytes = int(mb_str) if mb_str and mb_str.isdigit() else 262144
                self._json(HTTPStatus.OK, diff_artifacts(self.daemon.engine, a_uid, b_uid, max_bytes=max_bytes))
            except RelayError as err:
                self._api_error(HTTPStatus.BAD_REQUEST, err.code, err.message, details=err.details)
            return
        if path.startswith("/v1/artifacts/"):
            suffix = path[len("/v1/artifacts/") :]
            try:
                if suffix.endswith("/lineage"):
                    self._json(HTTPStatus.OK, artifact_lineage(self.daemon.db, suffix[: -len("/lineage")]))
                elif suffix.endswith("/content"):
                    limit = int(params.get("max_bytes", ["65536"])[0])
                    self._json(
                        HTTPStatus.OK, artifact_content(self.daemon.db, suffix[: -len("/content")], max_bytes=limit)
                    )
                else:
                    self._json(HTTPStatus.OK, artifact_detail(self.daemon.db, suffix))
            except RelayError as err:
                self._api_error(HTTPStatus.NOT_FOUND, err.code, err.message, details=err.details)
            return
        if path.startswith("/v1/runs/") and path.endswith("/quality"):
            run_id = path[len("/v1/runs/") : -len("/quality")]
            self._json(HTTPStatus.OK, run_quality_api(self.daemon.engine, run_id))
            return
        if path == "/v1/quality/attention":
            st = (params.get("status") or ["low"])[0]
            limit = int((params.get("limit") or ["50"])[0])
            self._json(HTTPStatus.OK, quality_attention_api(self.daemon.engine, status_filter=st, limit=limit))
            return
        if path.startswith("/v1/runs/"):
            suffix = path[len("/v1/runs/") :]
            try:
                if suffix.endswith("/lineage"):
                    self._json(HTTPStatus.OK, run_lineage(self.daemon.db, suffix[: -len("/lineage")]))
                elif suffix.endswith("/result"):
                    self._json(HTTPStatus.OK, run_result(self.daemon.db, suffix[: -len("/result")]))
                elif suffix.endswith("/artifacts"):
                    self._json(HTTPStatus.OK, run_artifacts(self.daemon.db, suffix[: -len("/artifacts")]))
                elif suffix.endswith("/events"):
                    self._json(HTTPStatus.OK, run_events(self.daemon.db, suffix[: -len("/events")]))
                elif suffix.endswith("/logs"):
                    values = parse_qs(parsed.query, keep_blank_values=True)
                    attempt_values = values.get("attempt_id", [])
                    stream_values = values.get("stream", [])
                    if not attempt_values or not stream_values:
                        raise RelayError("INVALID_REQUEST", "attempt_id and stream are required.")
                    self._json(
                        HTTPStatus.OK,
                        run_logs(
                            self.daemon.db,
                            suffix[: -len("/logs")],
                            attempt_id=int(attempt_values[0]),
                            stream=stream_values[0],
                            offset=int(values["offset"][0]) if values.get("offset") and values["offset"][0] else None,
                            limit=int(values["limit"][0]) if values.get("limit") and values["limit"][0] else 16000,
                            errors_only=bool(
                                values.get("errors_only") and values["errors_only"][0].lower() in {"1", "true", "yes"}
                            ),
                        ),
                    )
                elif suffix.endswith("/check"):
                    self._json(HTTPStatus.OK, run_progress(self.daemon.engine, suffix[: -len("/check")]))
                else:
                    self._json(HTTPStatus.OK, run_detail(self.daemon.engine, suffix))
            except RelayError as err:
                self._api_error(HTTPStatus.NOT_FOUND, err.code, err.message, details=err.details)
            except (TypeError, ValueError) as err:
                self._api_error(HTTPStatus.BAD_REQUEST, "INVALID_REQUEST", str(err))
            return
        if path.startswith("/v1/jobs/"):
            suffix = path[len("/v1/jobs/") :]
            try:
                if suffix.endswith("/result"):
                    self._json(HTTPStatus.OK, job_result(self.daemon.db, suffix[: -len("/result")]))
                elif suffix.endswith("/artifacts"):
                    self._json(HTTPStatus.OK, job_artifacts(self.daemon.db, suffix[: -len("/artifacts")]))
                elif suffix.endswith("/events"):
                    self._json(HTTPStatus.OK, job_events(self.daemon.db, suffix[: -len("/events")]))
                elif suffix.endswith("/logs"):
                    values = parse_qs(parsed.query, keep_blank_values=True)
                    attempt_values = values.get("attempt_id", [])
                    stream_values = values.get("stream", [])
                    offset_values = values.get("offset", [])
                    limit_values = values.get("limit", [])
                    errors_only_values = values.get("errors_only", [])
                    if not attempt_values or not stream_values:
                        raise RelayError("INVALID_REQUEST", "attempt_id and stream are required.")
                    self._json(
                        HTTPStatus.OK,
                        job_logs(
                            self.daemon.db,
                            suffix[: -len("/logs")],
                            attempt_id=int(attempt_values[0]),
                            stream=stream_values[0],
                            offset=int(offset_values[0]) if offset_values and offset_values[0] else None,
                            limit=int(limit_values[0]) if limit_values and limit_values[0] else 16000,
                            errors_only=bool(
                                errors_only_values and errors_only_values[0].lower() in {"1", "true", "yes"}
                            ),
                        ),
                    )
                else:
                    self._json(HTTPStatus.OK, job_detail(self.daemon.engine, suffix))
            except RelayError as err:
                self._api_error(HTTPStatus.NOT_FOUND, err.code, err.message, details=err.details)
            except (TypeError, ValueError) as err:
                self._api_error(HTTPStatus.BAD_REQUEST, "INVALID_REQUEST", str(err))
            return
        for prefix, action in (("/status/", "status"), ("/result/", "result"), ("/show/", "show")):
            if path.startswith(prefix):
                job_id = path[len(prefix) :]
                try:
                    if action == "show":
                        value = self.daemon.engine.show(job_id)
                    else:
                        value = self.daemon.engine.receipt(job_id)
                    self._json(200, value)
                except RelayError as err:
                    self._json(404, {"ok": False, "error_code": err.code, "error_message": err.message})
                return
        self._json(404, {"ok": False, "error": "not found"})

    def do_POST(self) -> None:
        if not self._authorized():
            self._json(HTTPStatus.UNAUTHORIZED, {"ok": False, "error": "unauthorized"})
            return
        try:
            self._body()
            parsed = urlsplit(self.path)
            path = parsed.path
            if path == "/v1/agents/antigravity/activate":
                self._json(
                    HTTPStatus.OK,
                    activate_antigravity(
                        self.daemon.config,
                        self.daemon.engine,
                        isolation_acknowledged=self._body().get("isolation_acknowledged"),
                    ),
                )
                return
            if path == "/v1/tasks":
                self._json(HTTPStatus.OK, create_task(self.daemon.engine, self._body()))
                return
            if path == "/v1/runs/save-as-task":
                run_id = self._body().get("run_id")
                self._json(HTTPStatus.OK, save_run_as_task(self.daemon.engine, run_id, self._body()))
                return
            if path.startswith("/v1/tasks/") and path.endswith("/run"):
                task_id = path[len("/v1/tasks/") : -len("/run")]
                self._json(HTTPStatus.OK, run_task(self.daemon.engine, task_id, self._body()))
                return
            if path.startswith("/v1/tasks/"):
                task_id = path[len("/v1/tasks/") :]
                self._json(HTTPStatus.OK, update_task(self.daemon.engine, task_id, self._body()))
                return
            if path == "/v1/projects":
                self._json(HTTPStatus.OK, create_project(self.daemon.engine, self._body()))
                return
            if path == "/v1/routines":
                self._json(HTTPStatus.OK, create_routine(self.daemon.engine, self._body()))
                return
            if path.startswith("/v1/routines/") and path.endswith("/run-now"):
                rid = path[len("/v1/routines/") : -len("/run-now")]
                self._json(HTTPStatus.OK, run_routine_now(self.daemon.engine, rid))
                return
            if path.startswith("/v1/projects/") and path.endswith("/run"):
                pid = path[len("/v1/projects/") : -len("/run")]
                self._json(HTTPStatus.OK, run_project(self.daemon.engine, pid, self._body()))
                return
            if path.startswith("/v1/projects/"):
                pid = path[len("/v1/projects/") :]
                self._json(HTTPStatus.OK, update_project(self.daemon.engine, pid, self._body()))
                return
            if path.startswith("/v1/routines/"):
                rid = path[len("/v1/routines/") :]
                self._json(HTTPStatus.OK, update_routine(self.daemon.engine, rid, self._body()))
                return
            if path.startswith("/v1/project-runs/") and "/approvals/" in path:
                # Path format: /v1/project-runs/{id}/approvals/{token}/{action}
                parts = path.split("/")
                # parts: ['', 'v1', 'project-runs', {id}, 'approvals', {token}, {action}]
                if len(parts) == 7:
                    prid, token, action = parts[3], parts[5], parts[6]
                    if action == "approve":
                        self._json(HTTPStatus.OK, approve_checkpoint(self.daemon.engine, prid, token, self._body()))
                        return
                    elif action == "reject":
                        self._json(HTTPStatus.OK, reject_checkpoint(self.daemon.engine, prid, token, self._body()))
                        return
                    elif action == "edit":
                        self._json(HTTPStatus.OK, edit_checkpoint(self.daemon.engine, prid, token, self._body()))
                        return
            if path == "/v1/search/semantic":
                self._json(HTTPStatus.OK, semantic_search_api(self.daemon.engine, self._body()))
                return
            if path == "/v1/notifications/test":
                self._json(HTTPStatus.OK, notify_test_api(self.daemon.engine, self._body()))
                return
            if path == "/v1/agent-apps":
                self._json(
                    HTTPStatus.OK,
                    {"ok": True, "agent": self.daemon.agent_app_service.create(self._body())},
                )
                return
            if path == "/v1/agent-apps/test-manifest":
                self._json(
                    HTTPStatus.OK,
                    {"ok": True, **self.daemon.agent_app_service.test_manifest(self._body())},
                )
                return
            if path.startswith("/v1/agent-apps/") and path.endswith("/test"):
                agent_id = path[len("/v1/agent-apps/") : -len("/test")]
                self._json(HTTPStatus.OK, {"ok": True, **self.daemon.agent_app_service.test(agent_id)})
                return
            if path == "/v1/schedules/preview":
                self._json(
                    HTTPStatus.OK,
                    {"ok": True, "occurrences": self.daemon.schedule_service.preview(self._body())},
                )
                return
            if path.startswith("/v1/schedules/from-job/"):
                source_job_id = path[len("/v1/schedules/from-job/") :]
                self._json(
                    HTTPStatus.OK,
                    {"ok": True, "schedule": self.daemon.schedule_service.create_from_job(source_job_id, self._body())},
                )
                return

            if path.startswith("/v1/schedules/"):
                suffix = path[len("/v1/schedules/") :]
                if suffix.endswith("/copy"):
                    self._json(
                        HTTPStatus.OK,
                        {
                            "ok": True,
                            "schedule": self.daemon.schedule_service.copy(suffix[: -len("/copy")], self._body()),
                        },
                    )
                    return
                if suffix.endswith("/pause"):
                    self._json(
                        HTTPStatus.OK,
                        {"ok": True, "schedule": self.daemon.schedule_service.pause(suffix[: -len("/pause")])},
                    )
                    return
                if suffix.endswith("/resume"):
                    self._json(
                        HTTPStatus.OK,
                        {"ok": True, "schedule": self.daemon.schedule_service.resume(suffix[: -len("/resume")])},
                    )
                    return
                if suffix.endswith("/run-now"):
                    self._json(
                        HTTPStatus.OK,
                        {"ok": True, "run": self.daemon.schedule_service.run_now(suffix[: -len("/run-now")])},
                    )
                    return
            if path == "/v1/jobs":
                request = JobRequest.from_dict(self._body())
                request.caller = "human"
                self._json(HTTPStatus.OK, self.daemon.engine.queue(request, submitted_via="gui"))
                return
            if path.startswith("/v1/jobs/"):
                suffix = path[len("/v1/jobs/") :]
                if suffix.endswith("/check"):
                    self._json(HTTPStatus.OK, check_job_progress(self.daemon.engine, suffix[: -len("/check")]))
                    return
                if suffix.endswith("/cancel"):
                    self._json(HTTPStatus.OK, self.daemon.engine.cancel(suffix[: -len("/cancel")]))
                    return
                if suffix.endswith("/rerun"):
                    self._json(HTTPStatus.OK, self.daemon.engine.queue_rerun(suffix[: -len("/rerun")]))
                    return
            if path == "/submit":
                request = JobRequest.from_dict(self._body())
                caller = request.caller.strip().lower()
                submitted_via = "hermes" if caller == "hermes" else "schedule" if caller == "schedule" else "cli"
                self._json(200, self.daemon.engine.queue(request, submitted_via=submitted_via))
                return
            if path.startswith("/cancel/"):
                self._json(HTTPStatus.OK, self.daemon.engine.cancel(path[len("/cancel/") :]))
                return
            if self.path == "/shutdown":
                self._json(200, {"ok": True, "status": "stopping"})
                threading.Thread(target=self.daemon.stop, daemon=True).start()
                return
            self._json(404, {"ok": False, "error": "not found"})
        except RelayError as err:
            self._json(400, {"ok": False, "error_code": err.code, "error_message": err.message})
        except Exception as exc:
            self._json(500, {"ok": False, "error_code": "INTERNAL_ERROR", "error_message": str(exc)})

    def do_PATCH(self) -> None:
        if not self._authorized():
            self._json(HTTPStatus.UNAUTHORIZED, {"ok": False, "error": "unauthorized"})
            return
        path = urlsplit(self.path).path
        if path.startswith("/v1/security/full-access/"):
            try:
                worker = path[len("/v1/security/full-access/") :]
                payload = self._body()
                settings = set_full_access_mode(self.daemon.config, worker, payload.get("enabled"))
                self._json(HTTPStatus.OK, {"ok": True, "full_access_mode": settings})
            except RelayError as err:
                self._api_error(HTTPStatus.BAD_REQUEST, err.code, err.message, details=err.details)
            return
        if path.startswith("/v1/agent-apps/"):
            try:
                suffix = path[len("/v1/agent-apps/") :]
                if suffix.endswith("/enabled"):
                    agent_id = suffix[: -len("/enabled")]
                    payload = self._body()
                    enabled = payload.get("enabled")
                    if not isinstance(enabled, bool):
                        raise RelayError("INVALID_REQUEST", "enabled must be a boolean")
                    value = self.daemon.agent_app_service.set_enabled(agent_id, enabled)
                else:
                    value = self.daemon.agent_app_service.update(suffix, self._body())
                self._json(HTTPStatus.OK, {"ok": True, "agent": value})
            except RelayError as err:
                self._api_error(HTTPStatus.BAD_REQUEST, err.code, err.message, details=err.details)
            return
        if path == "/v1/autostart":
            try:
                payload = self._body()
                enabled = payload.get("enabled")
                if not isinstance(enabled, bool):
                    raise RelayError("INVALID_REQUEST", "enabled must be a boolean")
                value = self.daemon.autostart_manager.enable() if enabled else self.daemon.autostart_manager.disable()
                self._json(HTTPStatus.OK, {"ok": True, "autostart": value})
            except RelayError as err:
                self._api_error(HTTPStatus.BAD_REQUEST, err.code, err.message, details=err.details)
            return

        if path.startswith("/v1/schedules/"):
            try:
                schedule_id = path[len("/v1/schedules/") :]
                self._json(
                    HTTPStatus.OK,
                    {"ok": True, "schedule": self.daemon.schedule_service.update(schedule_id, self._body())},
                )
            except RelayError as err:
                self._api_error(HTTPStatus.BAD_REQUEST, err.code, err.message, details=err.details)
            return
        self._json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "not found"})

    def do_DELETE(self) -> None:
        if not self._authorized():
            self._json(HTTPStatus.UNAUTHORIZED, {"ok": False, "error": "unauthorized"})
            return
        path = urlsplit(self.path).path
        if path.startswith("/v1/tasks/"):
            try:
                task_id = path[len("/v1/tasks/") :]
                self._json(HTTPStatus.OK, delete_task(self.daemon.engine, task_id))
            except RelayError as err:
                self._api_error(HTTPStatus.NOT_FOUND, err.code, err.message, details=err.details)
            return
        if path.startswith("/v1/projects/"):
            try:
                pid = path[len("/v1/projects/") :]
                self._json(HTTPStatus.OK, delete_project(self.daemon.engine, pid))
            except RelayError as err:
                self._api_error(HTTPStatus.NOT_FOUND, err.code, err.message, details=err.details)
            return
        if path.startswith("/v1/routines/"):
            try:
                rid = path[len("/v1/routines/") :]
                self._json(HTTPStatus.OK, delete_routine(self.daemon.engine, rid))
            except RelayError as err:
                self._api_error(HTTPStatus.NOT_FOUND, err.code, err.message, details=err.details)
            return
        if path.startswith("/v1/agent-apps/"):
            try:
                agent_id = path[len("/v1/agent-apps/") :]
                self._json(
                    HTTPStatus.OK,
                    {"ok": True, "deleted": self.daemon.agent_app_service.delete(agent_id)},
                )
            except RelayError as err:
                self._api_error(HTTPStatus.BAD_REQUEST, err.code, err.message, details=err.details)
            return

        if path.startswith("/v1/schedules/"):
            try:
                schedule_id = path[len("/v1/schedules/") :]
                self._json(HTTPStatus.OK, {"ok": True, "deleted": self.daemon.schedule_service.delete(schedule_id)})
            except RelayError as err:
                self._api_error(HTTPStatus.NOT_FOUND, err.code, err.message, details=err.details)
            return
        self._json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "not found"})


class RelayDaemon:
    def __init__(self, config: Config):
        self.config = config
        self.config.init()
        self.db = Database(config.path_value("database_path"))
        self.engine = RelayEngine(config, self.db)
        self.agent_app_service = AgentAppService(self.config, self.db, self.engine)
        self.schedule_service = ScheduleService(self.config, self.db, self.engine)
        self.project_service = ProjectService(self.db, self.engine)
        self.project_runtime = ProjectRuntime(self.db, self.engine, self.project_service)
        self.routine_service = RoutineService(self.config, self.db, self.engine)
        self.routine_runtime = RoutineRuntime(self.config, self.db, self.engine, self.routine_service)
        self.autostart_manager = AutoStartManager(self.config)
        self.schedule_runtime = ScheduleRuntime(self.config, self.db, self.engine)
        self.scheduler = Scheduler(self.engine)
        self.schedule_loop = ScheduleLoop(
            self.schedule_runtime, float(self.config.get("schedule_poll_interval_seconds", 1))
        )
        self.maintenance = MaintenanceLoop(config, self.db)
        self.runtime = config.path_value("runtime_root")
        ensure_dir(self.runtime)
        self.token_path = self.runtime / "daemon.token"
        self.pid_path = self.runtime / "daemon.pid"
        self.info_path = self.runtime / "daemon.json"
        if not self.token_path.exists():
            self.token_path.write_text(random_token(), encoding="utf-8")
            if os.name != "nt":
                os.chmod(self.token_path, 0o600)
        self.token = self.token_path.read_text(encoding="utf-8").strip()
        self.started_at = utc_now()
        self.server = ThreadingHTTPServer(
            (str(config.get("daemon_host", "127.0.0.1")), int(config.get("daemon_port", 47831))),
            RelayRequestHandler,
        )
        self.server.relay_daemon = self  # type: ignore[attr-defined]

    def serve(self) -> None:
        recovered = self.db.recover_interrupted()
        self.pid_path.write_text(str(os.getpid()), encoding="ascii")
        json_dump(
            self.info_path,
            {
                "pid": os.getpid(),
                "host": self.config.get("daemon_host"),
                "port": self.config.get("daemon_port"),
                "started_at": self.started_at,
                "recovered_jobs": recovered,
            },
        )
        self.scheduler.start()
        self.schedule_loop.start()
        self.maintenance.start()
        self.project_runtime.start()
        self.routine_runtime.start()
        try:
            self.server.serve_forever(poll_interval=0.5)
        finally:
            self.maintenance.stop()
            self.project_runtime.stop()
            self.routine_runtime.stop()
            self.schedule_loop.stop()
            self.scheduler.stop()
            self.pid_path.unlink(missing_ok=True)

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()
