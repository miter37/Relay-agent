# Relay Agent Live Worker Scenario Validation Report v1.0

## 검증 목적

실제 Worker를 mock 없이 사용해 Agent Catalog, Project DAG, Artifact handoff, 후속 Task 입력, lineage, 검색이 하나의 오케스트레이션 루프에서 끝까지 동작하는지 확인했다. 모든 런너는 `D:\Python314\python.exe`를 사용했다.

## 시나리오

공통 복합 시나리오는 다음과 같다.

```text
Evidence analysis ─┐
Architecture analysis ─┼─> Decision synthesis ─> Final decision review (A1)
Risk analysis ──────┘
```

- 5개 등록 Task
- 3개 병렬 선행 Task
- 1개 Synthesis Project 노드
- Synthesis output Artifact를 독립 Review Task의 `A1` 입력으로 연결
- Project Run 종료, Artifact materialization, lineage, Run/Artifact 검색을 확인

## 최종 결과

| 실제 Worker | 버전 | Deep doctor | Project | 후속 Review | lineage/search |
|---|---|---:|---:|---:|---:|
| Codex | `codex-cli 0.144.3` | healthy | completed | completed | 통과 |
| Claude | `2.1.221` | healthy | completed | completed | 통과 |
| Antigravity | `1.1.10` | healthy | 6/6 시나리오 통과 | 포함 | 통과 |

Codex와 Claude 복합 시나리오 모두 선행 Task 중 `PARTIAL` 결과가 포함됐지만 Project는 `completed`로 종료했고, Synthesis Artifact 소비 lineage 1건과 Artifact 검색 결과 4건을 확인했다. Antigravity는 기존 실제 검증 보고서의 S1–S6 전체 통과 결과를 재사용했다. 사용자가 별도 실행 중인 Antigravity 세션은 종료하거나 변경하지 않았다.

## 최초 결함과 보완

첫 Codex 실행에서 선행 Task Run들은 Artifact를 생성하고 `PARTIAL`로 정상 종료했지만 Project step이 계속 `running`, Synthesis가 `pending`으로 남았다. 원인은 ProjectRuntime이 `COMPLETED`만 성공 terminal로 인정한 것이었다.

추가로 후속 Task 요청서의 Artifact 입력 안내가 Relay Home의 snapshot 경로를 표시하고 실제 Worker workspace의 `input/<snapshot filename>` 경로를 표시하지 않는 결함을 확인했다. Agent가 입력 파일을 찾지 못해 후속 Review가 실패할 수 있는 경로였다.

수정 내용:

- `PARTIAL`을 Project dependency progression의 성공 terminal 상태로 처리
- Project receipt의 `warnings`에 `TASK_RUN_PARTIAL` 기록
- 최종 Artifact 누락 등 구조적 오류는 계속 Project `failed`
- Artifact 입력 안내를 실제 workspace 경로인 `input/<snapshot filename>`으로 수정
- 두 동작에 회귀 테스트 추가

구현 계획은 [Relay_Live_Worker_Scenario_Hardening_Implementation_Plan_v1.0.md](Relay_Live_Worker_Scenario_Hardening_Implementation_Plan_v1.0.md)에 기록했다.

## 결론

현재 코드 기준으로 실제 Codex와 Claude의 복합 오케스트레이션 루프는 오류 없이 완료됐고, 기존 실제 Antigravity S1–S6 검증도 모두 통과 상태다. 이번 사이클에서 발견된 Project 고착과 Artifact 경로 불일치는 수정 및 회귀 테스트로 보완됐다.

이 검증은 Worker의 사실 정확도를 평가하지 않고 Relay의 실행 계약, 상태 전이, Artifact handoff, lineage 및 검색 연결을 평가한다.
