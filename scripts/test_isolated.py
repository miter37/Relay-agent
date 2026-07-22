from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TESTS = [
    "tests.test_relay.RelayTests.test_deep_doctor_and_antigravity_opt_in",
    "tests.test_relay.RelayTests.test_sync_json_delivery",
    "tests.test_relay.RelayTests.test_fallback_to_codex",
    "tests.test_relay.RelayTests.test_exact_dedup",
    "tests.test_relay.RelayTests.test_daemon_submit",
]

failed = []
for test in TESTS:
    print(f"=== {test} ===", flush=True)
    cp = subprocess.run([sys.executable, "-m", "unittest", test, "-v"], cwd=ROOT)
    if cp.returncode:
        failed.append(test)

if failed:
    print("FAILED:")
    for test in failed:
        print(f"- {test}")
    raise SystemExit(1)
print("All isolated tests passed.")
