from __future__ import annotations

import ctypes
import json
import os
import signal
import subprocess
import time
from collections.abc import Callable
from pathlib import Path

from .models import ProcessOutcome
from .util import ensure_dir, redact_env, utc_now

_PROMPT_MARKERS = (
    "do you want to proceed",
    "allow this action",
    "approve this action",
    "trust this folder",
    "press enter to continue",
    "type approve",
    "waiting for approval",
    "permission required",
)


class WindowsJobObject:
    """Best-effort Windows Job Object that kills the process tree on close."""

    def __init__(self) -> None:
        self.handle = None
        if os.name != "nt":
            return
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._kernel32 = kernel32
        self.handle = kernel32.CreateJobObjectW(None, None)
        if not self.handle:
            return

        class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_longlong),
                ("PerJobUserTimeLimit", ctypes.c_longlong),
                ("LimitFlags", ctypes.c_uint32),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", ctypes.c_uint32),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", ctypes.c_uint32),
                ("SchedulingClass", ctypes.c_uint32),
            ]

        class IO_COUNTERS(ctypes.Structure):
            _fields_ = [
                (name, ctypes.c_ulonglong)
                for name in (
                    "ReadOperationCount",
                    "WriteOperationCount",
                    "OtherOperationCount",
                    "ReadTransferCount",
                    "WriteTransferCount",
                    "OtherTransferCount",
                )
            ]

        class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
                ("IoInfo", IO_COUNTERS),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        info.BasicLimitInformation.LimitFlags = 0x00002000
        ok = kernel32.SetInformationJobObject(self.handle, 9, ctypes.byref(info), ctypes.sizeof(info))
        if not ok:
            kernel32.CloseHandle(self.handle)
            self.handle = None

    def assign(self, process: subprocess.Popen) -> bool:
        if os.name != "nt" or not self.handle:
            return False
        try:
            return bool(self._kernel32.AssignProcessToJobObject(self.handle, int(process._handle)))
        except Exception:
            return False

    def terminate(self) -> None:
        if os.name == "nt" and self.handle:
            try:
                self._kernel32.TerminateJobObject(self.handle, 1)
            except Exception:
                pass

    def close(self) -> None:
        if os.name == "nt" and self.handle:
            try:
                self._kernel32.CloseHandle(self.handle)
            finally:
                self.handle = None


def _workspace_fingerprint(root: Path) -> tuple[int, int, int]:
    count = 0
    total = 0
    newest = 0
    try:
        for base, dirs, files in os.walk(root, followlinks=False):
            dirs[:] = [d for d in dirs if not (Path(base) / d).is_symlink()]
            for name in files:
                try:
                    stat = (Path(base) / name).stat()
                except OSError:
                    continue
                count += 1
                total += stat.st_size
                newest = max(newest, stat.st_mtime_ns)
    except OSError:
        pass
    return count, total, newest


def _file_state(path: Path) -> tuple[int, int]:
    try:
        stat = path.stat()
        return stat.st_size, stat.st_mtime_ns
    except OSError:
        return 0, 0


def _tail_contains_prompt(path: Path, max_bytes: int = 32768) -> bool:
    try:
        size = path.stat().st_size
        with path.open("rb") as f:
            if size > max_bytes:
                f.seek(size - max_bytes)
            text = f.read().decode("utf-8", errors="ignore").lower()
        return any(marker in text for marker in _PROMPT_MARKERS)
    except OSError:
        return False


def terminate_process_tree(process: subprocess.Popen, job: WindowsJobObject | None = None) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":
        if job:
            job.terminate()
        try:
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=15,
            )
        except Exception:
            try:
                process.kill()
            except Exception:
                pass
    else:
        try:
            os.killpg(os.getpgid(process.pid), signal.SIGTERM)
            time.sleep(0.25)
            if process.poll() is None:
                os.killpg(os.getpgid(process.pid), signal.SIGKILL)
        except Exception:
            try:
                process.kill()
            except Exception:
                pass


def run_supervised(
    command: list[str],
    cwd: Path,
    stdin_bytes: bytes | None,
    env_extra: dict[str, str],
    stdout_path: Path,
    stderr_path: Path,
    timeout_seconds: int,
    soft_stall_seconds: int,
    hard_stall_seconds: int,
    poll_seconds: float,
    base_env: dict[str, str] | None = None,
    cancel_requested: Callable[[], bool] | None = None,
    event_callback: Callable[[str, dict], None] | None = None,
) -> ProcessOutcome:
    ensure_dir(stdout_path.parent)
    env = {
        **(os.environ if base_env is None else base_env),
        **env_extra,
        "PYTHONUTF8": "1",
        "PYTHONIOENCODING": "utf-8",
        "NO_COLOR": "1",
        "TERM": "dumb",
        "CLICOLOR": "0",
    }
    creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
    start_new_session = os.name != "nt"
    command_record = {
        "command": command,
        "cwd": str(cwd),
        "started_at": utc_now(),
        "environment": redact_env({k: env[k] for k in env_extra}),
    }
    (stdout_path.parent / "command.json").write_text(
        json.dumps(command_record, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    start = time.monotonic()
    timed_out = stalled = cancelled = marker_seen = False
    soft_reported = False
    previous_fp = _workspace_fingerprint(cwd)
    last_activity = time.monotonic()
    previous_stdout = (0, 0)
    previous_stderr = (0, 0)

    with stdout_path.open("wb", buffering=0) as stdout_file, stderr_path.open("wb", buffering=0) as stderr_file:
        process = subprocess.Popen(
            command,
            cwd=cwd,
            stdin=subprocess.PIPE if stdin_bytes is not None else subprocess.DEVNULL,
            stdout=stdout_file,
            stderr=stderr_file,
            env=env,
            creationflags=creationflags,
            start_new_session=start_new_session,
        )
        job = WindowsJobObject() if os.name == "nt" else None
        if job:
            job.assign(process)
        if stdin_bytes is not None and process.stdin:
            try:
                process.stdin.write(stdin_bytes)
                process.stdin.flush()
            finally:
                process.stdin.close()

        try:
            while process.poll() is None:
                now = time.monotonic()
                elapsed = now - start
                if cancel_requested and cancel_requested():
                    cancelled = True
                    terminate_process_tree(process, job)
                    break
                if elapsed >= timeout_seconds:
                    timed_out = True
                    terminate_process_tree(process, job)
                    break

                fp = _workspace_fingerprint(cwd)
                stdout_state = _file_state(stdout_path)
                stderr_state = _file_state(stderr_path)
                if fp != previous_fp or stdout_state != previous_stdout or stderr_state != previous_stderr:
                    previous_fp = fp
                    previous_stdout = stdout_state
                    previous_stderr = stderr_state
                    last_activity = now
                    soft_reported = False
                    if _tail_contains_prompt(stdout_path) or _tail_contains_prompt(stderr_path):
                        marker_seen = True

                idle = now - last_activity
                if marker_seen and idle >= min(15, soft_stall_seconds):
                    stalled = True
                    terminate_process_tree(process, job)
                    break
                if idle >= soft_stall_seconds and not soft_reported:
                    soft_reported = True
                    if event_callback:
                        event_callback("SOFT_STALL", {"idle_seconds": round(idle, 1)})
                if idle >= hard_stall_seconds:
                    stalled = True
                    terminate_process_tree(process, job)
                    break
                time.sleep(max(0.05, poll_seconds))

            try:
                process.wait(timeout=20)
            except subprocess.TimeoutExpired:
                terminate_process_tree(process, job)
                process.wait(timeout=10)
        finally:
            if job:
                job.close()

    duration = time.monotonic() - start
    failure_code = None
    if cancelled:
        failure_code = "CANCELLED"
    elif timed_out:
        failure_code = "TIMEOUT"
    elif stalled:
        failure_code = "INTERACTIVE_PROMPT_DETECTED" if marker_seen else "STALL_TIMEOUT"
    return ProcessOutcome(
        exit_code=process.returncode,
        timed_out=timed_out,
        stalled=stalled,
        cancelled=cancelled,
        interactive_prompt_detected=marker_seen,
        duration_seconds=duration,
        stdout_path=stdout_path,
        stderr_path=stderr_path,
        command=command,
        failure_code=failure_code,
    )
