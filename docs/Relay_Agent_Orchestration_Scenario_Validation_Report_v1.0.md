# Relay Agent 오케스트레이션 시나리오 검증 보고서 v1.0

검증일: 2026-08-04  
실행 환경: `D:\Python314\python.exe`  
Worker: bundled mock Codex  
검증 방식: Relay의 Task/Project 서비스와 실제 Worker 실행 경로 사용. 운영 DB를 직접 조작해 상태를 만들지 않음.

## 요약

5개 시나리오를 실행했고, 실행·Artifact 전달·Project Runtime·실패 영수증·Catalog 조회는 정상 동작했다.

| 항목 | 결과 |
|---|---:|
| 등록 Task | 11개 |
| 생성 Task Run | 12개 |
| 실행 Project | 3개 |
| 완료 Project Run | 3개 |
| Doctor deep audit | 통과 |
| 실행 실패 시나리오 | 실패 영수증 생성 후 재실행 성공 |
| Artifact 본문 읽기 | `available=true`, canonical field `text` |

초기 검증 당시에는 새로 실행된 Run/Artifact가 즉시 과거 검색 결과에 나타나지 않아 Run 검색과 Artifact 검색이 각각 0건이었다. 이후 Search Index Hardening을 구현했고, 같은 orchestration 시나리오를 재실행해 이 문제의 해결을 확인했다.

## 시나리오 1 — 단독 Task 결과 이어받기

흐름:

```text
Collect source material
  -> Artifact 01KZ5Y397CHBGBTFD014KBT2SD
  -> Summarize source material
```

- 원본 Task Run: `01KZ5Y38TARSG9612DNNDARHW3`
- 소비 Task Run: `01KZ5Y398ST03601AEBQ2FHNZB`
- 소비 Run 입력 Artifact: `01KZ5Y397CHBGBTFD014KBT2SD`
- 소비 Run lineage count: `1`
- 두 Run 모두 `completed`
- 영수증에 `task_summary`, `result_summary`가 기록됨

판정: 통과. Artifact UID를 다음 Task 입력으로 연결하고 provenance를 확인할 수 있었다.

## 시나리오 2 — 순차형 Project

Project: `Market research report`  
Project ID: `01KZ5Y39QJQEV538R692K1PZG4`  
Project Run ID: `01KZ5Y39RAZYTTVS7AJQPY3HWH`

```text
research -> clean -> report
```

- Step 수: 3
- 모든 Step: `completed`
- 실행된 Step Run 수: 3
- 외부 입력: 없음

판정: 통과. 순차 의존성과 앞 단계 결과의 다음 단계 연결이 정상 동작했다.

## 시나리오 3 — 병렬 분기 후 통합

Project: `Product launch review`  
Project ID: `01KZ5Y3BAPKHDSHTMTYMTWTGHY`  
Project Run ID: `01KZ5Y3BBDSMPXWY3VJP8HY62W`

```text
market ─┐
        ├─ synthesis
risk   ─┘
```

- Step 수: 3
- `market`, `risk`, `synthesis` 모두 `completed`
- 실행된 Step Run 수: 3
- 외부 입력: 없음

판정: 통과. 독립 branch 두 개가 실행된 후 통합 Task가 실행됐다.

## 시나리오 4 — Project 간 Artifact 재사용

Project A의 최종 report Artifact를 Project C의 외부 입력으로 전달했다.

- 원본 Project Run: `01KZ5Y39RAZYTTVS7AJQPY3HWH`
- 원본 Artifact: `01KZ5Y3B86S0286XK3TBJP4RZZ`
- 소비 Project Run: `01KZ5Y3CW46SJRFSVT0MRKMFR8`
- 소비 Project의 외부 입력 수: `1`
- 원본 Artifact lineage 소비자 수: `1`
- 소비 Project Run: `completed`

판정: 통과. 실행 후 원본 Artifact의 `artifact_lineage` 소비자 연결도 확인했다.

## 시나리오 5 — 실패 영수증과 재실행

Worker 설정을 유효하지 않은 상태로 바꾸어 실패를 유도한 뒤, 설정을 복구하고 같은 Task를 재실행했다.

실패 Run:

- Task Run: `01KZ5Y3DZC3QB81QK02TNBQZD5`
- 상태: `failed`
- `failure_reason`: `codex has no capability audit for its installed version. Run relay doctor --worker codex --deep.`
- `result_summary`: `null`

복구 Run:

- Task Run: `01KZ5Y3E0W4J7S3A6RYKJA97DE`
- 상태: `completed`
- `result_summary`: `Mock answer from codex`
- 생성 Artifact: `01KZ5Y3EDFRDV1QCB19D6C0D8J`

판정: 통과. 실패 원인이 영수증에 보존되고, 환경 복구 후 재실행이 성공했다.

## Catalog·본문·검색 검증

- Task Catalog: 11개
- Task Run Catalog: 12개
- Project Catalog: 3개
- Project Run Catalog: 3개
- Artifact content: `available=true`, `text` field 존재
- 초기 baseline Run 검색: 0건
- 초기 baseline Artifact 검색: 0건

검색어는 각각 실행 결과에 실제 존재하는 `Mock`, `RELAY_ARTIFACT_OK`를 사용했다. 따라서 단순히 검색어가 데이터에 없어서 생긴 결과로 보기는 어렵다.
현재 코드에는 `index_run()`과 `index_artifact()` 및 전체 `rebuild_search_index()`가 존재하지만, 새 실행 완료 시점에 자동 색인하는 호출 경로가 확인되지 않았다. 이 부분은 관찰 결과에 기반한 원인 추정이며, 별도 구현 검증이 필요하다.

## Search Index Hardening 후속 검증

- 새 성공 Run 검색: 통과
- 실패 Run 검색: 통과
- 새 Artifact 본문 검색: 통과
- 기존 DB 재오픈 시 stale index backfill: 통과
- non-replayable 검색 비노출 회귀: 통과

구현계획: [`Relay_Agent_Search_Index_Hardening_Implementation_Plan_v1.0.md`](Relay_Agent_Search_Index_Hardening_Implementation_Plan_v1.0.md)

검증 러너: [`test_agent_orchestration_scenarios.py`](../tests/test_agent_orchestration_scenarios.py)
