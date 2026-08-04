# Relay Agent Mission Hardening Implementation Plan v1.0

- **Document status:** Implementation-ready plan
- **Version:** 1.0
- **Date:** 2026-08-04
- **Product:** Relay-agent
- **Evidence:** `Relay_Agent_Skill_Mission_Validation_Report_v1.0.md`
- **Related plan:** `Relay_Agent_Catalog_Implementation_Plan_v1.0.md`
- **Primary Python for verification:** `D:\Python314\python.exe`

---

## 0. Executive summary

5개의 단독 Task, 2개의 Artifact 연속 실행, 3개의 Project 구조를 사용한 Agent 미션 검증에서 Relay의 핵심 모델은 정상 동작했다.

- Task Catalog pagination과 detail 조회
- 성공·실패 Task Run summary
- Artifact UID 입력과 immutable snapshot
- Lineage와 SHA-256 검증
- 순차·병렬·외부 입력 Project Run
- 변조 Artifact의 `ARTIFACT_CHANGED` 차단

그러나 Agent가 실제 CLI만 사용해 자율적으로 미션을 수행하려면 세 가지가 더 필요하다.

1. **Machine contract 명확화:** Artifact 본문 필드, 목록 item key, status 표기처럼 Agent가 추측하기 쉬운 계약을 self-describing하게 고정한다.
2. **실제 CLI E2E:** DB 상태를 직접 조정하는 가상 완료가 아니라 bundled mock Worker로 `submit → wait → result → Artifact reuse → Lineage`를 수행한다.
3. **Project discovery:** Task와 Task Run뿐 아니라 Project와 Project Run도 bounded Catalog로 탐색할 수 있게 한다.

이번 계획은 Relay 안에 검색·추천·랭킹을 추가하지 않는다. Agent가 Catalog를 읽고 후보를 선택한다는 책임 경계는 유지한다.

---

## 1. Evidence and findings

## 1.1 확인된 강점

| 검증 항목 | 결과 | 근거 |
|---|---|---|
| 등록 Task 탐색 | 통과 | Catalog page와 detail 조회 |
| summary 기반 후보 축소 | 통과 | `task_summary`, `result_summary`, `failure_reason` |
| 단독 Artifact 연속 실행 | 통과 | 2개 consumer Lineage의 `binding_mode=snapshot` |
| Project Artifact 연결 | 통과 | 순차 Project의 role→alias 연결 |
| 병렬 Project | 통과 | 독립 Node dispatch와 final Artifact 선택 |
| 외부 Artifact Project 입력 | 통과 | Project Run 생성 시 UID snapshot |
| 실패 Run 회피 정보 | 통과 | non-empty `failure_reason` |
| 변조 차단 | 통과 | `ARTIFACT_CHANGED` |

## 1.2 발견된 계약 마찰

### A. Artifact read payload

검증기는 처음에 본문 필드를 `content`로 추측했다. 실제 계약은 `text`다.

```json
{
  "ok": true,
  "artifact_uid": "art-...",
  "available": true,
  "text": "...",
  "size": 123,
  "truncated": false,
  "mime_type": "text/markdown"
}
```

애플리케이션 결함은 아니지만, Agent가 API 구현을 읽지 않고도 canonical field를 알 수 있어야 한다.

### B. 목록 envelope 차이

- Catalog: `items`
- Project list: `projects`
- Project Run list: `project_runs`

전용 alias는 유용하지만 Agent용 machine parsing에는 공통 `items`가 필요하다.

### C. status 표기 추측

Project Run의 canonical public status는 소문자 `completed`다. 내부 저장소와 일부 legacy payload에는 대문자 상태가 남아 있다. Agent가 어느 표기를 기대해야 하는지 machine contract에서 명확히 해야 한다.

## 1.3 발견된 검증 공백

기존 미션은 Relay Engine·DB·Daemon·CLI의 읽기 경로를 사용했지만 Worker 완료는 통제된 상태 변경으로 만들었다. 따라서 다음은 아직 하나의 실제 흐름으로 검증되지 않았다.

```text
relay submit
→ daemon queue
→ audited Worker adapter
→ result.json 생성
→ validation/delivery
→ receipt schema v2
→ relay wait/result
→ Artifact UID 재사용
```

저장소의 `mocks/`와 deep Doctor 경로가 이미 있으므로 새로운 fake execution framework를 만들 필요는 없다.

## 1.4 발견된 제품 공백

Agent는 Task를 Catalog에서 찾을 수 있지만 Project를 선택할 bounded Catalog가 없다.

현재 가능한 것은 `relay project list`와 개별 Project 상세 조회다. Project 수가 늘어나면 전체 `definition_json`을 읽기 전에 다음 정보가 필요하다.

- Project의 짧은 summary
- Version
- Node와 connection 수
- 최종 output role
- 최근 Project Run 상태와 실패 이유
- Project/Project Run detail 경로

---

## 2. Product decisions

## 2.1 유지할 책임 경계

Relay:

- bounded Catalog와 stable ID 제공
- summary·Version·status·Artifact metadata 보존
- detail path와 machine contract 제공
- privacy, retention, migration, integrity 집행

Agent:

- 요청의 목적·입력·출력·제약 추출
- Catalog page 순회
- 후보 Project/Task 선정
- detail 비교와 최종 선택
- no-match 시 새 정의 제안

Relay는 `query`, `score`, `similarity`, `recommended`를 새 Catalog에 추가하지 않는다.

## 2.2 Machine contract 원칙

새 read-only 목록은 공통 envelope를 사용한다.

```json
{
  "ok": true,
  "kind": "projects",
  "items": [],
  "next_cursor": null,
  "has_more": false
}
```

기존 전용 key는 additive alias로 유지한다.

```json
{
  "items": [],
  "projects": []
}
```

Canonical public status는 소문자다.

```text
created, queued, running, completed, partial, failed, cancelled
```

기존 API·DB compatibility field의 대문자 값은 변경하지 않는다. 새 Catalog serializer와 machine contract만 canonical lowercase를 보장한다.

## 2.3 Catalog versioning 결정

기존 `catalog_schema_version=1`은 유지한다. Project와 Project Run은 새 kind로 additive하게 추가하고 각 kind에 `item_schema_version=1`을 둔다. 기존 Task/Task Run consumer가 root version 변경으로 중단되지 않게 한다.

---

## 3. Target Agent behavior

```text
Mission 수신
→ GET /v1/catalog 또는 relay catalog --machine
→ Task 또는 Project kind 선택
→ bounded page에서 summary로 후보 선정
→ 후보 detail 조회
→ Task 또는 Project 실행
→ wait/result/receipt 검증
→ 과거 Task Run 또는 Project Run Catalog 확인
→ 필요한 Artifact만 UID로 조회·재사용
→ Lineage 검증
```

No-match:

```text
Catalog에 목적·계약이 모두 맞는 후보 없음
→ 가까운 후보를 강제 실행하지 않음
→ 새 Task 또는 Project 정의 제안
```

---

## 4. Machine contract hardening

## 4.1 Catalog capability manifest 확장

`GET /v1/catalog` 응답에 다음 additive metadata를 추가한다.

```json
{
  "ok": true,
  "catalog_schema_version": 1,
  "response_contract": {
    "list_items_key": "items",
    "status_style": "lowercase",
    "cursor_style": "opaque_urlsafe"
  },
  "resources": {
    "artifact_content": {
      "path_template": "/v1/artifacts/{artifact_uid}/content",
      "text_field": "text",
      "availability_field": "available"
    }
  },
  "kinds": {}
}
```

`content` alias는 추가하지 않는다. 동일 본문을 두 key에 복제하면 어느 key가 canonical인지 다시 불명확해진다.

## 4.2 Additive list aliases

다음 기존 응답에 `kind`, `items`, `has_more`를 additive하게 추가한다.

- `GET /v1/projects`
- `GET /v1/projects/{project_id}/runs`

기존 `projects`, `project_runs`는 유지한다. 기존 endpoint에는 cursor를 억지로 추가하지 않고, pagination은 Catalog endpoint가 담당한다.

## 4.3 Contract reference

새 문서 `docs/Relay_Machine_Read_Contract_v1.0.md`를 구현 시 작성한다.

반드시 포함할 표:

- command/path
- canonical item key
- compatibility aliases
- status casing
- text/content field
- ID field
- pagination 방식
- detail follow-up path

## 4.4 Regression tests

- Artifact read가 `text`를 반환하고 `content`를 반환하지 않음
- Project list의 `items is projects` 동등성
- Project Run list의 `items is project_runs` 동등성
- Catalog status가 항상 lowercase
- capability manifest의 field name과 실제 serializer 일치

---

## 5. Project summary data model

## 5.1 Schema v14

Project 후보를 전체 DAG 없이 판단할 수 있도록 dedicated summary를 추가한다.

```sql
ALTER TABLE projects ADD COLUMN project_summary TEXT;

CREATE INDEX IF NOT EXISTS idx_projects_catalog
ON projects(updated_at DESC, project_id DESC);

CREATE INDEX IF NOT EXISTS idx_project_runs_catalog
ON project_runs(created_at DESC, project_run_id DESC);
```

## 5.2 Project summary contract

- optional string
- whitespace collapse
- blank → `null`
- maximum 500 Unicode characters
- 초과 시 기존 summary normalization과 동일한 ellipsis 정책

Project create/update 입력:

```json
{
  "project_summary": "여러 출처를 조사하고 검증한 뒤 최종 보고서를 만든다."
}
```

## 5.3 Resolution and backfill

새 write 우선순위:

1. explicit `project_summary`
2. bounded `description`
3. `null`

기존 Project backfill:

1. existing description
2. Project name과 Node의 Task name을 사용한 deterministic bounded summary
3. 정보가 부족하면 `null`

Backfill은 다음을 지킨다.

- 기존 non-null summary를 덮어쓰지 않는다.
- Project definition을 변경하지 않는다.
- migration transaction에서 Result/Artifact 파일을 읽지 않는다.
- 재실행 가능하고 결정적이다.

## 5.4 Immutable Project Run summary

Project Run 생성 시 `project_summary`를 `project_snapshot_json`에 저장한다. Project가 이후 수정돼도 과거 Project Run Catalog summary는 바뀌지 않는다.

별도 `project_runs.project_summary` column은 이번 단계에서 추가하지 않는다. Project Run row가 이미 immutable snapshot을 보유하므로 Catalog serializer가 그 bounded field를 읽는다.

---

## 6. Project Catalog API

## 6.1 Capability kinds

```json
{
  "projects": {
    "item_schema_version": 1,
    "list_path": "/v1/catalog/projects",
    "detail_path_template": "/v1/projects/{project_id}",
    "order": "updated_at_desc"
  },
  "project_runs": {
    "item_schema_version": 1,
    "list_path": "/v1/catalog/project-runs",
    "detail_path_template": "/v1/project-runs/{project_run_id}",
    "order": "created_at_desc"
  }
}
```

## 6.2 Project Catalog

```http
GET /v1/catalog/projects?limit=100&cursor=<opaque>&updated_since=<ISO8601>
```

Item:

```json
{
  "project_id": "01K...",
  "name": "Research and validate",
  "version": 3,
  "project_summary": "자료를 조사하고 독립 검증 후 최종 보고서를 만든다.",
  "node_count": 4,
  "connection_count": 3,
  "output_roles": ["final_report", "sources"],
  "has_checkpoints": true,
  "created_at": "...",
  "updated_at": "..."
}
```

목록에 포함하지 않는 것:

- 전체 `definition_json`
- 전체 Task snapshot
- delivery path
- notification secret
- raw instructions

정렬:

```text
(updated_at DESC, project_id DESC)
```

## 6.3 Project Run Catalog

```http
GET /v1/catalog/project-runs?limit=100&cursor=<opaque>&status=completed&project_id=<ID>&from=<ISO>&to=<ISO>
```

Item:

```json
{
  "project_run_id": "01K...",
  "project_id": "01K...",
  "project_version": 3,
  "project_summary": "자료를 조사하고 독립 검증 후 최종 보고서를 만든다.",
  "status": "completed",
  "step_count": 4,
  "completed_step_count": 4,
  "failed_step_count": 0,
  "final_artifact_count": 2,
  "final_artifact_roles": ["final_report", "sources"],
  "failure_reason": null,
  "trigger_type": "manual",
  "created_at": "...",
  "completed_at": "..."
}
```

실패 reason 우선순위:

1. Project Run receipt의 failure reason
2. first failed Project step의 normalized error message
3. warnings의 first normalized error
4. `null` only when status is not failed

정렬:

```text
(created_at DESC, project_run_id DESC)
```

## 6.4 Query and performance

- `limit`: 1~200, default 100
- invalid cursor: `INVALID_CURSOR`
- exact status/Project/date filters only
- Project Run step counts는 aggregate query 또는 bounded batch query
- Project별 step N+1 query 금지
- cursor에 sort key와 last ID 저장

---

## 7. CLI contract

추가 command:

```text
relay catalog projects
relay catalog project-runs
```

예:

```sh
relay catalog projects --limit 100 --machine
relay catalog projects --cursor "<CURSOR>" --machine
relay catalog project-runs --status failed --machine
relay catalog project-runs --project-id "<PROJECT_ID>" --machine
```

상세 조회는 기존 command를 재사용한다.

```sh
relay project show <PROJECT_ID> --machine
relay project-run show <PROJECT_RUN_ID> --machine
relay project-run steps <PROJECT_RUN_ID> --machine
relay project-run receipt <PROJECT_RUN_ID> --machine
```

사람용 출력은 ID, name, Version, status, summary와 node/step 수만 표시한다. 전체 DAG나 snapshot을 기본 출력하지 않는다.

---

## 8. Real CLI mission E2E

## 8.1 목적

기존 가상 미션의 DB 상태 직접 조정을 제거하고 실제 adapter와 daemon lifecycle을 검증한다.

## 8.2 기존 test infrastructure 재사용

사용할 자산:

- `mocks/claude` / `mocks/claude.cmd`
- `mocks/codex` / `mocks/codex.cmd`
- `mocks/agy` / `mocks/agy.cmd`
- `RELAY_TEST_PYTHON=D:\Python314\python.exe`
- `Doctor(...).audit(..., deep=True)`
- 임시 Relay Home과 random daemon port

새 mock framework는 만들지 않는다.

## 8.3 Checked-in scenario

신규 파일:

```text
tests/test_agent_mission_e2e.py
```

시나리오:

1. deep audit가 통과한 mock Codex 구성
2. 등록 Task 5개 생성
3. 실제 CLI로 단독 Task 5개 실행
4. Task 2가 Task 1 Artifact를 `A1`로 사용
5. Task 4가 Task 3 Artifact를 `A1`로 사용
6. 순차 Project 등록·실행
7. 병렬 Project 등록·실행
8. 외부 Artifact 입력 Project 등록·실행
9. 각 Project Run terminal 상태 대기
10. receipt schema v2 summary 확인
11. Catalog에서 Task/Project와 각각의 Run 발견
12. Artifact read와 Lineage 확인
13. 실패 mock mode로 non-empty failure reason 확인
14. Artifact 변조 후 `ARTIFACT_CHANGED` 확인

금지:

- `db.update_job(... status=...)`로 완료 위조
- private `_fail_job`로 실패 위조
- provider 실행 파일 직접 호출
- fixed daemon port
- 실제 사용자 Relay Home 사용

## 8.4 CLI process boundary

최소 한 시나리오는 Python API direct call이 아니라 subprocess로 다음을 수행한다.

```text
relay task create
relay catalog tasks
relay task run
relay wait
relay result
relay project create
relay project run
relay project-run show
relay artifact read
relay run-lineage
```

Windows, Linux, macOS에서 동일 테스트를 실행한다. Windows wrapper는 반드시 현재 test interpreter인 `D:\Python314\python.exe`를 사용한다.

---

## 9. Agent skill update

`skills/hermes-relay/SKILL.md`에 다음을 추가한다.

## 9.1 Machine response table

```text
artifact read          → text
catalog list           → items
project list alias     → projects
project-run list alias → project_runs
catalog status         → lowercase
```

Agent는 compatibility alias보다 canonical field를 먼저 사용한다.

## 9.2 Project discovery

```text
relay catalog projects --machine
→ project_summary와 output_roles로 후보 3~5개 선정
→ relay project show로 DAG와 Task Version 확인
→ 목적·입력·출력 계약 비교
→ 적합한 Project 실행 또는 새 Project 제안
```

과거 Project Run:

```text
relay catalog project-runs --machine
→ status, project_summary, step counts, failure_reason 확인
→ 유망 Run만 receipt/steps/final Artifact 조회
```

## 9.3 Public terminology guard

새 skill·README·manual·CLI help·Catalog examples에는 `Project`, `Task`, `Task Run`, `Project Run`, `Attempt`, `Artifact`만 사용한다. 내부 compatibility 설명은 별도 allowlist section에만 둔다.

---

## 10. Implementation slices

## Slice 1 — Freeze machine read contracts

대상:

- `relay/api.py`
- `relay/daemon.py`
- API/CLI contract tests
- `docs/Relay_Machine_Read_Contract_v1.0.md`

작업:

- Catalog capability metadata
- Project/Project Run 기존 list의 additive `items`
- Artifact `text` contract 고정
- lowercase status contract 고정

검증:

- exact payload tests
- old alias compatibility tests
- no duplicate body field

## Slice 2 — Replace simulated completion with mock-Worker E2E

대상:

- `tests/test_agent_mission_e2e.py`
- 필요한 경우 기존 `mocks/`의 최소 확장

작업:

- 5 Task·2 chain·3 Project scenario
- actual submit/wait/result
- failure and tamper scenario

검증:

- no direct status mutation
- cross-platform mock wrappers
- receipt/Result/Artifact/Lineage all checked

## Slice 3 — Project summary and schema v14

대상:

- `relay/db.py`
- `relay/projects/models.py`
- `relay/projects/service.py`
- migration tests

작업:

- `project_summary`
- normalize/create/update/backfill
- Project Run snapshot pinning
- Catalog indexes

검증:

- v13→v14 migration
- old Project preserved
- edited Project does not change old Project Run summary
- blank/long summary normalization

## Slice 4 — Project and Project Run Catalog

대상:

- `relay/db.py`
- `relay/api.py`
- `relay/daemon.py`
- `relay/cli.py`
- catalog tests

작업:

- capability kinds
- Project pagination
- Project Run pagination and filters
- aggregate step/final Artifact metadata
- CLI commands

검증:

- identical timestamp cursor boundaries
- invalid cursor
- status/Project/date filters
- no DAG/snapshot/content leakage
- no N+1 query

## Slice 5 — Skill and operating documentation

대상:

- `skills/hermes-relay/SKILL.md`
- `README.md`
- `manual.md`
- machine contract document

작업:

- canonical response table
- Project candidate selection
- Project Run reuse/failure workflow
- no-match behavior
- terminology guard

검증:

- documented commands parse
- examples match exact machine payloads
- public terminology scan

## Slice 6 — Release acceptance

작업:

- focused tests
- full unittest
- Ruff
- compileall
- release build
- built `relay.pyz` smoke commands

Windows 기준:

```powershell
D:\Python314\python.exe -m ruff check .
D:\Python314\python.exe -m ruff format --check .
D:\Python314\python.exe -m unittest discover -s tests
D:\Python314\python.exe -m compileall -q relay
D:\Python314\python.exe build_release.py
D:\Python314\python.exe relay.pyz catalog --machine
git diff --check
```

Generated `relay.pyz`, `SHA256SUMS.txt`, temporary Relay Home, Result, Artifact는 commit하지 않는다.

---

## 11. Test matrix

| Layer | Required coverage |
|---|---|
| Validation | Project summary type, whitespace, length |
| Migration | v13→v14, fresh schema, legacy fixtures |
| DB | ordering, cursor boundary, step aggregate |
| API | capability, Projects, Project Runs, Artifact text |
| Daemon | query parsing, invalid cursor, filters |
| CLI | all new commands and exact query encoding |
| E2E | 5 Tasks, 2 chains, 3 Projects, actual mock Worker |
| Privacy | no instructions, full DAG, Result body, secret leakage |
| Compatibility | legacy list aliases and routes unchanged |
| Cross-platform | Windows wrapper and POSIX mock execution |

---

## 12. Privacy and security

- Project Catalog에는 전체 DAG와 Task instructions를 넣지 않는다.
- Project Run Catalog에는 snapshot, delivery path, notification configuration을 넣지 않는다.
- Artifact content는 명시적 `artifact read`에서만 반환한다.
- non-replayable Task Run summary scrub 정책은 변경하지 않는다.
- Project summary는 등록 Project의 명시적 durable metadata로 취급한다.
- E2E는 임시 Relay Home과 bundled mocks만 사용한다.
- test에서 실제 provider credential 또는 사용자 환경을 상속하지 않는다.

---

## 13. Compatibility strategy

- DB migration은 additive schema v14다.
- 기존 Project와 Project Run ID를 변경하지 않는다.
- 기존 `/v1/projects`, `/v1/projects/{id}/runs` payload key를 제거하지 않는다.
- 기존 Task/Task Run Catalog schema version을 바꾸지 않는다.
- 기존 GUI는 새 Catalog kind를 몰라도 정상 동작한다.
- public status를 일괄 rewrite하지 않는다. 새 Catalog serializer만 lowercase를 보장한다.
- 내부 legacy identifier와 route는 호환 경계에서 유지한다.

---

## 14. Acceptance criteria

다음 조건을 모두 만족해야 완료다.

1. Agent가 capability manifest만 보고 canonical `items`, `text`, lowercase status를 알 수 있다.
2. 기존 Project/Project Run list consumer가 계속 동작한다.
3. Agent가 Project Catalog를 cursor로 순회할 수 있다.
4. Project item만 보고 유망 후보를 선정할 수 있다.
5. Agent가 Project Run Catalog에서 성공·실패와 step 진행 결과를 비교할 수 있다.
6. Project Catalog에 전체 DAG 또는 Task instructions가 노출되지 않는다.
7. Project Run Catalog에 full snapshot 또는 Result content가 노출되지 않는다.
8. 5개 단독 Task가 실제 mock Worker를 통해 terminal 상태에 도달한다.
9. 두 Task가 이전 Artifact UID를 입력으로 사용하고 Lineage가 검증된다.
10. 순차·병렬·외부 입력 Project Run 3개가 실제 runtime으로 완료된다.
11. failure reason과 Artifact tamper rejection이 실제 execution path에서 검증된다.
12. 테스트가 DB status 직접 조정이나 private failure helper에 의존하지 않는다.
13. v13 DB가 v14로 안전하게 migration된다.
14. 기존 471개 이상 전체 테스트와 새 테스트가 통과한다.
15. Ruff, format, compileall, release build, `relay.pyz` smoke가 통과한다.
16. 새 공개 문서·help·Catalog에 비표준 실행 용어를 도입하지 않는다.

---

## 15. Commit strategy

1. `test: freeze machine read response contracts`
2. `test: run agent missions through bundled mock workers`
3. `feat: persist project summaries with schema v14`
4. `feat: expose project and project-run catalogs`
5. `docs: teach agents project catalog discovery`
6. `test: verify release artifact mission smoke`

각 commit은 독립적으로 focused test가 통과해야 한다.

---

## 16. Deferred follow-ups

이번 미션에서 직접 필요성이 입증되지 않았으므로 다음은 구현하지 않는다.

- Task Version history registry와 diff
- Project Version history registry
- recursive Lineage graph
- GUI Catalog browser
- production embedding backend
- Relay-side semantic ranking 또는 recommendation
- Agent가 Project design을 자동 생성하는 기능
- 실제 Claude/Codex/Antigravity 계정에 의존하는 CI

Project Catalog와 mock-Worker E2E가 안정화된 뒤 실제 사용자 미션에서 다시 우선순위를 평가한다.

---

## 17. Recommended execution order

```text
Machine contract freeze
→ mock-Worker mission E2E
→ schema v14 Project summary
→ Project/Project Run Catalog
→ skill/manual update
→ release artifact acceptance
```

첫 두 Slice를 먼저 수행하면 새 기능을 추가하기 전에 현재 Agent workflow의 신뢰성을 고정할 수 있다. 그 위에 Project Catalog를 추가하면 Task Catalog에서 검증한 책임 경계와 pagination 방식을 그대로 재사용할 수 있다.
