# Relay 0.5.0 test report

Test date: 2026-07-23

## Environment

- Linux container
- Python 3.12 runtime for tests
- No external Python dependencies
- Mock Claude, Codex, and Antigravity executables

## Automated tests

All 27 tests passed:

1. cleanup preserves recent workspaces
2. cleanup removes expired completed workspaces while preserving delivered results/artifacts
3. cleanup state and due scheduling
4. daemon performs due cleanup on startup
5. daemon submit/result/shutdown
6. deep doctor and Antigravity opt-in policy
7. exact request-id deduplication
8. Claude-to-Codex technical fallback
9. synchronous JSON delivery and artifact manifest
10. request ID conflict rejection
11. concurrent request ID convergence
12. queued cancellation finalization
13. Codex sandbox command safety
14. workspace override handling
15. artifact delivery rollback
16. JSON collection validation
17. Codex output schema compatibility
18. module entrypoint daemon startup
19. UTF-8 artifact payload materialization
20. artifact payload path-escape rejection
21. redundant `artifacts/` prefix normalization
22. failed `run` receipt returns nonzero CLI exit
23. cancelled `wait` receipt returns nonzero CLI exit
24. stable missing-attachment error
25. model verification bypasses unverified cache
26. daemon source import path propagation
27. daemon HTTP error-code preservation

Command:

```sh
PYTHONPATH=. python -m unittest tests.test_relay -v
```

Result:

```text
Ran 27 tests
OK
```

## Live provider verification

- Antigravity (`agy 1.1.5`): deep doctor passed; synchronous artifact creation and asynchronous submit/status/wait/result flows passed.
- Codex (`codex 0.144.1`): deep doctor passed. A live Relay-to-Codex request completed with one materialized UTF-8 artifact, zero missing items, an exact byte-content check, and matching manifest SHA-256.
- Claude task execution: not run because the installed account was quota-limited at test time; model listing and model-check probes were still executed.

Codex JSON artifact output uses a validated content payload. Relay materializes that payload inside the artifact root, rejects unsafe paths and invalid encodings, and removes payload content from the delivered result JSON.

The installed model matrix was also checked: Codex 7/7 and Antigravity 11/11 models returned `model-check ok=true`; Claude's six configured entries produced three verified aliases and three unavailable legacy IDs. Invalid model IDs returned `ok=false` for all three workers. `models --worker all --refresh --include-hidden --verify` completed successfully and refreshed Claude verification instead of reusing an unverified cache.

The Antigravity CLI matrix covered JSON/TXT, task-file, implicit run, fallback/no-fallback, output/artifact paths, all three profiles, timeout, human/Hermes callers, request IDs, attachments, workspace, overwrite/force-new, model selection, machine output, submit/status/wait/result/show/logs/history/rerun, daemon lifecycle, cleanup, and documented path, gating, conflict, timeout, cancellation, and missing-job failures.

## Packaged executable smoke test

The built `relay.pyz` was tested in a fresh temporary Relay home:

- `relay init`
- Claude deep doctor using mock CLI
- Codex deep doctor using mock CLI
- Codex synchronous work request
- JSON result validation and final delivery
- artifact delivery and manifest creation
- automatic cleanup state creation
- `relay cleanup --status`

All passed.

## Unix installer smoke test

`scripts/install_unix.sh` was run with isolated `HOME`, `INSTALL_DIR`, and `RELAY_HOME` directories. The installed launcher successfully ran:

- `relay version`
- `relay cleanup --status --machine`

## Platform coverage

| Platform | Status |
|---|---|
| Linux | Implemented and tested with mocks |
| Windows | Implementation retained; actual Job Object/provider CLI validation requires target Windows machine |
| macOS | POSIX implementation and installer added; actual provider CLI validation requires target Mac |

## Important limitation

Mock tests verify Relay orchestration and delivery behavior. The live checks above apply only to the listed local CLI versions and account state; every target machine must run `doctor --deep` for each provider version.
