"""Reusable execution profiles and durable custom-profile storage."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

from .errors import RelayError
from .util import new_job_id, utc_now

BUILTIN_PROFILES = (
    {
        "profile_id": "evidence-research",
        "name": "근거 기반 조사",
        "description": "최신·공식 출처를 확인하고 불확실성과 누락을 밝힙니다.",
        "instructions": "Use current authoritative sources where available. Include source URLs for material claims. Separate confirmed facts from estimates or interpretation. Put unresolved issues in uncertainties or missing_items.",
    },
    {
        "profile_id": "decision-brief",
        "name": "의사결정 브리핑",
        "description": "핵심 결론, 선택지, 위험과 다음 조치를 짧고 분명하게 정리합니다.",
        "instructions": "Lead with the decision and recommendation. Distinguish facts from assumptions. Present options, trade-offs, key risks, and concrete next actions.",
    },
    {
        "profile_id": "data-validation",
        "name": "데이터 검증",
        "description": "수치의 출처·계산·누락·이상치를 투명하게 검토합니다.",
        "instructions": "Verify units, dates, calculations, and source provenance. Flag missing values and anomalies. Do not invent data; make every calculation reproducible.",
    },
    {
        "profile_id": "analysis-only",
        "name": "분석 전용",
        "description": "입력 파일을 수정하지 않고 분석과 판단만 수행합니다.",
        "instructions": "Do not modify input files. Produce analysis only. State assumptions, evidence, limitations, and recommended follow-up work.",
    },
    {
        "profile_id": "artifact-production",
        "name": "산출물 제작",
        "description": "요청된 문서·코드·기타 산출물을 명확한 완료 기준에 맞춰 만듭니다.",
        "instructions": "Produce the requested result and supporting artifacts. Check requested formats and completion criteria before finishing. Describe created files and any remaining gaps.",
    },
    {
        "profile_id": "code-review",
        "name": "코드 검토",
        "description": "결함·회귀·보안·검증 관점에서 코드를 검토합니다.",
        "instructions": "Prioritize correctness, regressions, security, and missing tests. Cite concrete file locations and explain impact. Do not claim a check passed unless it was actually run.",
    },
)

LEGACY_PROFILE_IDS = {
    "web-research": "evidence-research",
    "report": "decision-brief",
    "analysis": "analysis-only",
    "analysis-only": "analysis-only",
    "general-artifact": "artifact-production",
    "code": "code-review",
}


class ProfileStore:
    def __init__(self, config) -> None:
        self.path = Path(config.config_dir) / "profiles.json"

    def list(self) -> list[dict]:
        builtins = [{**item, "builtin": True, "editable": False} for item in BUILTIN_PROFILES]
        custom = [{**item, "builtin": False, "editable": True} for item in self._custom().values()]
        return builtins + sorted(custom, key=lambda item: item["name"].casefold())

    def get(self, profile_id: str | None) -> dict:
        resolved = LEGACY_PROFILE_IDS.get(str(profile_id or "").strip(), str(profile_id or "").strip())
        for profile in self.list():
            if profile["profile_id"] == resolved:
                return profile
        # Old external callers may use arbitrary profile labels; retain their generic behavior.
        return {
            "profile_id": resolved or "artifact-production",
            "name": resolved or "산출물 제작",
            "description": "Legacy generic execution profile.",
            "instructions": "Complete the requested task faithfully.",
            "builtin": False,
            "editable": False,
            "legacy": True,
        }

    def create(self, payload: dict) -> dict:
        name = str(payload.get("name") or "").strip()
        instructions = str(payload.get("instructions") or "").strip()
        if not name or not instructions:
            raise RelayError("PROFILE_INVALID", "Profile name and execution instructions are required.")
        profile_id = f"custom-{new_job_id().lower()}"
        row = {
            "profile_id": profile_id,
            "name": name,
            "description": str(payload.get("description") or "").strip(),
            "instructions": instructions,
            "created_at": utc_now(),
            "updated_at": utc_now(),
        }
        custom = self._custom()
        custom[profile_id] = row
        self._save(custom)
        return {**row, "builtin": False, "editable": True}

    def update(self, profile_id: str, payload: dict) -> dict:
        custom = self._custom()
        if profile_id not in custom:
            raise RelayError(
                "PROFILE_NOT_EDITABLE", "Built-in or unknown Profiles cannot be edited; duplicate one first."
            )
        row = custom[profile_id]
        for key in ("name", "description", "instructions"):
            if key in payload:
                row[key] = str(payload[key] or "").strip()
        if not row["name"] or not row["instructions"]:
            raise RelayError("PROFILE_INVALID", "Profile name and execution instructions are required.")
        row["updated_at"] = utc_now()
        self._save(custom)
        return {**row, "builtin": False, "editable": True}

    def delete(self, profile_id: str) -> bool:
        custom = self._custom()
        if profile_id not in custom:
            raise RelayError("PROFILE_NOT_EDITABLE", "Built-in or unknown Profiles cannot be deleted.")
        del custom[profile_id]
        self._save(custom)
        return True

    def _custom(self) -> dict[str, dict]:
        if not self.path.exists():
            return {}
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return deepcopy(value) if isinstance(value, dict) else {}

    def _save(self, custom: dict[str, dict]) -> None:
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(custom, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(self.path)
