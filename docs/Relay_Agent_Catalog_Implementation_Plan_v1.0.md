# Relay Agent Catalog Implementation Plan v1.0

- **Document status:** Detailed implementation plan
- **Version:** 1.0
- **Date:** 2026-08-04
- **Product:** Relay-agent
- **Related identity:** `Relay_Product_Identity_v1.0.md`
- **Scope:** Registered Task catalog, Task Run receipt summaries, read-only machine catalog, Agent usage guidance, and public terminology cleanup
- **Primary Python for verification:** `D:\Python314\python.exe`

---

## 0. Executive summary

Relay는 이번 작업에서 새로운 검색 엔진을 만들지 않는다. 현재 제공하는 Task Run/Artifact 검색은 제거하거나 대체하지 않는다.

Relay의 책임은 Agent가 기존 Task와 과거 Task Run을 스스로 탐색할 수 있도록 다음 데이터를 안정적으로 관리하고 제공하는 것이다.

- 등록된 Task의 짧고 일관된 설명
- Task ID와 Version
- Task의 입력·출력 계약 존재 여부와 상세 조회 경로
- Task Run이 수행하려 했던 내용의 요약
- 성공한 Task Run의 결과 요약
- 실패한 Task Run의 정규화된 실패 원인
- Task Run, Result, Artifact, Lineage로 이동할 수 있는 안정적인 ID
- 대량 목록을 안전하게 순회할 수 있는 pagination

후보 검색, 유사 표현 생성, 후보 비교, 최종 Task 선택은 Relay를 사용하는 Agent의 책임이다.

```text
Relay
  → 정규화된 catalog 제공
  → 선택된 ID의 상세 정의와 receipt 제공

Agent
  → 요청에서 목적·입력·출력·제약 추출
  → catalog를 읽고 유망 후보 선정
  → 후보의 전체 Task 정의 조회
  → 가장 적합한 Task 선택 또는 새 Task 제안
```

이번 구현은 FTS5 Task 검색, Embedding, relevance score, 추천, 자동 선택, Project 검색, 통합 검색 GUI를 추가하지 않는다. 기존 Task Run/Artifact 검색은 그대로 둔다.

### 0.1 Canonical terminology

제품의 정식 업무 모델은 다음과 같다.

```text
Project       = 여러 Task를 연결한 업무 흐름 정의
Task          = 재사용 가능한 단일 업무 정의
Task Run      = Task가 한 번 수행된 논리적 실행 기록
Project Run   = Project가 한 번 수행된 논리적 실행 기록
Attempt       = 하나의 Task Run 안에서 Worker가 수행한 개별 시도
Artifact      = 실행에서 생성되거나 확정된 결과물
```

`Job`은 제품 객체나 사용자 화면의 용어가 아니다. 현재 DB의 `jobs` table, `job_id`, `/v1/jobs` API, 기존 CLI 인자와 `JOB_*` 오류 코드는 데이터·API 호환을 위해 내부/레거시 경계에서만 보존한다. 새 GUI, 새 문서, 새 catalog 계약, 새 도움말에서는 `Task Run`, `Project Run`, `Attempt`를 사용한다.

단독 `Run`이라는 표현도 새 계약에서는 사용하지 않는다. 문맥에 맞춰 `Task Run` 또는 `Project Run`으로 쓴다. 기존 `relay search --kind runs|artifacts`와 기존 API 경로는 호환 기능으로 유지한다.

---

## 1. Problem statement

현재 Relay에는 다음 기반이 이미 존재한다.

- 등록 Task CRUD와 실행
- Task Version 숫자와 Run별 Task snapshot
- Task Run receipt
- Task Run·Artifact FTS5 검색
- Artifact UID, content, lineage
- `--machine` JSON 출력
- Artifact를 새 Task 또는 Project 입력으로 재사용하는 기능

그러나 Agent가 기존 Task와 과거 Run을 체계적으로 탐색하기에는 데이터 계약이 불완전하다.

### 1.1 Registered Task discovery gap

현재 Task 목록은 이름과 전체 정의를 제공할 수 있지만 후보 탐색에 적합한 짧은 catalog 계약이 없다.

- Task를 한 문장으로 설명하는 전용 필드가 없다.
- 전체 instructions를 읽기 전 후보를 좁힐 안정적인 요약이 없다.
- 목록이 커질 때 순회할 opaque cursor 계약이 없다.
- Agent가 따라야 할 후보 선정 절차가 스킬 문서에 없다.

### 1.2 Task Run discovery gap

현재 성공 receipt에는 Worker, 결과 경로, Artifact 경로, Task snapshot, 상태와 검증 정보가 들어간다.

현재 실패 receipt에는 오류 코드, 오류 메시지, Attempt와 로그 경로가 들어간다.

다음 항목은 보장되지 않는다.

- `task_summary`
- `result_summary`
- `failure_reason`

Task Run FTS 인덱스는 결과 파일에서 `answer` 또는 `summary`를 읽어 검색용 요약을 만들 수 있다. 그러나 그 요약은 receipt와 일반 DB row의 정규 필드가 아니다. 결과 파일이 삭제되면 인덱스 재구축 시 같은 요약을 보장할 수 없다.

### 1.3 Product decision

Relay는 후보를 랭킹하거나 가장 적합한 Task를 결정하지 않는다.

Relay는 다음 두 단계 읽기 계약을 제공한다.

```text
Catalog list
→ 짧은 요약, ID, Version, 상태

Detail get
→ 전체 Task 정의, 전체 receipt, Result, Artifact, Lineage
```

Agent는 catalog에서 후보를 선정하고 detail을 읽어 최종 판단한다.

---

## 2. Goals

### 2.1 Primary goals

1. Agent가 등록된 Task 목록을 bounded JSON으로 순회할 수 있다.
2. Agent가 전체 instructions를 읽기 전에 요약으로 후보를 좁힐 수 있다.
3. Agent가 유망 Task의 전체 정의를 조회하고 입력·출력·검증 계약을 비교할 수 있다.
4. 새 Task Run receipt는 성공·실패와 관계없이 동일한 summary key를 가진다.
5. 결과 파일이 삭제돼도 DB에 저장된 Task Run 요약은 남는다.
6. Agent가 과거 Task Run 목록에서 유망 Task Run을 선정하고 Result·Artifact·Lineage를 추가 조회할 수 있다.
7. 기존 history privacy와 non-replayable scrub 정책을 약화하지 않는다.
8. 기존 CLI/API/GUI 소비자는 additive 변경으로 계속 동작한다.

### 2.2 Success statement

> Relay는 무엇이 존재하는지, 각 항목이 무엇을 의미하는지, 어떤 ID로 상세 자료를 읽을 수 있는지를 제공한다. Agent는 그 자료를 사용해 후보를 찾고 비교하고 선택한다.

---

## 3. Non-goals

이번 작업에서는 다음을 구현하지 않는다.

- Task FTS 검색 endpoint
- Task semantic search
- Embedding 생성 또는 production embedding backend
- relevance score 또는 자동 랭킹
- Relay의 추천 Task
- Relay의 자동 Task 선택
- Project·Project Run catalog
- Project Design 생성
- Task Version 이력 레지스트리
- Mission 또는 Orchestrator 도메인 객체
- GUI 통합 검색 화면
- SQLite DB에 대한 Agent 직접 접근
- 기존 Task Run·Artifact 검색 기능 제거 또는 재작성

기존 `relay search --kind runs|artifacts`는 호환 기능으로 유지한다. 이 계획의 catalog는 검색 결과가 아니라 안정적인 Task/Task Run 원장 목록이다.

---

## 4. Responsibility boundary

| Relay responsibility | Agent responsibility |
|---|---|
| Task와 Task Run summary를 정규화해 저장 | 요청의 목적·입력·출력·제약 추출 |
| 안정적인 ID, Version, 상태 제공 | 유사 표현과 후보 판단 기준 생성 |
| bounded catalog와 pagination 제공 | catalog page를 읽고 후보 선정 |
| 선택한 ID의 전체 정의 제공 | 후보 instructions와 계약 비교 |
| receipt, Result, Artifact, Lineage 제공 | 과거 결과의 재사용 가치 판단 |
| 민감 정보와 retention 정책 집행 | 선택 이유와 불확실성 설명 |
| receipt 실패 원인 보증 | 적합한 Task가 없으면 새 Task 제안 |

Relay가 제공하는 catalog에는 `query`, `score`, `similarity`, `recommended` 필드를 두지 않는다.

---

## 5. Data contract

## 5.1 Registered Task catalog item

```json
{
  "task_id": "01K...",
  "name": "제품 위험 뉴스 조사",
  "version": 3,
  "task_summary": "제품 관련 부정적 뉴스와 위험 신호를 근거와 함께 수집한다.",
  "has_input_schema": true,
  "has_output_contract": true,
  "has_validation_policy": true,
  "default_worker": "auto",
  "profile": "web-research",
  "result_format": "json",
  "created_at": "2026-08-01T09:00:00+09:00",
  "updated_at": "2026-08-04T13:00:00+09:00"
}
```

Catalog item은 전체 instructions, 전체 schema, 전체 contract를 포함하지 않는다.

Agent는 유망 후보에 대해 기존 상세 계약을 사용한다.

```text
relay task show <TASK_ID> --machine
GET /v1/tasks/<TASK_ID>
```

Task detail에는 기존 필드를 그대로 제공한다.

- instructions
- description
- input schema
- output contract
- validation policy
- Worker와 실행 기본값

## 5.2 Task Run catalog item

```json
{
  "task_run_id": "01K...",
  "task_id": "01K...",
  "task_version": 3,
  "status": "completed",
  "task_summary": "제품 관련 위험 뉴스를 조사한다.",
  "result_summary": "위험 신호 4개와 관련 출처 12개를 정리했다.",
  "failure_reason": null,
  "worker": "codex",
  "trigger_type": "manual",
  "result_available": true,
  "artifact_count": 2,
  "artifact_roles": ["result", "sources"],
  "created_at": "2026-08-04T10:00:00+09:00",
  "completed_at": "2026-08-04T10:12:00+09:00"
}
```

실패 item도 같은 key를 가진다.

```json
{
  "task_run_id": "01K...",
  "task_id": "01K...",
  "task_version": 3,
  "status": "failed",
  "task_summary": "제품 관련 위험 뉴스를 조사한다.",
  "result_summary": null,
  "failure_reason": "Agent 인증 만료로 실행을 시작하지 못했다.",
  "worker": "codex",
  "trigger_type": "manual",
  "result_available": false,
  "artifact_count": 0,
  "artifact_roles": [],
  "created_at": "2026-08-04T10:00:00+09:00",
  "completed_at": "2026-08-04T10:00:05+09:00"
}
```

## 5.3 Field invariants

- 세 summary key는 새 receipt와 catalog item에 항상 존재한다.
- 성공 Run은 `failure_reason=null`이다.
- 결과를 검증하고 전달한 성공 Run은 가능한 경우 `result_summary`를 가진다.
- 결과 파일을 만들지 못한 실패 Run은 `result_summary=null`이다.
- 실패 Run은 non-empty `failure_reason`을 가진다.
- `task_id`가 없는 ad-hoc Run도 `task_summary`를 가질 수 있다.
- `task_version`은 등록 Task Run일 때만 값이 있다.
- Summary는 plain text이며 Markdown 또는 HTML을 요구하지 않는다.
- Catalog는 summary를 실행 지시로 해석하지 않는다.

## 5.4 Length limits

- `task_summary`: 최대 500 Unicode characters
- `result_summary`: 최대 1,000 Unicode characters
- `failure_reason`: 최대 1,000 Unicode characters

Relay는 저장 전에 surrounding whitespace를 제거한다. 제한을 넘는 fallback text는 Unicode character 경계에서 자르고 `…`를 붙인다.

Agent가 명시적으로 제공한 JSON `summary`가 제한을 넘으면 결과 전체를 실패시키지 않고 bounded summary만 receipt에 사용한다. 전체 `answer`는 기존 결과 파일에 그대로 남는다.

---

## 6. Summary ownership and resolution

## 6.1 `task_summary`

Registered Task에는 새 optional field `task_summary`를 추가한다.

생성·수정 경로에서 사용자가 또는 Agent가 명시적으로 제공할 수 있다.

Task Run 생성 시 Relay는 다음 우선순위로 immutable `task_summary`를 결정한다.

1. Task definition의 `task_summary`
2. Task definition의 `description`
3. instructions의 deterministic bounded snippet
4. ad-hoc Run이면 request task의 deterministic bounded snippet
5. history policy가 content 보존을 허용하지 않으면 `null`

결정된 값은 Task Run row와 Task snapshot에 기록한다. Task가 이후 수정돼도 과거 Task Run summary는 변경하지 않는다.

## 6.2 `result_summary`

JSON result schema에 optional `summary` string을 추가한다.

Request builder는 수행 Agent에게 다음을 요구한다.

> `summary`에는 수행한 작업과 실제로 만들어진 결과를 1~3개의 짧은 문장으로 작성한다. 품질을 스스로 보증하거나 결과에 없는 사실을 추가하지 않는다.

Relay는 성공·partial 결과에서 다음 순서로 `result_summary`를 결정한다.

1. 검증된 JSON result의 `summary`
2. JSON result의 `answer` bounded snippet
3. TXT result의 bounded snippet
4. 어떤 결과 text도 읽을 수 없으면 `null`

`result_summary`는 수행 Agent가 제안하지만 Relay가 검증된 결과에서 추출하고 길이를 제한한 뒤 receipt에 기록한다.

## 6.3 `failure_reason`

`failure_reason`은 수행 Agent가 아니라 Relay가 생성한다.

우선순위는 다음과 같다.

1. 최종 `RelayError` message
2. Job `error_message`
3. 마지막 Attempt의 failure message
4. 상태 기반 fallback (`Cancelled`, `Daemon restarted`, `Timed out` 등)

DB에서는 기존 `error_message`를 canonical source로 유지한다. Catalog와 receipt에서 이를 `failure_reason`이라는 안정적인 이름으로 노출한다. 동일 내용을 위한 새 DB column은 만들지 않는다.

## 6.4 Summary quality rules

좋은 `task_summary`:

- 수행 목적을 설명한다.
- 기대 결과가 무엇인지 드러낸다.
- 특정 실행의 날짜·Worker·상태를 포함하지 않는다.

좋은 `result_summary`:

- 실제로 수행한 범위를 설명한다.
- 결과의 핵심 수량 또는 산출물을 포함할 수 있다.
- “성공적으로 완료했다” 같은 상태 반복만으로 끝나지 않는다.
- 결과 파일에 없는 품질 주장이나 사실을 추가하지 않는다.

---

## 7. Persistence and migration

## 7.1 Schema revision

현재 DB schema v12에서 v13으로 올린다.

```sql
ALTER TABLE tasks ADD COLUMN task_summary TEXT;
ALTER TABLE jobs ADD COLUMN task_summary TEXT;
ALTER TABLE jobs ADD COLUMN result_summary TEXT;

CREATE INDEX IF NOT EXISTS idx_tasks_catalog
ON tasks(updated_at DESC, task_id DESC);

CREATE INDEX IF NOT EXISTS idx_jobs_catalog
ON jobs(created_at DESC, job_id DESC);
```

`failure_reason`은 기존 `jobs.error_message`를 사용한다.

## 7.2 New writes

- Task create/update는 `task_summary`를 저장한다.
- Registered Task Run 생성은 resolved Task summary를 Job row와 Task snapshot에 저장한다.
- ad-hoc Run은 history policy가 허용할 때 request에서 bounded summary를 저장한다.
- 성공/partial completion은 `result_summary`를 Job row에 저장한다.
- 실패 completion은 기존 `error_message`를 유지한다.
- non-replayable scrub은 `task_summary`와 `result_summary`도 제거한다.

## 7.3 Backfill

Migration transaction 안에서 결과 파일을 읽지 않는다. DB schema migration은 빠르고 결정적이어야 한다.

Schema migration 이후 별도의 idempotent backfill을 수행한다.

Task backfill:

1. `description`
2. bounded `instructions`

Job backfill:

1. `task_snapshot_json`의 `task_summary` 또는 `description`
2. 기존 `task_preview`
3. history policy가 허용할 때 `task_text` snippet

Result summary backfill:

1. 결과 파일이 존재하면 기존 `result_summary()` helper 사용
2. 파일이 없으면 `null`

Failure catalog는 기존 `error_message`를 사용하므로 별도 backfill이 필요 없다.

Backfill은 다음 성질을 가져야 한다.

- 기존 non-null summary를 덮어쓰지 않는다.
- 없는 파일을 오류로 처리하지 않는다.
- 사용자 Result 또는 Artifact를 변경하지 않는다.
- 배치 단위로 수행한다.
- daemon restart 후 다시 실행해도 안전하다.

## 7.4 Historical receipt policy

기존 on-disk `relay-receipt.json`과 기존 `receipt_json`을 migration에서 다시 쓰지 않는다.

- 기존 receipt는 생성 당시 schema를 보존한다.
- 새 receipt만 schema v2로 생성한다.
- Catalog는 normalized DB columns를 사용하므로 과거 receipt 형식과 독립적으로 동작한다.
- `engine.receipt()`는 저장된 역사 receipt를 반환하되 없는 catalog summary key를 임의로 역사 파일에 영구 기록하지 않는다.

---

## 8. Receipt schema v2

새 Task Run receipt에는 `receipt_schema_version=2`를 포함한다.

### 8.1 Success receipt

```json
{
  "receipt_schema_version": 2,
  "ok": true,
  "status": "completed",
  "job_id": "01K...",
  "task_run_id": "01K...",
  "task_summary": "제품 관련 위험 뉴스를 조사한다.",
  "result_summary": "위험 신호 4개와 관련 출처 12개를 정리했다.",
  "failure_reason": null,
  "worker": "codex",
  "result_path": "...",
  "artifact_path": "..."
}
```

### 8.2 Partial receipt

```json
{
  "receipt_schema_version": 2,
  "ok": true,
  "status": "partial",
  "task_summary": "제품 관련 위험 뉴스를 조사한다.",
  "result_summary": "국내 자료는 정리했으나 해외 자료 두 곳은 접근하지 못했다.",
  "failure_reason": null
}
```

미완료 항목은 기존 `missing_items_count`와 결과 파일의 `missing_items`로 확인한다. `failure_reason`은 기술적 실패 Run에 사용하고 partial 결과 설명을 중복하지 않는다.

### 8.3 Failure receipt

```json
{
  "receipt_schema_version": 2,
  "ok": false,
  "status": "failed",
  "job_id": "01K...",
  "task_run_id": "01K...",
  "task_summary": "제품 관련 위험 뉴스를 조사한다.",
  "result_summary": null,
  "failure_reason": "Agent 인증 만료로 실행을 시작하지 못했다.",
  "error_code": "AUTH_REQUIRED",
  "error_message": "Agent 인증 만료로 실행을 시작하지 못했다.",
  "attempts": []
}
```

기존 `error_code`, `error_message`는 호환성을 위해 유지한다.

API schema revision 5와 minimum GUI 1.1.0은 변경하지 않는다. Receipt schema만 독립적으로 2로 올린다.

---

## 9. Read-only catalog API

Catalog는 query 또는 ranking을 제공하지 않는다.

## 9.1 Capability manifest

```http
GET /v1/catalog
```

```json
{
  "ok": true,
  "catalog_schema_version": 1,
  "kinds": {
    "tasks": {
      "list_path": "/v1/catalog/tasks",
      "detail_path_template": "/v1/tasks/{task_id}",
      "order": "updated_at_desc"
    },
    "task_runs": {
      "list_path": "/v1/catalog/task-runs",
      "detail_path_template": "/v1/task-runs/{task_run_id}",
      "order": "created_at_desc"
    }
  }
}
```

## 9.2 Task catalog

```http
GET /v1/catalog/tasks?limit=100&cursor=<opaque>&updated_since=<ISO8601>
```

허용 parameter:

- `limit`: 1~200, default 100
- `cursor`: Relay가 반환한 opaque cursor
- `updated_since`: optional ISO-8601 lower bound

`q`, `query`, `score`, `sort`는 지원하지 않는다.

응답:

```json
{
  "ok": true,
  "catalog_schema_version": 1,
  "kind": "tasks",
  "items": [],
  "next_cursor": null,
  "has_more": false
}
```

정렬은 `(updated_at DESC, task_id DESC)`로 고정한다.

## 9.3 Task Run catalog

```http
GET /v1/catalog/task-runs?limit=100&cursor=<opaque>&status=completed&task_id=<ID>&from=<ISO>&to=<ISO>
```

허용 parameter는 목록 범위 제한용이다.

- `status`: exact normalized status
- `task_id`: exact Task ID
- `from`, `to`: 실행 시각 범위
- `limit`, `cursor`

이는 검색어 기반 검색이나 추천 기능이 아니다.

정렬은 `(created_at DESC, task_run_id DESC)`로 고정한다.

## 9.4 Cursor contract

- Cursor는 opaque URL-safe string이다.
- Client는 cursor 내용을 해석하지 않는다.
- Cursor에는 sort key와 마지막 ID를 포함하고 server가 검증한다.
- 잘못된 cursor는 `INVALID_CURSOR`를 반환한다.
- 같은 cursor를 재사용해도 중복 또는 누락 없이 같은 다음 범위를 반환해야 한다.
- 새 row가 추가돼도 이미 진행 중인 역방향 pagination의 정렬 계약이 깨지지 않아야 한다.

---

## 10. CLI contract

새 top-level command를 추가한다.

```text
relay catalog
relay catalog tasks
relay catalog task-runs
```

예:

```sh
relay catalog --machine
relay catalog tasks --limit 100 --machine
relay catalog tasks --cursor "<CURSOR>" --machine
relay catalog task-runs --status completed --limit 100 --machine
relay catalog task-runs --task-id "<TASK_ID>" --machine
```

상세 조회는 기존 command를 재사용한다.

```sh
relay task show <TASK_ID> --machine
relay show <RUN_ID> --machine
relay result <RUN_ID> --machine
relay artifact show <ARTIFACT_UID> --machine
relay artifact lineage <ARTIFACT_UID> --machine
```

Catalog command는 interactive table보다 `--machine` JSON 계약을 우선한다. 사람용 출력은 ID, 이름, Version, 상태와 짧은 summary만 표시한다.

기존 `task list`, `history`, `search` command는 변경하지 않는다.

---

## 11. Agent skill workflow

`skills/hermes-relay/SKILL.md`에 두 절차를 추가한다.

## 11.1 Registered Task discovery

규칙:

1. 요청에서 목적, 필요한 입력, 기대 출력, 제약을 분리한다.
2. `relay catalog tasks --machine`으로 catalog를 읽는다.
3. 항목이 더 있으면 `next_cursor`를 사용해 필요한 만큼 순회한다.
4. 이름과 `task_summary`를 읽고 유망 후보 3~5개를 선정한다.
5. 유망 후보 각각에 `relay task show <TASK_ID> --machine`을 호출한다.
6. 다음 항목을 비교한다.
   - instructions
   - input schema
   - output contract
   - validation policy
   - default Worker와 실행 profile
   - 현재 Version
7. 목적과 계약이 모두 맞는 Task만 선택한다.
8. 적합한 Task가 없으면 가장 가까운 Task를 억지로 실행하지 않고 새 Task 생성을 제안한다.
9. 선택한 Task ID, Version, 선택 이유를 상위 응답 또는 실행 기록에 남긴다.

Agent는 Relay가 relevance 순으로 catalog를 반환한다고 가정해서는 안 된다.

## 11.2 Past Task Run discovery and reuse

규칙:

1. `relay catalog task-runs --machine`으로 Task Run catalog를 읽는다.
2. `task_summary`, `result_summary`, `failure_reason`, status를 먼저 확인한다.
3. 실패 Run은 결과 재사용 후보에서 제외하되 동일 실패를 피하기 위한 참고로 사용할 수 있다.
4. 유망 Run만 `relay result`, `artifact show`, `artifact lineage`로 상세 조회한다.
5. Artifact가 현재 요청의 입력 계약과 맞는지 확인한다.
6. 재사용할 때는 Artifact UID를 명시적으로 전달한다.
7. 새 Task Run 완료 후 Lineage에서 원본 Artifact 연결을 확인한다.

## 11.3 Skill safety rule

Agent는 Relay Home의 SQLite 파일을 직접 열지 않는다.

- migration 경계를 우회하지 않는다.
- history privacy 정책을 우회하지 않는다.
- raw schema에 종속되지 않는다.
- Catalog와 detail API/CLI만 사용한다.

---

## 12. Privacy and retention

Summary는 원문보다 짧지만 여전히 업무 content다.

### 12.1 Registered Tasks

등록 Task 정의는 사용자가 명시적으로 영구 등록한 데이터이므로 Task catalog에 summary를 노출할 수 있다. 인증된 local API와 CLI 경계는 기존 Task detail과 동일하게 유지한다.

### 12.2 Task Run history

기존 `history_display_mode`와 `history_mode`를 따른다.

- `full`: 허용된 summary를 DB와 catalog에 보존·노출
- metadata: ad-hoc Task와 Result content summary는 `null`; 등록 Task의 ID, Version, name과 이미 영구 등록된 Task summary만 허용
- non-replayable: 완료·실패·취소 시 `task_summary`, `result_summary`를 scrub

`failure_reason`은 기술 오류 정보로 유지할 수 있지만 secret, credential, environment value가 포함되지 않도록 기존 오류 sanitization 경계를 적용한다.

### 12.3 Catalog response

- raw request JSON을 포함하지 않는다.
- 전체 instructions를 포함하지 않는다.
- Result 본문을 포함하지 않는다.
- Artifact content를 포함하지 않는다.
- credential, environment value, webhook secret를 포함하지 않는다.

---

## 13. Implementation slices

## Slice 1 — Contract and model

변경 대상:

- `relay/models.py`
- `relay/request_builder.py`
- `relay/validation.py`
- receipt schema constant owner
- focused unit tests

작업:

- TaskSpec에 optional `task_summary` 추가
- JSON result schema에 optional `summary` 추가
- summary normalization helper 추가
- receipt schema v2 상수와 예제 고정

검증:

- summary length와 type validation
- JSON result summary 허용
- 기존 schema v1.0 결과 호환

## Slice 2 — Database migration and persistence

변경 대상:

- `relay/db.py`
- `tests/test_migrations.py`
- Phase 3 Task DB tests
- 신규 catalog DB tests

작업:

- schema v13 migration
- Task/Job summary columns
- catalog ordering indexes
- create/update/read persistence
- privacy scrub 확장
- idempotent backfill

검증:

- v12→v13 migration
- fixture migration
- existing DB rows preserved
- non-replayable summary scrub
- 결과 파일 누락 시 backfill 안전성

## Slice 3 — Receipt production

변경 대상:

- `relay/engine.py`
- `relay/search/__init__.py`
- receipt/API tests

작업:

- Task Run 생성 시 task summary snapshot
- 성공/partial result summary 추출
- 실패 reason normalization
- receipt v2 작성
- FTS rebuild가 stored summary를 우선 사용하도록 변경

검증:

- JSON agent summary
- JSON answer fallback
- TXT fallback
- 실패 before-start
- 실패 after-attempt
- partial result
- 결과 파일 삭제 후 stored summary 유지

## Slice 4 — Catalog API and CLI

변경 대상:

- `relay/api.py`
- `relay/daemon.py`
- `relay/cli.py`
- daemon route tests
- CLI tests

작업:

- catalog capability manifest
- Task catalog route
- Task Run catalog route
- opaque cursor
- `relay catalog` command
- additive compatibility fields

검증:

- pagination without duplicate/omission
- invalid cursor
- exact status/task/time filters
- no full prompt or Result content leakage
- `--machine` stable response shape

## Slice 5 — Agent skill and end-to-end contract

변경 대상:

- `skills/hermes-relay/SKILL.md`
- 신규 daemon/CLI E2E tests
- README 또는 manual의 Agent usage section

작업:

- Task 후보 선정 절차
- Task Run 후보 선정 절차
- no-match behavior
- Artifact reuse and lineage verification

검증 시나리오:

```text
Task catalog page 읽기
→ 후보 Task 두 개 상세 조회
→ 하나를 선택해 실행
→ receipt summary 확인
→ Task Run catalog에서 해당 Task Run 확인
→ Artifact 조회
→ 새 Task Run 입력으로 Artifact 재사용
→ Lineage 확인
```

---

## 14. Test plan

## 14.1 Unit tests

- summary normalization and limits
- Task summary resolution precedence
- Result summary resolution precedence
- failure reason fallback
- cursor encode/decode and validation
- catalog item serialization

## 14.2 Database tests

- v12→v13 migration
- Task summary CRUD
- Job summary persistence
- deterministic catalog ordering
- cursor boundary with identical timestamps
- history metadata redaction
- non-replayable scrub
- backfill idempotency

## 14.3 Engine tests

- registered Task summary snapshot
- edited Task does not change past Task Run summary
- ad-hoc Task summary
- JSON provided summary
- JSON answer fallback
- TXT result fallback
- failed Task Run failure reason
- Result deletion does not remove stored summary

## 14.4 API/CLI tests

- `/v1/catalog`
- paginated `/v1/catalog/tasks`
- paginated `/v1/catalog/task-runs`
- exact filters
- invalid parameters
- authentication
- `relay catalog ... --machine`
- existing `task list`, `history`, `search` regressions

## 14.5 Full regression

```powershell
& 'D:\Python314\python.exe' -m unittest discover -s tests
ruff check relay tests
& 'D:\Python314\python.exe' -m compileall -q relay tests
git diff --check
& 'D:\Python314\python.exe' build_release.py
```

Release build도 `D:\Python314\python.exe`를 명시적으로 사용한다.

---

## 15. Compatibility

- DB migration은 additive다.
- 기존 Task/Job ID를 변경하지 않는다.
- 기존 receipt 파일을 다시 쓰지 않는다.
- 기존 `error_code`, `error_message`를 제거하지 않는다.
- 기존 `task list`, `history`, `search` command를 유지한다.
- 기존 API schema revision 5를 유지한다.
- 새 catalog endpoint를 모르는 구버전 GUI는 영향받지 않는다.
- schema v13 database backup과 migration failure recovery는 기존 정책을 따른다.

### 15.1 Public terminology migration

이 계획의 용어 정리는 문자열 치환이나 물리 스키마 rename이 아니라 공개 경계의 전환이다.

- GUI 메뉴·제목·빈 상태·도움말: `Task Runs` 또는 문맥상 `Attempts`, `Project Runs`, `Artifacts`
- CLI 새 도움말과 catalog 예시: `Task Run`, `Project Run`, `Attempt`
- 새 API payload: `task_run_id`, `project_run_id`, `attempt_id`
- 기존 API payload의 `job_id`, `/v1/jobs`, 기존 CLI 옵션: 호환 입력/출력으로 유지
- 기존 DB table/column과 Python 내부 symbol: migration 안정화 전까지 유지
- 공개 오류 문구는 `Task Run not found`처럼 고치되 기존 오류 code는 alias로 보존

완료 기준은 코드 내부에 `job` 문자열이 0개가 되는 것이 아니다. 사용자가 새 기능을 사용할 때 `Job`을 배워야 하지 않고, 기존 데이터와 클라이언트가 깨지지 않는 것이 기준이다.

---

## 16. Performance constraints

- Catalog list는 Result 또는 Artifact 파일을 읽지 않는다.
- Catalog list는 receipt JSON 전체를 매 row 파싱하지 않는다.
- 목록 item은 bounded summary만 포함한다.
- default page 100, maximum page 200이다.
- ordering index를 사용한다.
- backfill의 파일 읽기는 migration transaction 밖에서 batch 처리한다.
- Catalog API는 N+1 Artifact query를 피하고 aggregate query 또는 bounded batch lookup을 사용한다.

---

## 17. Acceptance criteria

다음 조건을 모두 충족하면 완료다.

1. Agent가 `relay catalog tasks --machine`으로 등록 Task를 페이지 단위로 읽을 수 있다.
2. Catalog item만 보고 유망 Task 후보를 선정할 수 있다.
3. Agent가 선택 후보의 전체 Task 정의를 기존 `task show`로 읽을 수 있다.
4. 새 성공·partial·실패 receipt에 세 summary key가 항상 존재한다.
5. 실패 Run에는 non-empty `failure_reason`이 있다.
6. 새 성공 Run의 summary가 DB에 남는다.
7. Result 파일을 삭제해도 Task Run catalog summary가 유지된다.
8. non-replayable Run은 summary content를 남기지 않는다.
9. Catalog는 query, score, recommendation을 제공하지 않는다.
10. Agent 스킬이 후보 목록→전문 비교→선택 절차를 명시한다.
11. 검색→선택→실행→receipt→Artifact 재사용 E2E가 통과한다.
12. 기존 전체 test suite, Ruff, compile, release build가 통과한다.
13. 새 GUI·문서·CLI 도움말·catalog 계약에 `Job` 또는 단독 `Run`을 신규 공개 용어로 사용하지 않는다.
14. 기존 `job_id`/`/v1/jobs`/`relay search --kind runs` 호환 테스트가 계속 통과한다.

---

## 18. Commit strategy

권장 commit 순서:

1. `feat: add task and run summary contracts`
2. `feat: persist catalog summaries with schema v13`
3. `feat: emit receipt schema v2 summaries`
4. `feat: expose read-only task and run catalogs`
5. `docs: teach agents catalog-driven task selection`
6. `test: cover catalog-to-artifact reuse flow`
7. `docs: standardize public Project Task Attempt terminology`

각 commit은 독립적으로 migration 또는 contract 검증이 가능해야 한다. Generated `relay.pyz`, Relay Home data, 실제 Result/Artifact, credential은 commit하지 않는다.

---

## 19. Deferred follow-ups

Catalog 계약이 실제 사용에서 안정화된 뒤에만 다음을 재평가한다.

- Project catalog (implemented in `Relay_Agent_Mission_Hardening_Implementation_Plan_v1.0.md`)
- Project Run catalog (implemented in `Relay_Agent_Mission_Hardening_Implementation_Plan_v1.0.md`)
- Task Version registry
- bulk Task detail API
- recursive Artifact lineage graph
- GUI catalog browser
- external Agent가 자체적으로 만든 index cache
- semantic search 또는 embedding

이 follow-up은 이번 구현의 완료 조건이 아니다.

---

## 20. Final product behavior

완성 후 Agent의 기본 동작은 다음과 같다.

```text
사용자 요청 수신
→ Task catalog 읽기
→ 이름과 summary로 후보 선정
→ 후보의 전체 정의 읽기
→ 계약을 비교해 Task 선택
→ Task 실행
→ summary가 포함된 receipt 회수
→ 필요할 때 Task Run catalog에서 과거 결과 후보 선정
→ Result·Artifact·Lineage 상세 조회
→ Artifact UID로 명시적 재사용
```

Relay는 이 과정에서 무엇이 가장 적합한지 판단하지 않는다.

Relay는 Agent가 판단할 수 있도록 업무 원장을 정확하고 지속적으로 제공한다.
