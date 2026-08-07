"""A Project-dispatched Task Run must keep the Task's own configured profile.

`_dispatch_step` builds a JobRequest without a profile. Because JobRequest.profile
used to default to the truthy string "web-research", the merge in
`run_task_from_snapshot` (`request.profile or base.profile`) always picked that
default over the Task snapshot's real profile.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from relay.config import Config
from relay.db import Database
from relay.engine import RelayEngine
from relay.models import JobRequest, TaskSpec


class DispatchBuiltRequestPreservesTaskProfileTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.config = Config(Path(self.temp.name) / "home")
        self.config.init()
        self.config.set("service_isolation_acknowledged", True)
        self.db = Database(self.config.path_value("database_path"))
        self.engine = RelayEngine(self.config, self.db)

    def tearDown(self):
        self.temp.cleanup()

    def test_request_without_a_profile_defaults_to_none(self):
        # This is the exact shape _dispatch_step builds: no profile set.
        request = JobRequest(task="x", caller="service", worker="antigravity")
        self.assertIsNone(request.profile)

    def test_snapshot_profile_survives_the_merge_when_request_has_none(self):
        task = self.engine.create_task(TaskSpec(name="t", instructions="x", profile="decision-brief"))
        snapshot = {
            "task_id": task["task_id"],
            "instructions": "x",
            "profile": "decision-brief",
            "default_worker": "antigravity",
        }
        dispatch_request = JobRequest(task="x", caller="service", worker="antigravity")

        job, _reused = self.engine.run_task_from_snapshot(snapshot, request=dispatch_request, queued=True)

        self.assertEqual(job["profile"], "decision-brief")


if __name__ == "__main__":
    unittest.main()
