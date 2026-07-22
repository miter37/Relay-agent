# Relay 0.5.0 test report

Test date: 2026-07-14

## Environment

- Linux container
- Python 3.13 runtime for tests
- No external Python dependencies
- Mock Claude, Codex, and Antigravity executables

## Automated tests

All 9 tests passed:

1. cleanup preserves recent workspaces
2. cleanup removes expired completed workspaces while preserving delivered results/artifacts
3. cleanup state and due scheduling
4. daemon performs due cleanup on startup
5. daemon submit/result/shutdown
6. deep doctor and Antigravity opt-in policy
7. exact request-id deduplication
8. Claude-to-Codex technical fallback
9. synchronous JSON delivery and artifact manifest

Command:

```sh
PYTHONPATH=. python -m unittest tests.test_relay -v
```

Result:

```text
Ran 9 tests
OK
```

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

Mock tests verify Relay orchestration and delivery behavior, not the actual current behavior of installed Claude Code, Codex CLI, or Antigravity CLI. Every target machine must run `doctor --deep` for each provider version.
