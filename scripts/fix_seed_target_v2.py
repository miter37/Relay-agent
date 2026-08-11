import pathlib

p = pathlib.Path("tests/test_phase5_cli.py")
t = p.read_text(encoding="utf-8")
old_block = (
    '    def _seed_routine(self, name="Daily"):\n'
    '        return self.client.request("POST", "/v1/routines", {"name": name, "target_type": "task", "target_id": "demo-task", "rule": {"type": "daily", "times": ["09:00"], "timezone": "UTC"}})\n'
)
new_block = (
    '    def _seed_routine(self, name="Daily"):\n'
    "        from relay.models import TaskSpec\n"
    "        task = self.engine.create_task(\n"
    '            TaskSpec(name=f"DemoTask-{name}", instructions="do work")\n'
    "        )\n"
    "        routine = self.engine.routine_service.create_routine(\n"
    "            {\n"
    '                "name": name,\n'
    '                "target_type": "task",\n'
    '                "target_id": task["task_id"],\n'
    '                "rule": {"type": "daily", "times": ["09:00"], "timezone": "UTC"},\n'
    "            }\n"
    "        )\n"
    '        return {"ok": True, "routine": routine}\n'
)
if old_block in t:
    t = t.replace(old_block, new_block, 1)
    p.write_text(t, encoding="utf-8")
    print("updated")
else:
    print("not found")
