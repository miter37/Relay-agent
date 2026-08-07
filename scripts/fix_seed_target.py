import pathlib
p = pathlib.Path("tests/test_phase5_cli.py")
t = p.read_text(encoding="utf-8")
old_seed = (
    "    def _seed_routine(self, name=\"Daily\"):\n"
    "        return self.client.request(\n"
    '            "POST", "/v1/routines", {"name": name, "target_type": "task", "target_id": "demo-task", "rule": {"type": "daily", "times": [\\"09:00\\"], "timezone": \\"UTC\\"}}'
    ")\n"
)
new_seed = (
    "    def _seed_routine(self, name=\"Daily\"):\n"
    "        from relay.models import TaskSpec\n"
    "        task = self.engine.create_task(TaskSpec(name=\"DemoTask-\" + name, instructions=\"do work\"))\n"
    "        routine = self.engine.routine_service.create_routine("
    "            {\"name\": name, \"target_type\": \"task\", \"target_id\": task[\"task_id\"], \"rule\": {\"type\": \"daily\", \"times\": [\"09:00\"], \"timezone\": \"UTC\"}}\n"
    "        )\n"
    "        return {\"ok\": True, \"routine\": routine}\n"
)
if old_seed in t:
    t = t.replace(old_seed, new_seed, 1)
    p.write_text(t, encoding="utf-8")
    print("updated _seed_routine")
else:
    print("not found")
