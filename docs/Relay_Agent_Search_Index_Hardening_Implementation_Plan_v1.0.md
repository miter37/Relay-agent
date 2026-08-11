# Relay Agent Search Index Hardening Implementation Plan v1.0

상태: 구현 완료 및 검증 통과 (2026-08-04)

## 1. 목적

실제 Task/Project 실행 후 생성된 Task Run과 Artifact가 기존 FTS5 검색 결과에 즉시 나타나도록 검색 인덱스 수명주기를 보완한다.

이번 변경은 검색 UI나 새로운 검색 알고리즘을 만드는 작업이 아니다. 현재의 FTS5 계약과 검색 API를 유지하면서, 실행 결과를 빠뜨리지 않는 것이 목표다.

## 2. 확인된 문제

- `Database.index_run()`과 `Database.index_artifact()`는 존재하지만 정상 실행 완료 경로에서 자동 호출되지 않는다.
- 따라서 새 Task Run과 Artifact가 `run_search`, `artifact_search`에 들어가지 않는다.
- 기존 DB를 새 코드로 열어도 검색 테이블이 비어 있거나 일부만 있으면 과거 데이터가 검색되지 않을 수 있다.
- `replayable=0` 실행은 `scrub_non_replayable()` 이후에 색인해야 Task 내용과 결과 요약이 검색 인덱스에 남지 않는다.

## 3. 구현 범위

### Slice 1 — terminal Run 자동 색인

`RelayEngine`에 best-effort 검색 색인 helper를 추가한다.

- 성공/partial Run: Job 상태와 receipt 저장, Artifact DB 등록, non-replayable scrub 이후 `index_run()`과 각 Artifact `index_artifact()` 호출
- 실패 Run: 실패 receipt와 상태 저장, scrub 이후 `index_run()` 호출
- 취소 Run: 취소 상태 저장과 scrub 이후 `index_run()` 호출
- 색인 실패가 Task 실행 자체를 실패시키지 않도록 경고 로그만 남긴다.

### Slice 2 — 기존 데이터 backfill

Database 초기화 시 FTS5 테이블과 원본 개수 차이를 감지한다.

- Job 수와 `run_search` 행 수 비교
- UID가 있는 Artifact 수와 `artifact_search` 행 수 비교
- 차이가 있으면 기존 `rebuild_search_index()`를 한 번 실행
- FTS5 미지원 환경은 기존 동작대로 검색 불가 상태를 유지
- 새로운 스키마나 검색 응답 필드는 추가하지 않는다.

### Slice 3 — 회귀 검증

- 실제 bundled mock Worker 성공 실행 후 결과 summary 검색
- 실제 Artifact 본문 검색
- 실패 실행 후 Run이 검색 인덱스에 들어가는지 확인
- non-replayable 실행의 민감한 Task 내용이 검색되지 않는지 확인
- 기존 수동 rebuild 및 semantic FTS fallback 테스트 유지
- 기존 orchestration 시나리오에서 검색 결과가 0건이 아닌지 확인

## 4. 성공 기준

- 새 성공 Task Run의 `result_summary` 또는 Task text를 `search runs`로 찾을 수 있다.
- 새 Artifact의 본문을 `search artifacts`로 찾을 수 있다.
- 실패 Task Run도 검색 가능한 실행 이력으로 남는다.
- non-replayable Task의 원문·요약이 검색 결과에 노출되지 않는다.
- 전체 기존 테스트와 orchestration 시나리오가 통과한다.
- Python 실행은 `D:\Python314\python.exe`를 사용한다.

## 5. 비범위

- 검색 랭킹 또는 semantic embedding backend 변경
- Task Catalog 자체에 대한 새 검색 엔진 추가
- 기존 `items` 응답 계약 변경
- raw log 전문 색인

## 6. 예상 변경 파일

- `relay/engine.py`
- `relay/db.py`
- `tests/test_phase2.py` 또는 검색 전용 회귀 테스트
- `tests/test_agent_orchestration_scenarios.py`
- `docs/Relay_Agent_Search_Index_Hardening_Implementation_Plan_v1.0.md`
