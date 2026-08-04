# Relay Agent Skill Mission Validation Report v1.0

- **검증일:** 2026-08-04
- **검증 Python:** `D:\Python314\python.exe`
- **대상:** Catalog-first Agent workflow, Task/Task Run, Project Run, Artifact, Lineage
- **검증 방식:** 임시 Relay Home에서 실제 Relay DB/Engine/Daemon/CLI 경로를 사용한 가상 Worker 미션

## 1. 검증 범위와 안전 경계

`skills/hermes-relay/SKILL.md`의 절차를 다음 순서로 적용했다.

```text
preflight
→ Catalog capability 확인
→ Task catalog 페이지 순회
→ summary로 후보 선정
→ Task detail 비교
→ Task Run catalog 확인
→ Receipt와 Artifact 조회
→ Artifact UID 재사용
→ Lineage와 snapshot 검증
→ 실패 및 무결성 오류 처리
```

스킬 문서의 “provider CLI를 직접 호출하지 않는다” 규칙을 지키기 위해 Claude/Codex/Antigravity를 실제로 호출하지 않았다. 대신 Relay Engine이 생성하는 실제 Task Run·Project Run·Artifact·Lineage 구조를 통제된 완료/실패 상태로 만들고, Daemon과 `relay ... --machine` CLI로 조회했다. 따라서 이번 결과는 Relay 내부 계약과 Agent workflow 검증이며, 외부 Worker 설치·인증·실제 추론 품질 검증은 포함하지 않는다.

## 2. 가상 미션 구성

### 2.1 등록한 단독 Task 5개

| 순서 | Task | 목적 | 수행 결과 |
|---:|---|---|---|
| 1 | Collect source | downstream 작업용 source report 생성 | 완료 |
| 2 | Summarize source | 이전 source Artifact 요약 | 완료, Task 1 Artifact를 `A1`로 재사용 |
| 3 | Validate facts | source report 사실 검증 | 완료 |
| 4 | Package findings | 이전 validation Artifact 패키징 | 완료, Task 3 Artifact를 `A1`로 재사용 |
| 5 | Review findings | 최종 findings 검토 | 의도적 실패, `MOCK_REVIEW_FAILURE` |

총 5개의 등록 Task와 5개의 단독 Task Run을 생성했다. 이 중 2개는 이전 Task의 결과를 Artifact UID로 이어받았다.

### 2.2 등록한 Project 3개

| Project | 구조 | 검증한 내용 |
|---|---|---|
| Sequential research pipeline | `source → summary` | 연결된 Artifact role과 `A1` 입력 전달 |
| Parallel quality review | `validate`와 `review` 병렬 | 독립 Node 동시 dispatch와 복수 final Artifact |
| External artifact packaging | 외부 Artifact → `package` | Project Run 생성 시 외부 Artifact snapshot 입력 |

각 Project를 등록하고 Project Run을 생성한 뒤, Project Runtime의 실제 reconciliation·dispatch·완료·final Artifact 선택 경로를 통과시켰다.

## 3. 실제 수행 절차와 결과

### Mission A — preflight와 Catalog 탐색

실행한 CLI 흐름:

```text
relay catalog --machine
relay security --machine
relay catalog tasks --limit 2 --machine
relay task show <TASK_ID> --machine
```

결과:

- `catalog_schema_version=1` 확인
- Task catalog 첫 페이지 2개 확인
- `has_more=true` pagination 확인
- Catalog item에 전체 `instructions`가 포함되지 않음
- Task detail에서 선택 후보의 summary와 전체 정의 확인
- “quantum drug discovery”처럼 맞는 후보가 없는 가상 요청은 빈 후보로 판정되어 새 Task 제안 경로로 분기 가능함을 확인

### Mission B — 과거 Task Run과 결과물 탐색

```text
relay catalog task-runs --limit 200 --machine
relay catalog task-runs --status failed --machine
relay result <TASK_RUN_ID> --machine
relay artifact show <ARTIFACT_UID> --machine
relay artifact read <ARTIFACT_UID> --machine
```

결과:

- 전체 Task Run catalog item 10개 확인
  - 단독 Task Run 5개
  - Project child Task Run 5개
- 실패 Run의 `failure_reason`이 `Review worker unavailable in simulation`으로 보존됨
- 성공 Run receipt에 `task_summary`와 `result_summary`가 존재함
- Artifact metadata, UID, role, SHA-256 조회 성공
- Artifact 본문 조회 응답의 실제 필드는 `text`임을 확인

### Mission C — Artifact UID 재사용과 Lineage

두 개의 단독 체인을 검증했다.

```text
Collect source
  → source Artifact
  → Summarize source (A1)

Validate facts
  → validation Artifact
  → Package findings (A1)
```

검증 결과:

- 두 consumer Task Run 모두 생성 성공
- 두 Lineage 모두 `binding_mode=snapshot`
- source `artifact_uid`와 alias `A1` 보존
- snapshot 파일이 실제로 존재함
- snapshot SHA-256이 Lineage metadata와 일치함

Project 3에서도 외부 Artifact를 `package` Node의 `A1`로 전달하고 Project Run을 완료했다.

### Mission D — 실패 Run 회피

`Review findings`를 의도적으로 실패시킨 뒤:

```text
relay catalog task-runs --status failed --machine
```

결과:

- 실패 Run이 catalog에 나타남
- `failure_reason`이 비어 있지 않음
- 성공 후보 목록에 실패 Run을 포함하지 않는 Agent 분기 조건을 확인함

### Mission E — Artifact 변조 차단

source Artifact 파일을 저장된 SHA-256과 다르게 변경한 뒤 동일 UID로 재사용을 시도했다.

결과:

```text
ARTIFACT_CHANGED
```

변조된 Artifact는 새 Task Run 입력으로 수락되지 않았다.

## 4. 검증 수치

```json
{
  "registered_standalone_tasks": 5,
  "standalone_task_runs": 5,
  "chained_standalone_runs": 2,
  "registered_projects": 3,
  "project_run_statuses": ["completed", "completed", "completed"],
  "catalog_task_run_items": 10,
  "artifact_uid_reuse": "passed",
  "artifact_tamper_detection": "ARTIFACT_CHANGED",
  "cli_contract_checks": "passed"
}
```

## 5. 검증 중 발견한 검증기 보정

첫 번째 검증 스크립트는 Artifact 본문을 `content`로 가정했지만 실제 API 계약은 `text`였다. 두 번째 검증 스크립트는 Project Run 상태를 대문자 `COMPLETED`로 가정했지만 실제 계약은 `completed`였다. 둘 다 애플리케이션 결함이 아니라 검증기 기대값 오류였으며, 스킬 문서와 현재 API 응답에 맞게 보정한 세 번째 실행에서 전체 시나리오가 통과했다.

## 6. 결론

현재 Relay는 Agent가 다음을 수행할 수 있는 수준으로 동작한다.

1. 등록 Task를 Catalog에서 페이지 단위로 읽는다.
2. summary와 계약 존재 여부로 후보를 좁힌다.
3. 선택 Task의 전체 정의를 조회한다.
4. 과거 Task Run의 성공·실패와 summary를 비교한다.
5. Artifact UID로 이전 결과를 새 Task 또는 Project 입력으로 재사용한다.
6. Lineage와 snapshot hash로 입력 연결을 검증한다.
7. 실패 Run과 변조 Artifact를 재사용 후보에서 제외한다.

남은 실제 운영 검증은 외부 Worker의 설치·인증·실제 추론 실행을 연결한 smoke test다. 이는 스킬의 직접 provider 호출 금지 규칙과 별도의 운영 환경 검증 영역이다.
