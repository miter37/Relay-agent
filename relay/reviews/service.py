from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..config import Config
from ..db import Database
from ..engine import RelayEngine
from ..errors import RelayError
from ..target_workspace import TargetWorkspace, apply_delta, safe_resolve
from ..util import new_job_id, utc_now

_ACTIONABLE = {"pending_human", "needs_human", "delivery_failed"}


class ReviewService:
    """Own the durable review session around one or more Task Run rounds."""

    def __init__(self, db: Database, engine: RelayEngine, config: Config):
        self.db = db
        self.engine = engine
        self.config = config

    def create_task_review(
        self,
        task_run_id: str,
        *,
        reviewer: str = "human",
        max_reruns: int = 0,
        review_id: str | None = None,
    ) -> dict[str, Any]:
        job = self.db.get_job(task_run_id)
        if not job:
            raise RelayError("JOB_NOT_FOUND", f"Task Run not found: {task_run_id}")
        review_id = review_id or job.get("review_id") or new_job_id()
        existing = self.db.get_review_session(review_id)
        if existing:
            if not any(r.get("task_run_id") == task_run_id for r in self.db.list_review_rounds(review_id)):
                round_no = int(existing.get("current_round") or 1)
                self.db.create_review_round(
                    {"review_id": review_id, "round_no": round_no, "task_run_id": task_run_id, "status": "pending"}
                )
            self.db.update_job(task_run_id, review_id=review_id, review_status="pending_human")
            self.db.update_review_session(
                review_id, status="pending_human", current_round=int(existing.get("current_round") or 1)
            )
            return self.get(review_id)
        policy = self._decode_policy(job.get("review_policy_json")) or {}
        reviewer = str(policy.get("reviewer") or reviewer)
        max_reruns = int(policy.get("max_reruns") or max_reruns or 0)
        if reviewer not in {"human", "orchestrator"}:
            raise RelayError("REVIEW_INVALID", "reviewer must be human or orchestrator.")
        if max_reruns < 0 or max_reruns > 20:
            raise RelayError("REVIEW_INVALID", "max_reruns must be between 0 and 20.")
        self.db.create_review_session(
            {
                "review_id": review_id,
                "scope_type": "task",
                "task_run_id": task_run_id,
                "reviewer": reviewer,
                "status": "pending_human" if reviewer == "human" else "evaluating",
                "guidelines": policy.get("guidelines"),
                "max_reruns": max_reruns,
                "current_round": 1,
            }
        )
        self.db.create_review_round(
            {"review_id": review_id, "round_no": 1, "task_run_id": task_run_id, "status": "pending"}
        )
        self.db.update_job(task_run_id, review_id=review_id, review_status="pending_human")
        return self.get(review_id)

    def create_project_review(
        self, project_run_id: str, node_id: str, task_run_id: str, checkpoint: dict[str, Any]
    ) -> dict[str, Any]:
        existing = self.db.review_session_for_project_node(project_run_id, node_id)
        if existing:
            rounds = self.db.list_review_rounds(existing["review_id"])
            if not any(r.get("task_run_id") == task_run_id for r in rounds):
                approval = self._create_legacy_approval(project_run_id, node_id)
                round_no = int(existing.get("current_round") or 1)
                self.db.create_review_round(
                    {"review_id": existing["review_id"], "round_no": round_no, "task_run_id": task_run_id}
                )
                self.db.update_review_session(
                    existing["review_id"],
                    approval_token=approval["token"],
                    status="evaluating" if existing.get("reviewer") == "orchestrator" else "pending_human",
                )
                self._mark_candidate(task_run_id, existing["review_id"])
            self.db.update_project_step(
                project_run_id, node_id, review_id=existing["review_id"], status="awaiting_review"
            )
            return self.get(existing["review_id"])
        reviewer = str(checkpoint.get("reviewer") or "human")
        review_id = new_job_id()
        approval = self._create_legacy_approval(project_run_id, node_id)
        self.db.create_review_session(
            {
                "review_id": review_id,
                "scope_type": "project",
                "project_run_id": project_run_id,
                "node_id": node_id,
                "approval_token": approval["token"],
                "reviewer": reviewer,
                "status": "pending_human" if reviewer == "human" else "evaluating",
                "guidelines": checkpoint.get("guidelines"),
                "max_reruns": int(checkpoint.get("max_reruns") if checkpoint.get("max_reruns") is not None else 2),
                "current_round": 1,
            }
        )
        self.db.create_review_round({"review_id": review_id, "round_no": 1, "task_run_id": task_run_id})
        self.db.update_project_step(project_run_id, node_id, review_id=review_id, status="awaiting_review")
        self._mark_candidate(task_run_id, review_id)
        return self.get(review_id)

    def _mark_candidate(self, task_run_id: str, review_id: str) -> None:
        self.db.update_job(task_run_id, review_id=review_id, review_status="pending_human")
        for artifact in self.db.artifacts_for_job(task_run_id):
            if artifact.get("artifact_uid"):
                self.db.update_artifact_publication(artifact["artifact_uid"], "candidate")

    def _publish_project_round(self, current: dict[str, Any]) -> None:
        for artifact in self.db.artifacts_for_job(current["task_run_id"]):
            if artifact.get("artifact_uid"):
                self.db.update_artifact_publication(artifact["artifact_uid"], "published")
        self.engine._refresh_search_index(current["task_run_id"])

    def _create_legacy_approval(self, project_run_id: str, node_id: str) -> dict[str, Any]:
        from ..approvals.service import ApprovalService

        return ApprovalService(self.db, self.engine, self.config).create_pending_approval(project_run_id, node_id)

    def get(self, review_id: str) -> dict[str, Any]:
        session = self.db.get_review_session(review_id)
        if not session:
            raise RelayError("REVIEW_NOT_FOUND", f"Review not found: {review_id}")
        rounds = self.db.list_review_rounds(review_id)
        current = rounds[-1] if rounds else None
        task_run = self.db.get_job(current["task_run_id"]) if current else None
        artifacts = self.db.artifacts_for_job(current["task_run_id"]) if current else []
        candidate_result = None
        if task_run:
            root = Path(str(task_run.get("review_candidate_root") or ""))
            candidate = root / Path(str(task_run.get("output_path") or "result.txt")).name
            if not candidate.is_file() and session.get("scope_type") == "project":
                candidate = Path(str(task_run.get("output_path") or ""))
            if candidate.is_file():
                try:
                    candidate_result = {
                        "path": str(candidate),
                        "text": candidate.read_text(encoding="utf-8", errors="replace")[:262144],
                        "truncated": candidate.stat().st_size > 262144,
                    }
                except OSError:
                    candidate_result = None
        return {
            "ok": True,
            "review": session,
            "rounds": rounds,
            "current_round": current,
            "task_run": task_run,
            "artifacts": artifacts,
            "candidate_result": candidate_result,
        }

    def list(self, *, status: str | None = None, limit: int = 100) -> dict[str, Any]:
        sessions = self.db.list_review_sessions(status=status, limit=limit)
        return {"ok": True, "reviews": [self._summary(item) for item in sessions]}

    def confirm(self, review_id: str, *, reviewer: str = "human") -> dict[str, Any]:
        session, current = self._pending(review_id, allow_evaluating=reviewer == "orchestrator")
        if session.get("scope_type") == "project":
            from ..approvals.service import ApprovalService

            token = session.get("approval_token")
            if not token:
                raise RelayError("REVIEW_INVALID", "Project review approval token is missing.")
            ApprovalService(self.db, self.engine, self.config).approve(
                session["project_run_id"], token, reviewer=reviewer
            )
            now = utc_now()
            self.db.update_review_round(review_id, int(current["round_no"]), status="approved", decided_at=now)
            self.db.update_review_session(review_id, status="approved", decided_at=now)
            self._publish_project_round(current)
            self.db.update_job(current["task_run_id"], review_id=review_id, review_status="approved")
            return self.get(review_id)
        task_run = self.db.get_job(current["task_run_id"])
        if not task_run:
            raise RelayError("JOB_NOT_FOUND", "Review Task Run is missing.")
        try:
            self._publish_task_run(task_run)
        except RelayError as exc:
            self.db.update_review_session(review_id, status="delivery_failed")
            self.db.update_job(task_run["job_id"], review_status="delivery_failed")
            raise exc
        now = utc_now()
        self.db.update_review_round(review_id, int(current["round_no"]), status="approved", decided_at=now)
        self.db.update_review_session(review_id, status="approved", decided_at=now)
        self.db.update_job(task_run["job_id"], review_status="approved")
        return self.get(review_id)

    def evaluate_orchestrator(self, review_id: str) -> dict[str, Any]:
        """Run a bounded, fail-closed automatic review for a Project node."""
        session = self.db.get_review_session(review_id)
        rounds = self.db.list_review_rounds(review_id)
        current = rounds[-1] if rounds else None
        if not session or not current:
            raise RelayError("REVIEW_NOT_FOUND", f"Review not found: {review_id}")
        if session.get("status") != "evaluating":
            return self.get(review_id)
        if session.get("scope_type") != "project" or session.get("reviewer") != "orchestrator":
            return self.get(review_id)
        job = self.db.get_job(current.get("task_run_id"))
        if not job:
            return self._handoff(session, current, "The completed Task Run is unavailable.")
        evidence = self._review_evidence(job)
        if evidence is None:
            return self._handoff(session, current, "The result evidence is missing or unreadable.")
        for artifact in self.db.artifacts_for_job(job["job_id"]):
            path = Path(str(artifact.get("final_path") or ""))
            item = {
                "artifact_uid": artifact.get("artifact_uid"),
                "relative_path": artifact.get("relative_path"),
                "role": artifact.get("role"),
                "size": artifact.get("size"),
                "sha256": artifact.get("sha256"),
                "available": path.is_file(),
            }
            if path.is_file():
                try:
                    item["content"] = path.read_text(encoding="utf-8", errors="replace")[:16384]
                except OSError:
                    item["available"] = False
            if not item["available"]:
                return self._handoff(session, current, "A result artifact is missing or unreadable.")
            evidence["artifacts"].append(item)
        try:
            from ..orchestrator.agent import OrchestratorAgent
            from ..orchestrator.supervisor import Supervisor

            config = Supervisor(self.db, self.engine).orchestrator_config(session["project_run_id"]) or {}
            decision = OrchestratorAgent(
                self.engine,
                worker=config.get("worker"),
                model=config.get("model"),
                profile=config.get("profile"),
            ).review(
                node_id=str(session.get("node_id") or ""),
                guidelines=str(session.get("guidelines") or ""),
                evidence=evidence,
            )
        except Exception as exc:  # noqa: BLE001 - automatic review always fails closed
            return self._handoff(session, current, f"Automatic review could not be completed: {exc}")
        evaluation = json.dumps(decision, ensure_ascii=False)
        self.db.update_review_round(review_id, int(current["round_no"]), evaluation_json=evaluation)
        if decision["decision"] == "approve":
            return self.confirm(review_id, reviewer="orchestrator")
        if decision["decision"] == "rerun":
            if int(session.get("reruns_used") or 0) >= int(session.get("max_reruns") or 0):
                return self._handoff(session, current, "Automatic rerun limit reached; human review is required.")
            return self.rerun(review_id, decision["comment"] or decision["reason"])
        return self._handoff(session, current, decision["reason"])

    def _handoff(self, session: dict[str, Any], current: dict[str, Any], reason: str) -> dict[str, Any]:
        now = utc_now()
        self.db.update_review_round(
            session["review_id"], int(current["round_no"]), status="needs_human", comment=reason
        )
        self.db.update_job(current["task_run_id"], review_status="needs_human")
        self.db.update_review_session(session["review_id"], status="needs_human", updated_at=now)
        return self.get(session["review_id"])

    @staticmethod
    def _review_evidence(job: dict[str, Any]) -> dict[str, Any] | None:
        evidence: dict[str, Any] = {
            "job_id": job.get("job_id"),
            "status": job.get("status"),
            "result_status": job.get("result_status"),
            "result_summary": job.get("result_summary"),
            "receipt": ReviewService._decode_json(job.get("receipt_json")),
            "artifacts": [],
        }
        output = Path(str(job.get("output_path") or ""))
        if not output.is_file():
            return None
        try:
            evidence["result"] = output.read_text(encoding="utf-8", errors="replace")[:32768]
        except OSError:
            return None
        # The caller supplies artifact metadata/content through the ordinary DB row;
        # this method is intentionally conservative about the amount sent to the model.
        return evidence

    def reject(self, review_id: str, reason: str) -> dict[str, Any]:
        session, current = self._pending(review_id)
        reason = str(reason or "").strip()
        if not reason:
            raise RelayError("REVIEW_INVALID", "A rejection reason is required.")
        if session.get("scope_type") == "project":
            from ..approvals.service import ApprovalService

            ApprovalService(self.db, self.engine, self.config).reject(
                session["project_run_id"], session.get("approval_token"), reviewer="human", reason=reason
            )
        now = utc_now()
        self.db.update_review_round(
            review_id, int(current["round_no"]), status="rejected", comment=reason, decided_at=now
        )
        self.db.update_review_session(review_id, status="rejected", decided_at=now)
        task_run_id = current["task_run_id"]
        self.db.update_job(task_run_id, review_status="rejected")
        for artifact in self.db.artifacts_for_job(task_run_id):
            if artifact.get("artifact_uid"):
                self.db.update_artifact_publication(artifact["artifact_uid"], "rejected")
        return self.get(review_id)

    def rerun(self, review_id: str, comment: str) -> dict[str, Any]:
        session, current = self._pending(review_id, allow_evaluating=True)
        comment = str(comment or "").strip()
        if not comment:
            raise RelayError("REVIEW_INVALID", "A comment is required before re-running.")
        if len(comment) > 4000:
            raise RelayError("REVIEW_INVALID", "Review comment must be 4000 characters or fewer.")
        if session["reviewer"] == "orchestrator" and int(session.get("reruns_used") or 0) >= int(
            session.get("max_reruns") or 0
        ):
            raise RelayError("REVIEW_RERUN_LIMIT", "The automatic review rerun limit has been reached.")
        if session.get("scope_type") == "project":
            if session.get("approval_token"):
                self.db.update_approval(session["approval_token"], status="superseded", reason=comment)
            self.engine.project_service.partial_reexecute(
                session["project_run_id"],
                from_node=session["node_id"],
                cascade=True,
                instruction_addendum=comment,
            )
            next_round = int(session.get("current_round") or 1) + 1
            self.db.update_review_session(
                review_id,
                status="revision_queued",
                current_round=next_round,
                reruns_used=int(session.get("reruns_used") or 0) + 1,
            )
            return self.get(review_id)
        source = self.db.get_job(current["task_run_id"])
        if not source:
            raise RelayError("JOB_NOT_FOUND", "Review Task Run is missing.")
        request = self._request_for_rerun(source, comment, review_id)
        snapshot = json.loads(source.get("task_snapshot_json") or "{}")
        new_job, _reused = self.engine.run_task_from_snapshot(
            snapshot,
            request=request,
            queued=True,
            submitted_via="gui",
            caller="human",
        )
        next_round = int(session.get("current_round") or 1) + 1
        self.db.update_review_round(review_id, int(current["round_no"]), status="rerun_requested", comment=comment)
        self.db.update_review_session(
            review_id,
            status="revision_queued",
            current_round=next_round,
            reruns_used=int(session.get("reruns_used") or 0) + 1,
        )
        self.db.update_job(new_job["job_id"], review_id=review_id, review_status="revision_queued")
        return self.get(review_id)

    def retry_delivery(self, review_id: str) -> dict[str, Any]:
        session = self.db.get_review_session(review_id)
        if not session or session.get("status") != "delivery_failed":
            raise RelayError("REVIEW_INVALID", "This review does not have a retryable delivery failure.")
        return self.confirm(review_id)

    def _publish_task_run(self, job: dict[str, Any]) -> None:
        root = Path(str(job.get("review_candidate_root") or ""))
        candidate_output = root / Path(job["output_path"]).name
        candidate_artifacts = root / "artifacts"
        if not candidate_output.is_file() or not candidate_artifacts.is_dir():
            raise RelayError("DELIVERY_FAILED", "Review candidate files are missing.")
        from ..delivery import atomic_deliver_pair

        raw_delta = job.get("review_target_delta_json")
        manifest = json.loads(raw_delta) if raw_delta else None
        delta = None
        target_workspace = None
        if manifest:
            from ..target_workspace import TargetDelta, _verify_no_conflicts

            delta_root = root / "target-delta"
            target = safe_resolve(Path(manifest["target"]))
            delta = TargetDelta(
                tuple(manifest["delta"].get("added", [])),
                tuple(manifest["delta"].get("modified", [])),
                tuple(manifest["delta"].get("deleted", [])),
            )
            target_workspace = TargetWorkspace(target, delta_root, bool(manifest.get("existed")), manifest["baseline"])
            _verify_no_conflicts(target_workspace, delta)

        atomic_deliver_pair(
            candidate_output,
            safe_resolve(Path(job["output_path"])),
            candidate_artifacts,
            safe_resolve(Path(job["artifact_path"])),
            overwrite=True,
        )
        if target_workspace and delta:
            apply_delta(target_workspace, delta)
        for artifact in self.db.artifacts_for_job(job["job_id"]):
            if artifact.get("artifact_uid"):
                self.db.update_artifact_publication(artifact["artifact_uid"], "published")
                destination = (
                    Path(str(job["output_path"]))
                    if artifact.get("role") == "result"
                    else Path(str(job["artifact_path"])) / str(artifact.get("relative_path") or "")
                )
                self.db.update_artifact_path(artifact["artifact_uid"], str(destination))
        receipt = self._decode_json(job.get("receipt_json"))
        receipt.update(
            {
                "review_status": "approved",
                "delivery_status": "delivered",
                "result_path": job["output_path"],
                "artifact_path": job["artifact_path"],
            }
        )
        self.db.update_job(
            job["job_id"], receipt_json=json.dumps(receipt, ensure_ascii=False), review_status="approved"
        )
        self.engine._refresh_search_index(job["job_id"])

    def _pending(self, review_id: str, *, allow_evaluating: bool = False) -> tuple[dict[str, Any], dict[str, Any]]:
        session = self.db.get_review_session(review_id)
        if not session:
            raise RelayError("REVIEW_NOT_FOUND", f"Review not found: {review_id}")
        allowed = set(_ACTIONABLE)
        if allow_evaluating:
            allowed.add("evaluating")
        if session.get("status") not in allowed:
            raise RelayError("REVIEW_ALREADY_DECIDED", f"Review is already {session.get('status')}.")
        rounds = self.db.list_review_rounds(review_id)
        if not rounds:
            raise RelayError("REVIEW_INVALID", "Review has no current round.")
        return session, rounds[-1]

    @staticmethod
    def _request_for_rerun(source: dict[str, Any], comment: str, review_id: str):
        from ..models import JobRequest

        request = JobRequest.from_dict(json.loads(source.get("request_json") or "{}"))
        request.task = f"{request.task}\n\nReview feedback for this revision:\n{comment}"
        request.request_id = None
        request.force_new = True
        request.output_path = None
        request.artifact_path = None
        request.review_mode = "human"
        request.review_id = review_id
        request.caller = "human"
        return request

    @staticmethod
    def _decode_json(value: Any) -> dict[str, Any]:
        try:
            parsed = json.loads(value or "{}")
        except (TypeError, json.JSONDecodeError):
            return {}
        return parsed if isinstance(parsed, dict) else {}

    _decode_policy = _decode_json

    def _summary(self, session: dict[str, Any]) -> dict[str, Any]:
        rounds = self.db.list_review_rounds(session["review_id"])
        current = rounds[-1] if rounds else {}
        job = self.db.get_job(current.get("task_run_id")) if current.get("task_run_id") else None
        return {
            **session,
            "current_task_run_id": current.get("task_run_id"),
            "task_title": job.get("title") if job else None,
            "round_count": len(rounds),
        }
