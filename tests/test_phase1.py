from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from relay.api import artifact_detail, artifact_lineage, run_lineage
from relay.config import Config
from relay.db import Database
from relay.engine import RelayEngine
from relay.errors import RelayError
from relay.models import JobRequest


class Phase1Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name) / "home"
        self.config = Config(self.home)
        self.config.init()
        self.db = Database(self.config.path_value("database_path"))
        self.engine = RelayEngine(self.config, self.db)

    def tearDown(self):
        self.temp.cleanup()

    def _source_artifact(self) -> tuple[str, Path, str]:
        source_job, _ = self.engine.create_job(JobRequest(task="Source", worker="codex"), queued=True)
        source = Path(self.config.path_value("artifact_root")) / source_job["job_id"] / "report.md"
        source.parent.mkdir(parents=True)
        source.write_text("original report", encoding="utf-8")
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        self.db.add_artifact(
            source_job["job_id"],
            relative_path="report.md",
            final_path=str(source),
            mime_type="text/markdown",
            size=source.stat().st_size,
            sha256=digest,
            artifact_uid="artifact-source-1",
            role="output",
        )
        return source_job["job_id"], source, digest

    def test_create_job_snapshots_artifact_and_records_lineage(self):
        source_job_id, source, digest = self._source_artifact()
        consumer, _ = self.engine.create_job(
            JobRequest(
                task="Use the previous report",
                worker="codex",
                artifact_inputs=[{"artifact_uid": "artifact-source-1", "alias": "A1"}],
            ),
            queued=True,
        )
        manifest = json.loads(consumer["input_manifest_json"])
        self.assertEqual(manifest[0]["alias"], "A1")
        self.assertEqual(manifest[0]["source_job_id"], source_job_id)
        self.assertEqual(manifest[0]["source_sha256"], digest)
        snapshot = self.home / manifest[0]["snapshot_relative_path"]
        self.assertEqual(snapshot.read_text(encoding="utf-8"), "original report")
        source.write_text("changed after submit", encoding="utf-8")
        lineage = self.db.lineage_for_job(consumer["job_id"])
        self.assertEqual(lineage[0]["source_artifact_uid"], "artifact-source-1")
        self.assertEqual(lineage[0]["snapshot_sha256"], digest)

    def test_artifact_inputs_reject_duplicate_alias_and_changed_source(self):
        _, source, _ = self._source_artifact()
        with self.assertRaisesRegex(RelayError, "duplicated"):
            self.engine.create_job(
                JobRequest(
                    task="bad aliases",
                    artifact_inputs=[
                        {"artifact_uid": "artifact-source-1", "alias": "A1"},
                        {"artifact_uid": "artifact-source-1", "alias": "A1"},
                    ],
                ),
                queued=True,
            )
        source.write_text("tampered", encoding="utf-8")
        with self.assertRaisesRegex(RelayError, "changed"):
            self.engine.create_job(
                JobRequest(
                    task="bad source",
                    artifact_inputs=[{"artifact_uid": "artifact-source-1", "alias": "A1"}],
                ),
                queued=True,
            )

    def test_lineage_api_exposes_source_and_consumer(self):
        source_job_id, _, _ = self._source_artifact()
        consumer, _ = self.engine.create_job(
            JobRequest(
                task="Use source",
                artifact_inputs=[{"artifact_uid": "artifact-source-1", "alias": "A1"}],
            ),
            queued=True,
        )
        self.assertEqual(run_lineage(self.db, consumer["job_id"])["inputs"][0]["alias"], "A1")
        self.assertEqual(artifact_detail(self.db, "artifact-source-1")["artifact"]["job_id"], source_job_id)
        self.assertEqual(
            artifact_lineage(self.db, "artifact-source-1")["consumers"][0]["consumer_job_id"], consumer["job_id"]
        )


if __name__ == "__main__":
    unittest.main()
