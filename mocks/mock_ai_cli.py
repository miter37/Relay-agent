#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path


def provider_name() -> str:
    name = Path(sys.argv[0]).stem.lower()
    if "claude" in name:
        return "claude"
    if "codex" in name:
        return "codex"
    if "agy" in name or "antigravity" in name:
        return "antigravity"
    return os.environ.get("RELAY_MOCK_PROVIDER", "claude")


provider = os.environ.get("RELAY_PROVIDER_NAME") or provider_name()
args = sys.argv[1:]
if any(x in args for x in ("--version", "-V")) or (args and args[0] == "version"):
    print(f"{provider} mock 9.9.9")
    raise SystemExit(0)
if "--help" in args or "-h" in args:
    flags = {
        "claude": "-p --output-format --permission-mode --json-schema --max-turns --tools --no-session-persistence",
        "codex": "exec --sandbox --ask-for-approval --output-last-message --output-schema --ephemeral --search",
        "antigravity": "-p --dangerously-skip-permissions --model",
    }
    print(flags[provider])
    raise SystemExit(0)

behavior = os.environ.get(f"RELAY_MOCK_{provider.upper()}_BEHAVIOR", os.environ.get("RELAY_MOCK_BEHAVIOR", "success"))
if behavior == "crash":
    print("mock provider crashed", file=sys.stderr)
    raise SystemExit(7)
if behavior == "empty":
    raise SystemExit(0)
if behavior == "stall":
    time.sleep(3600)

cwd = Path.cwd()
result_path = os.environ.get("RELAY_STAGING_RESULT")
if provider == "codex" and "--output-last-message" in args:
    result_path = args[args.index("--output-last-message") + 1]
result = Path(result_path) if result_path else cwd / "output" / "result.json.partial"
result.parent.mkdir(parents=True, exist_ok=True)
artifact_dir = Path(os.environ.get("RELAY_ARTIFACT_DIR", str(cwd / "artifacts")))
artifact_dir.mkdir(parents=True, exist_ok=True)
(artifact_dir / "probe-artifact.txt").write_text("RELAY_ARTIFACT_OK", encoding="utf-8")
(artifact_dir / "research-notes.txt").write_text("Mock research notes", encoding="utf-8")
status = "partial" if behavior == "partial" else "complete"
value = {
    "schema_version": "1.0",
    "status": status,
    "answer": "RELAY_UNATTENDED_OK" if "doctor-" in os.environ.get("RELAY_JOB_ID", "") else f"Mock answer from {provider}",
    "sources": [{"title": "Mock source", "url": "https://example.com", "publisher": "Example", "published_at": "2026-07-14"}],
    "uncertainties": ["Mock uncertainty"] if status == "partial" else [],
    "missing_items": [],
    "artifacts": [
        {"name": "probe-artifact.txt", "relative_path": "probe-artifact.txt", "description": "probe"},
        {"name": "research-notes.txt", "relative_path": "research-notes.txt", "description": "notes"},
    ],
}
fmt = os.environ.get("RELAY_RESULT_FORMAT", "json")
if fmt == "txt":
    text = f"Mock text answer from {provider}\n"
    result.write_text(text, encoding="utf-8")
    print(text)
else:
    text = json.dumps(value, ensure_ascii=False)
    if provider == "codex" or provider == "antigravity":
        result.write_text(text, encoding="utf-8")
        print(text)
    else:
        # Claude wrapper shape.
        print(json.dumps({"type": "result", "result": text, "structured_output": value}, ensure_ascii=False))
raise SystemExit(0)
