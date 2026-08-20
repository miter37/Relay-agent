from __future__ import annotations

import hashlib
import socket
import tempfile
import threading
import unittest
from pathlib import Path

from relay.api import artifact_content
from relay.config import Config
from relay.daemon import RelayDaemon
from relay.db import Database
from relay.engine import RelayEngine
from relay.errors import RelayError
from relay.models import JobRequest
from relay.reviews.service import ReviewService
from relay.rpc import RPCClient


class ReviewArtifactContentServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name) / "home"
        self.config = Config(self.home)
        self.config.init()
        self.db = Database(self.config.path_value("database_path"))
        self.engine = RelayEngine(self.config, self.db)
        self.service = ReviewService(self.db, self.engine, self.config)
        self.review_ids: list[str] = []

    def tearDown(self):
        self.temp.cleanup()

    def _candidate_review(self, name: str) -> tuple[str, str]:
        job, _ = self.engine.create_job(
            JobRequest(task=f"produce {name}", review_mode="human", force_new=True), submitted_via="gui"
        )
        candidate_root = self.home / "review-candidates" / job["job_id"]
        artifact_path = candidate_root / "artifacts" / f"{name}.txt"
        artifact_path.parent.mkdir(parents=True)
        artifact_path.write_text(f"candidate-{name}", encoding="utf-8")
        self.db.update_job(
            job["job_id"],
            status="COMPLETED",
            result_status="complete",
            review_status="pending_human",
            review_candidate_root=str(candidate_root),
        )
        self.db.add_artifact(
            job["job_id"],
            relative_path=f"{name}.txt",
            final_path=str(artifact_path),
            mime_type="text/plain",
            size=artifact_path.stat().st_size,
            sha256=hashlib.sha256(artifact_path.read_bytes()).hexdigest(),
            artifact_uid=f"artifact-{name}",
            role="output",
            publication_status="candidate",
        )
        review = self.service.create_task_review(job["job_id"])
        review_id = review["review"]["review_id"]
        self.review_ids.append(review_id)
        return review_id, f"artifact-{name}"

    def test_review_content_is_current_round_scoped(self):
        review_a, artifact_a = self._candidate_review("a")
        review_b, artifact_b = self._candidate_review("b")

        own = self.service.artifact_content(review_a, artifact_a, 4)

        self.assertEqual(own["text"], "cand")
        self.assertTrue(own["truncated"])
        with self.assertRaisesRegex(RelayError, "Artifact not found"):
            self.service.artifact_content(review_a, artifact_b, 262144)
        with self.assertRaisesRegex(RelayError, "Artifact not found"):
            artifact_content(self.db, artifact_a, max_bytes=262144)
        with self.assertRaisesRegex(RelayError, "Review not found"):
            self.service.artifact_content("missing-review", artifact_a, 262144)


class ReviewArtifactContentRouteTests(unittest.TestCase):
    @staticmethod
    def _free_port() -> int:
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        sock.close()
        return port

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name) / "home"
        self.config = Config(self.home)
        self.config.init()
        self.config.set("daemon_port", self._free_port())
        self.daemon = RelayDaemon(self.config)
        self.thread = threading.Thread(target=self.daemon.serve, daemon=True)
        self.thread.start()
        self.client = RPCClient(self.config)
        self.assertTrue(self.client.wait_until_healthy(5.0))
        self.db = Database(self.config.path_value("database_path"))
        self.engine = RelayEngine(self.config, self.db)
        self.service = ReviewService(self.db, self.engine, self.config)

        job, _ = self.engine.create_job(
            JobRequest(task="route candidate", review_mode="human", force_new=True), submitted_via="gui"
        )
        candidate = self.home / "review-candidates" / job["job_id"] / "artifacts" / "route.txt"
        candidate.parent.mkdir(parents=True)
        candidate.write_text("route-candidate", encoding="utf-8")
        self.db.update_job(
            job["job_id"],
            status="COMPLETED",
            result_status="complete",
            review_status="pending_human",
            review_candidate_root=str(candidate.parent.parent),
        )
        self.db.add_artifact(
            job["job_id"],
            relative_path="route.txt",
            final_path=str(candidate),
            mime_type="text/plain",
            size=candidate.stat().st_size,
            sha256=hashlib.sha256(candidate.read_bytes()).hexdigest(),
            artifact_uid="route-artifact",
            role="output",
            publication_status="candidate",
        )
        review = self.service.create_task_review(job["job_id"])
        self.review_id = review["review"]["review_id"]

    def tearDown(self):
        if self.thread.is_alive():
            try:
                self.client.request("POST", "/shutdown")
            except Exception:
                pass
            self.thread.join(timeout=3)
        self.temp.cleanup()

    def test_review_artifact_content_route_is_scoped_and_bounded(self):
        payload = self.client.request(
            "GET",
            f"/v1/reviews/{self.review_id}/artifacts/route-artifact/content?max_bytes=5",
        )

        self.assertEqual(payload["text"], "route")
        self.assertTrue(payload["truncated"])


if __name__ == "__main__":
    unittest.main()
