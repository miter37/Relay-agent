"""Deterministic Project Run evidence cases for GUI/API regression tests."""

from __future__ import annotations

from copy import deepcopy

PROJECT_ID = "project-leaders-speak"
PROJECT_NAME = "[L1] Leaders Speak — 최근 주요 인물 발언 리포트"

NODES = [
    ("official_research", "task-official"),
    ("media_research", "task-media"),
    ("verify_merge_select", "task-verify"),
    ("analysis_translation_script", "task-analysis"),
    ("image_collection", "task-image"),
    ("render_and_qa", "task-render"),
]

CONNECTIONS = [
    {"from_node": "official_research", "from_role": "result", "to_node": "verify_merge_select", "to_alias": "A1"},
    {"from_node": "media_research", "from_role": "result", "to_node": "verify_merge_select", "to_alias": "A2"},
    {"from_node": "verify_merge_select", "from_role": "result", "to_node": "analysis_translation_script", "to_alias": "A1"},
    {"from_node": "analysis_translation_script", "from_role": "result", "to_node": "image_collection", "to_alias": "A1"},
    {"from_node": "analysis_translation_script", "from_role": "result", "to_node": "render_and_qa", "to_alias": "A1"},
    {"from_node": "image_collection", "from_role": "image_bundle", "to_node": "render_and_qa", "to_alias": "A2"},
]


def _snapshot() -> dict:
    return {
        "project_id": PROJECT_ID,
        "project_version": 1,
        "project_definition": {
            "name": PROJECT_NAME,
            "nodes": [{"node_id": node_id, "task_id": task_id} for node_id, task_id in NODES],
            "connections": deepcopy(CONNECTIONS),
            "output_selection": [
                {"node_id": "render_and_qa", "role": "report_json"},
                {"node_id": "render_and_qa", "role": "final_report"},
                {"node_id": "render_and_qa", "role": "assets_bundle"},
            ],
        },
    }


def _step(node_id: str, status: str, *, index: int, error_code: str | None = None) -> dict:
    task_run_id = f"task-run-{node_id}"
    started = f"2026-08-09T07:{5 + index:02d}:00+00:00" if status not in {"blocked", "pending"} else None
    completed = f"2026-08-09T07:{5 + index:02d}:45+00:00" if status == "completed" else None
    return {
        "project_run_id": "project-run-case",
        "node_id": node_id,
        "task_id": dict(NODES).get(node_id, f"task-{node_id}"),
        "task_version": 4,
        "status": status,
        "active_task_run_id": task_run_id if started else None,
        "attempt_count": 1,
        "worker_override": None,
        "error_code": error_code,
        "error_message": error_code,
        "started_at": started,
        "completed_at": completed,
    }


def _receipt(steps: list[dict], *, retry_node: str | None = None) -> dict:
    receipt_steps = []
    for step in steps:
        node_id = step["node_id"]
        if step["status"] in {"blocked", "pending"}:
            attempts = []
        elif node_id == retry_node:
            attempts = [
                {
                    "step_attempt": 1,
                    "status": "failed",
                    "worker": "antigravity",
                    "created_at": "2026-08-09T07:05:00+00:00",
                    "completed_at": "2026-08-09T07:05:12+00:00",
                    "error_code": "DAEMON_RESTARTED",
                },
                {
                    "step_attempt": 2,
                    "status": "completed",
                    "worker": "antigravity",
                    "created_at": "2026-08-09T07:05:20+00:00",
                    "completed_at": "2026-08-09T07:06:00+00:00",
                },
            ]
        else:
            attempts = [
                {
                    "step_attempt": 1,
                    "status": step["status"],
                    "worker": "antigravity",
                    "created_at": step["started_at"],
                    "completed_at": step["completed_at"],
                    "error_code": step.get("error_code"),
                }
            ]
        receipt_steps.append({"node_id": node_id, "task_runs": attempts, "resolved_inputs": []})
    return {"project_run_id": "project-run-case", "status": "completed", "steps": receipt_steps}


def _base_case(status: str, *, completed: int, failed: int, blocked: int, failed_node_id: str | None, error_code: str | None) -> dict:
    return {
        "catalog_item": {
            "project_run_id": "project-run-case",
            "project_id": PROJECT_ID,
            "project_name": PROJECT_NAME,
            "project_version": 1,
            "status": status,
            "step_count": len(NODES),
            "completed_step_count": completed,
            "failed_step_count": failed,
            "blocked_step_count": blocked,
            "failed_node_id": failed_node_id,
            "error_code": error_code,
            "final_artifact_count": 0,
            "final_artifact_ids": [],
            "trigger_type": "manual",
            "created_at": "2026-08-09T07:05:00+00:00",
            "started_at": "2026-08-09T07:05:00+00:00",
            "completed_at": "2026-08-09T07:14:47+00:00",
        },
        "snapshot": _snapshot(),
        "steps": [],
        "receipt": {"project_run_id": "project-run-case", "status": status, "steps": []},
        "task_run_details": {},
        "artifacts": [],
    }


def _with_steps(case: dict, steps: list[dict], *, retry_node: str | None = None) -> dict:
    case["steps"] = steps
    case["receipt"] = _receipt(steps, retry_node=retry_node)
    case["task_run_details"] = {
        step["node_id"]: {
            "job_id": step["active_task_run_id"],
            "task_run_id": step["active_task_run_id"],
            "requested_worker": "antigravity",
            "actual_worker": "antigravity",
            "status": step["status"].upper(),
            "error_code": step.get("error_code"),
        }
        for step in steps
        if step.get("active_task_run_id")
    }
    return case


def project_run_case(name: str) -> dict:
    """Return an independent deterministic case by its scenario name."""
    if name == "success_parallel":
        case = _base_case("completed", completed=6, failed=0, blocked=0, failed_node_id=None, error_code=None)
        steps = [_step(node_id, "completed", index=index) for index, (node_id, _) in enumerate(NODES)]
        case = _with_steps(case, steps)
        case["catalog_item"]["final_artifact_count"] = 3
        case["catalog_item"]["final_artifact_ids"] = [
            {"node_id": "render_and_qa", "role": role, "artifact_uid": f"artifact-{role}"}
            for role in ("report_json", "final_report", "assets_bundle")
        ]
        case["artifacts"] = deepcopy(case["catalog_item"]["final_artifact_ids"])
        return case

    if name == "schema_failure_blocked":
        case = _base_case(
            "failed", completed=4, failed=1, blocked=1, failed_node_id="image_collection", error_code="SCHEMA_MISMATCH"
        )
        steps = [_step(node_id, "completed", index=index) for index, (node_id, _) in enumerate(NODES[:4])]
        steps.extend(
            [
                _step("image_collection", "failed", index=4, error_code="SCHEMA_MISMATCH"),
                _step("render_and_qa", "blocked", index=5),
            ]
        )
        return _with_steps(case, steps)

    if name == "unsupported_worker_blocked":
        case = _base_case(
            "failed", completed=0, failed=2, blocked=4, failed_node_id="media_research", error_code="UNSUPPORTED_WORKER"
        )
        steps = [
            _step("official_research", "failed", index=0, error_code="UNSUPPORTED_WORKER"),
            _step("media_research", "failed", index=1, error_code="UNSUPPORTED_WORKER"),
            *[_step(node_id, "blocked", index=index) for index, (node_id, _) in enumerate(NODES[2:], start=2)],
        ]
        case = _with_steps(case, steps)
        case["task_run_details"]["media_research"] = {
            "task_run_id": "task-run-media_research",
            "requested_worker": "agy",
            "actual_worker": None,
            "status": "FAILED",
            "error_code": "UNSUPPORTED_WORKER",
        }
        return case

    if name == "cancelled":
        case = _base_case("cancelled", completed=2, failed=0, blocked=0, failed_node_id=None, error_code="CANCELLED")
        steps = [
            _step("official_research", "completed", index=0),
            _step("media_research", "completed", index=1),
            _step("verify_merge_select", "cancelled", index=2, error_code="CANCELLED"),
            *[_step(node_id, "pending", index=index) for index, (node_id, _) in enumerate(NODES[3:], start=3)],
        ]
        return _with_steps(case, steps)

    if name == "awaiting_approval":
        case = _base_case("awaiting_approval", completed=2, failed=0, blocked=3, failed_node_id=None, error_code=None)
        steps = [
            _step("official_research", "completed", index=0),
            _step("media_research", "completed", index=1),
            _step("verify_merge_select", "awaiting_approval", index=2),
            *[_step(node_id, "blocked", index=index) for index, (node_id, _) in enumerate(NODES[3:], start=3)],
        ]
        case = _with_steps(case, steps)
        case["approvals"] = [{"node_id": "verify_merge_select", "token": "approval-token", "status": "pending"}]
        return case

    if name == "retry_then_success":
        case = _base_case("completed", completed=2, failed=0, blocked=0, failed_node_id=None, error_code=None)
        steps = [_step("official_research", "completed", index=0), _step("media_research", "completed", index=1)]
        steps[0]["attempt_count"] = 2
        case = _with_steps(case, steps, retry_node="official_research")
        return case

    raise KeyError(f"Unknown Project Run case: {name}")
