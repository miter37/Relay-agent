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
from .api import list_jobs
from .cleanup import CleanupManager
from .compatibility import relay_home_id
from .config import Config
from .db import Database
from .engine import RelayEngine
from .errors import RelayError
from .models import JobRequest
from .util import ensure_dir, json_dump, random_token, utc_now


class Scheduler:
    def __init__(self, engine: RelayEngine):
        self.engine = engine
        self.config = engine.config
        self.stop_event = threading.Event()
        self.executor = ThreadPoolExecutor(max_workers=int(self.config.get("max_concurrent_jobs", 2)))
        self.active: set[str] = set()
        self.lock = threading.Lock()
        self.worker_limits = {
            name: threading.Semaphore(int(self.config.get("max_concurrent_per_worker", 1)))
            for name in ("claude", "codex", "antigravity")
        }

    def start(self) -> None:
        threading.Thread(target=self.loop, name="relay-scheduler", daemon=True).start()

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
        self.executor.shutdown(wait=False, cancel_futures=False)


class MaintenanceLoop:
    def __init__(self, config: Config, db: Database):
        self.config = config
        self.manager = CleanupManager(config, db)
        self.stop_event = threading.Event()
        self.thread: threading.Thread | None = None

    def start(self) -> None:
        if not bool(self.config.get("cleanup_enabled", True)):
            return
        self.thread = threading.Thread(target=self.loop, name="relay-maintenance", daemon=True)
        self.thread.start()

    def loop(self) -> None:
        # Check hourly at most; the persisted last-run state enforces the configured interval.
        while not self.stop_event.is_set():
            try:
                if self.manager.due():
                    self.manager.run()
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
        length = int(self.headers.get("Content-Length", "0"))
        return json.loads(self.rfile.read(length).decode("utf-8")) if length else {}

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
                    "daemon_version": __version__,
                    "api_versions": ["v1"],
                    "api_schema_revision": 1,
                    "min_gui_version": "0.7.0",
                    "relay_home_id": relay_home_id(self.daemon.config.home),
                },
            )
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
        if path.startswith("/v1/jobs/"):
            job_id = path[len("/v1/jobs/") :]
            try:
                self._json(HTTPStatus.OK, self.daemon.engine.show(job_id))
            except RelayError as err:
                self._api_error(HTTPStatus.NOT_FOUND, err.code, err.message, details=err.details)
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
            if self.path == "/submit":
                request = JobRequest.from_dict(self._body())
                caller = request.caller.strip().lower()
                submitted_via = "hermes" if caller == "hermes" else "schedule" if caller == "schedule" else "cli"
                self._json(200, self.daemon.engine.queue(request, submitted_via=submitted_via))
                return
            if self.path.startswith("/cancel/"):
                job_id = self.path.split("/")[-1]
                changed = self.daemon.engine.db.request_cancel(job_id)
                self._json(
                    200, {"ok": changed, "job_id": job_id, "status": "cancel_requested" if changed else "unchanged"}
                )
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


class RelayDaemon:
    def __init__(self, config: Config):
        self.config = config
        self.config.init()
        self.db = Database(config.path_value("database_path"))
        self.engine = RelayEngine(config, self.db)
        self.scheduler = Scheduler(self.engine)
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
        self.maintenance.start()
        try:
            self.server.serve_forever(poll_interval=0.5)
        finally:
            self.maintenance.stop()
            self.scheduler.stop()
            self.pid_path.unlink(missing_ok=True)

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()
