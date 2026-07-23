from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..errors import RelayError
from ..model_catalog import ModelCatalog
from ..models import AdapterSpec
from ..util import json_dump, json_load, utc_now, which


@dataclass(slots=True)
class AdapterContext:
    job_id: str
    workspace: Path
    request_file: Path
    result_file: Path
    artifact_dir: Path
    schema_file: Path
    result_format: str
    profile: str
    model: str | None
    config: dict[str, Any]


class Adapter(ABC):
    name: str
    command_name: str
    version_args: tuple[str, ...] = ("--version",)
    help_args: tuple[str, ...] = ("--help",)

    def __init__(self, worker_config: dict[str, Any], spec_root: Path):
        self.worker_config = worker_config
        self.spec_root = spec_root
        self.command_name = str(worker_config.get("command") or self.command_name)

    def executable(self) -> str | None:
        return which(self.command_name)

    def capture(self, args: list[str], timeout: int = 20) -> tuple[int, str, str]:
        executable = self.executable()
        if not executable:
            raise RelayError("WORKER_NOT_INSTALLED", f"{self.name} executable not found: {self.command_name}")
        try:
            cp = subprocess.run(
                [executable, *args],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                env={**os.environ, "NO_COLOR": "1", "TERM": "dumb"},
            )
            return cp.returncode, cp.stdout, cp.stderr
        except subprocess.TimeoutExpired as exc:
            raise RelayError("TIMEOUT", f"{self.name} capability command timed out", True) from exc

    def version(self) -> str | None:
        candidates = [list(self.version_args), ["version"], ["-V"]]
        for args in candidates:
            try:
                code, out, err = self.capture(args, timeout=15)
            except RelayError:
                continue
            text = (out or err).strip()
            if code == 0 and text:
                return text.splitlines()[0][:300]
        return None

    def help_text(self) -> str:
        try:
            _, out, err = self.capture(list(self.help_args), timeout=20)
            return (out + "\n" + err).strip()
        except RelayError:
            return ""

    def spec_path(self, version: str | None = None) -> Path:
        safe_version = re.sub(r"[^A-Za-z0-9_.-]+", "_", version or "unknown")[:120]
        return self.spec_root / self.name / f"{safe_version}.json"

    def load_spec(self) -> AdapterSpec | None:
        version = self.version()
        value = json_load(self.spec_path(version))
        if not value:
            return None
        try:
            return AdapterSpec.from_dict(value)
        except TypeError:
            return None

    def save_spec(self, spec: AdapterSpec) -> Path:
        path = self.spec_path(spec.version)
        json_dump(path, spec.to_dict())
        return path

    def shallow_audit(self) -> AdapterSpec:
        executable = self.executable()
        version = self.version() if executable else None
        help_text = self.help_text() if executable else ""
        details = self.detect_capabilities(help_text)
        spec = AdapterSpec(
            worker=self.name,
            executable=executable,
            version=version,
            audited_at=utc_now(),
            help_hash=hashlib.sha256(help_text.encode("utf-8")).hexdigest() if help_text else None,
            shallow_ok=bool(executable and version),
            deep_ok=False,
            unattended_ok=False,
            output_ok=False,
            artifact_ok=False,
            status="shallow-ok" if executable and version else "unavailable",
            details=details,
        )
        self.save_spec(spec)
        return spec

    def require_verified(self) -> AdapterSpec:
        spec = self.load_spec()
        if not spec:
            raise RelayError(
                "WORKER_UNVERIFIED",
                f"{self.name} has no capability audit for its installed version. Run relay doctor --worker {self.name} --deep.",
            )
        current_executable = self.executable()
        if (
            current_executable
            and spec.executable
            and Path(current_executable).resolve() != Path(spec.executable).resolve()
        ):
            raise RelayError(
                "WORKER_UNVERIFIED",
                f"{self.name} executable path changed since audit. Run relay doctor --worker {self.name} --deep.",
            )
        if self.worker_config.get("require_deep_doctor", True) and not spec.deep_ok:
            raise RelayError(
                "WORKER_UNVERIFIED",
                f"{self.name} deep doctor has not passed for version {spec.version}",
            )
        expected_definition_hash = self.worker_config.get("_definition_hash")
        if expected_definition_hash and spec.details.get("definition_hash") != expected_definition_hash:
            raise RelayError(
                "WORKER_UNVERIFIED",
                f"{self.name} configuration changed since the last deep doctor audit",
            )
        if spec.status != "healthy":
            raise RelayError("WORKER_UNHEALTHY", f"{self.name} audit status is {spec.status}")
        return spec

    @abstractmethod
    def detect_capabilities(self, help_text: str) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def build_command(self, ctx: AdapterContext) -> tuple[list[str], bytes | None, dict[str, str]]:
        raise NotImplementedError

    @abstractmethod
    def normalize_output(self, ctx: AdapterContext, stdout_path: Path, stderr_path: Path) -> None:
        raise NotImplementedError

    def permission_mode(self) -> str:
        return "unknown"

    def sandbox_mode(self) -> str:
        return "external-workspace"

    def discover_models(
        self,
        *,
        refresh: bool = False,
        include_hidden: bool = False,
        verify: bool = False,
    ) -> ModelCatalog:
        raise RelayError(
            "MODEL_DISCOVERY_UNSUPPORTED",
            f"{self.name} does not support model discovery",
        )

    def classify_failure(self, exit_code: int | None, stderr: str) -> tuple[str, bool]:
        lower = stderr.lower()
        if "auth" in lower or "login" in lower or "sign in" in lower:
            return "AUTH_REQUIRED", False
        if "rate limit" in lower or "too many requests" in lower:
            return "RATE_LIMITED", True
        if "quota" in lower or "usage limit" in lower:
            return "QUOTA_EXCEEDED", True
        if "permission" in lower or "not allowed" in lower:
            return "PERMISSION_BLOCKED", False
        return "PROCESS_CRASHED", True

    def spec_hash(self, spec: AdapterSpec) -> str:
        return hashlib.sha256(json.dumps(spec.to_dict(), sort_keys=True).encode()).hexdigest()
