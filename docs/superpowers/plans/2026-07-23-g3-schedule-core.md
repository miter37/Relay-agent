# G3 Schedule Core Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Relay 0.9.0에 deterministic timezone-aware Schedule, 안전한 input snapshot, atomic occurrence claim, CLI/API 제어, unique output, Schedule 전용 retention을 추가한다.

**Architecture:** 기존 queued Job executor는 유지한다. `relay/schedules/` 도메인 패키지를 추가해 rule 계산, snapshot, lifecycle/API, runtime, retention을 분리하고, 예약 실행은 일반 queued Job을 내부 engine context로 생성한다.

**Tech Stack:** Python 3.11+, SQLite, `zoneinfo`, standard library, `unittest`, 기존 loopback daemon/RPC, Ruff.

## Global Constraints

- G3는 CLI + daemon/API core만 포함하며 Schedule GUI는 G4로 미룬다.
- package version은 `0.9.0`으로 올리고 기존 G2 GUI 호환성을 유지한다.
- Schedule Job은 `caller="schedule"`, `submitted_via="schedule"`, `force_new=True`, managed workspace, unique output path를 사용한다.
- 외부 scheduler dependency를 추가하지 않는다.
- 모든 기능은 failing test → RED 확인 → 최소 구현 → GREEN 검증 순서로 진행한다.
- `relay-receipt.json`, `test_result.json`, `test_task.md`는 staging하지 않는다.

---

### Task 0: G2 기준선 고정

**Files:** G2 변경 파일 전체(`.github/workflows/ci.yml`, `RELEASE_NOTES.md`, `relay/`, `tests/test_g0_api.py`, `tests/test_g1_gui.py`, G2 신규 파일).

**Interfaces:** 현재 G2 working tree를 입력으로 받아 G3 시작 가능한 커밋을 만든다.

- [ ] `python -m unittest discover -s tests -v`, Ruff, compileall을 실행해 G2 기준선을 확인한다.
- [ ] G2 파일만 명시적으로 `git add`한다. 사용자 미추적 파일은 staging하지 않는다.
- [ ] `git commit -m "feat: implement G2 job control GUI"`로 체크포인트를 만든다.

Expected: G2 전체 테스트 통과, G2 commit 생성, 사용자 파일 3개는 계속 untracked.

### Task 1: SQLite schema v2와 Schedule DB primitives

**Files:** Modify `relay/db.py`, `tests/test_migrations.py`; create `tests/test_g3_db.py`.

**Produces:** `schedules`, `schedule_runs` 테이블과 `create_schedule`, `get_schedule`, `list_schedules`, `update_schedule`, `insert_schedule_run`, `claim_schedule_occurrence`, `list_schedule_runs`, `active_jobs_for_schedule`, `link_schedule_run_job`.

- [ ] 먼저 v1→v2 migration, idempotent reopen, row preservation, `(schedule_id, occurrence_key)` unique claim 실패 테스트를 작성한다.
- [ ] `python -m unittest tests.test_migrations tests.test_g3_db -v`를 실행해 RED를 확인한다.
- [ ] `CURRENT_SCHEMA_VERSION = 2`, transactional `MIGRATION_1_TO_2`, 두 테이블 DDL/index/foreign key를 구현한다.
- [ ] claim/link SQL은 짧은 transaction과 parameterized query를 사용한다.
- [ ] 같은 focused test를 GREEN으로 실행한다.
- [ ] `git commit -m "feat: add schedule schema and database primitives"`로 커밋한다.

### Task 2: deterministic timezone-aware rule calculator

**Files:** Create `relay/schedules/__init__.py`, `relay/schedules/rules.py`, `tests/test_g3_rules.py`.

**Produces:** `Occurrence`, `validate_rule`, `next_occurrences(rule, after_utc, limit=5)`.

- [ ] Daily/Weekly/Monthly/N-days/Once, multiple times, month-day skip/last-day, invalid rule 테스트를 먼저 작성한다.
- [ ] DST nonexistent time skip, ambiguous time fold=0, stable occurrence key, start/end boundary 테스트를 추가한다.
- [ ] `python -m unittest tests.test_g3_rules -v`로 RED를 확인한다.
- [ ] Python `zoneinfo`와 UTC round-trip으로 local time 유효성을 판단하고, 후보 instant를 정렬·deduplicate한다.
- [ ] `RelayError("SCHEDULE_RULE_INVALID", ...)`로 field-level validation 오류를 반환한다.
- [ ] rule 및 기존 G2 regression test를 GREEN으로 실행하고 커밋한다: `feat: add timezone-aware schedule rules`.

### Task 3: immutable task/attachment snapshot

**Files:** Create `relay/schedules/snapshots.py`, `relay/schedules/service.py`, `tests/test_g3_snapshots.py`; modify `relay/errors.py` and config only if required.

**Produces:** `ScheduleSnapshot`, `validate_source_job`, `materialize_snapshot`, `build_scheduled_request`, `schedule_output_paths`.

- [ ] completed+complete+replayable eligibility와 failed/partial/non-replayable/missing request/attachment/agent rejection 테스트를 작성한다.
- [ ] filename normalization, duplicate name, size limit, symlink/path escape, failed copy cleanup, SHA-256 manifest 테스트를 작성하고 RED를 확인한다.
- [ ] temporary sibling directory에 `request.md`, copied attachments, `attachments.json`을 만든 후 atomic rename한다.
- [ ] source/destination을 configured Relay roots 아래로 resolve하고 symlink escape를 거부한다.
- [ ] cloned request에 `request_id=None`, `force_new=True`, `caller="schedule"`, managed workspace, snapshot task file, unique output paths를 강제한다.
- [ ] focused tests와 `ruff check relay/schedules relay/errors.py`를 실행하고 `feat: snapshot schedule inputs safely`로 커밋한다.

### Task 4: Schedule lifecycle service와 daemon API

**Files:** Modify `relay/schedules/service.py`, `relay/api.py`, `relay/daemon.py`, `relay/errors.py`; create `tests/test_g3_api.py`.

**Produces:** `create_from_job`, `preview`, `list`, `show`, `pause`, `resume`, `run_now`, `delete`와 `/v1/schedules` endpoint.

- [ ] eligible Job 생성, preview, list/show, pause/resume, run-now, delete, stable errors, public `/v1/jobs` schedule-link forge 거부 테스트를 작성한다.
- [ ] `python -m unittest tests.test_g3_api -v`로 RED를 확인한다.
- [ ] repeated `--time`에 대응하는 canonical rule JSON, `next_run_at_utc`, snapshot 생성, token-authenticated routing을 구현한다.
- [ ] missing resource는 404, invalid policy/rule는 400 stable error code를 반환한다.
- [ ] `tests.test_g3_api tests.test_g2_api tests.test_g0_api`를 실행하고 `feat: expose schedule lifecycle API`로 커밋한다.

### Task 5: scheduled Job context와 atomic runtime

**Files:** Modify `relay/engine.py`, `relay/daemon.py`, possibly `relay/db.py`; create `relay/schedules/runtime.py`, `tests/test_g3_runtime.py`.

**Produces:** `RelayEngine.queue_scheduled(...)`와 `ScheduleRuntime.tick(now_utc)`.

- [ ] due occurrence queue, schedule identity/link, unique output, concurrent duplicate suppression, overlap skip/queue, missed skip/catch-up, one-time deactivation, restart recalculation 테스트를 먼저 작성한다.
- [ ] `python -m unittest tests.test_g3_runtime -v`로 RED를 확인한다.
- [ ] 내부 Schedule context로 Job row에 `schedule_id`/`scheduled_for`를 Job runner 노출 전 기록하고 public payload로는 받을 수 없게 한다.
- [ ] occurrence unique insert를 claim gate로 사용한다. 파일 복사/worker 실행 중 write transaction을 잡지 않는다.
- [ ] `ScheduleRuntime`를 별도 loop로 daemon start/stop에 연결하고 기존 `RelayDaemon.scheduler` 호환성을 보존한다.
- [ ] runtime/API/G2/replay regression을 실행하고 `feat: run scheduled jobs with atomic claims`로 커밋한다.

### Task 6: CLI schedule commands

**Files:** Modify `relay/cli.py`; create `tests/test_g3_cli.py`; update command documentation if needed.

**Produces:** `relay schedule create|preview|list|show|runs|pause|resume|run-now|delete`와 `--machine` JSON.

- [ ] parser에 repeated `--time`, `--weekday`, `--month-day`, policy, retention, start/end, output-root 옵션 테스트를 먼저 작성한다.
- [ ] `python -m unittest tests.test_g3_cli -v`로 RED를 확인한다.
- [ ] `COMMANDS`와 schedule subparser를 추가하고 기존 RPC/daemon startup helper를 사용한다.
- [ ] human output과 stable machine JSON, invalid operation nonzero exit을 구현한다.
- [ ] CLI/API test를 GREEN으로 실행하고 `feat: add schedule CLI commands`로 커밋한다.

### Task 7: Schedule-specific output retention

**Files:** Create `relay/schedules/retention.py`, `tests/test_g3_retention.py`; modify `relay/cleanup.py`, `relay/daemon.py`.

**Produces:** `ScheduleRetentionManager.run(dry_run=False)`과 maintenance integration.

- [ ] days/latest_runs/forever, active protection, newest-success protection, user-root protection, symlink escape, retryable deletion failure 테스트를 작성한다.
- [ ] `python -m unittest tests.test_g3_retention -v`로 RED를 확인한다.
- [ ] run manifest와 root ownership을 검증하고 Relay가 만든 run directory만 삭제한다. root 자체와 DB history는 보존한다.
- [ ] 기존 ordinary Job cleanup과 독립적으로 maintenance loop에서 실행한다.
- [ ] `python -m unittest tests.test_g3_retention tests.test_cleanup -v`를 GREEN으로 실행하고 `feat: add schedule output retention`으로 커밋한다.

### Task 8: release metadata, integration, final verification

**Files:** Modify `relay/__init__.py`, `RELEASE_NOTES.md`, `relay/daemon.py`, `tests/test_g0_api.py`; create `tests/test_g3_integration.py`.

- [ ] version 0.9.0, CLI-visible Schedule Job history, daemon restart, one complete scheduled Job flow 테스트를 먼저 작성하고 RED를 확인한다.
- [ ] release notes와 G2 compatibility floor를 갱신한다.
- [ ] 전체 검증을 실행한다:

`python -m unittest discover -s tests -v`  
`ruff format --check relay tests`  
`ruff check relay tests`  
`git diff --check`  
`python -m compileall -q relay tests`  
`python build_release.py`

- [ ] `git status --short --branch`와 `git ls-files --others --exclude-standard`로 scope와 사용자 파일 보존을 확인한다.
- [ ] `git commit -m "release: add Relay 0.9.0 schedule core"`로 release slice를 커밋한다.

## Stop Gate

- 모든 5개 rule type의 deterministic next-run test 통과.
- concurrent claim이 하나의 run만 생성.
- overlap/missed policy가 run history에 기록.
- replayable completed Job을 CLI에서 Schedule로 등록.
- scheduled Job이 일반 CLI history에 Schedule identity로 표시.
- task/attachments가 immutable snapshot으로 복사.
- 반복 실행 output이 서로 다른 directory에 저장.
- Schedule retention이 ordinary Job cleanup과 독립.
- daemon restart에서 due occurrence 중복/유실이 문서 정책대로 처리.
- 전체 test, Ruff, compileall, release build 통과.
