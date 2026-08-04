# Relay Live Worker Scenario Hardening Implementation Plan v1.0

## 목적

실제 Worker를 `mock` 없이 실행하는 복합 Project 시나리오에서, 선행 Task Run이 정상적으로 결과 Artifact를 만들었음에도 응답 상태가 `PARTIAL`이면 Project가 영원히 `running`에 남는 결함을 수정한다.

## 재현 증거

- 실행 대상: 실제 Codex Worker (`D:\Python314\python.exe`로 Relay 런너 실행)
- 시나리오: Evidence / Architecture / Risk 3개 병렬 Task → Synthesis Task → 후속 Review Task
- 관찰 결과: 선행 3개 Task Run은 `PARTIAL`로 종료되고 Markdown Artifact를 각각 생성했다.
- 결함 상태: `project_run_steps`가 세 선행 노드를 계속 `running`으로 유지하고, Synthesis는 `pending`, Project Run은 `running`으로 고착됐다.
- 원인: `relay/projects/runtime.py`가 Task Run의 성공 상태로 `COMPLETED`만 인정하고 `PARTIAL`을 처리하지 않는다.

## 구현 범위

1. `PARTIAL`을 Project dependency progression에서 성공적인 terminal Task Run으로 취급한다.
2. `PARTIAL` Task Run을 사용한 Project에는 비차단 경고를 Project receipt의 `warnings`에 남긴다.
3. 최종 Artifact 누락 같은 구조적 Project 오류는 기존처럼 Project `failed`로 유지한다.
4. `ProjectRuntime` 단위 회귀 테스트를 추가한다.
5. 같은 실제 Worker 복합 시나리오를 Codex, Claude, Antigravity 순으로 재실행하고, Project 종료·Artifact 연결·후속 Task 입력·검색 결과를 확인한다.

## 완료 기준

- `PARTIAL` 선행 Task가 있더라도 Project가 `running`에 고착되지 않고 terminal 상태에 도달한다.
- 후속 노드가 선행 Artifact를 입력으로 받아 실행된다.
- Project receipt에서 partial 결과가 경고로 식별된다.
- 최종 Artifact와 lineage/search 결과가 유지된다.
- 관련 단위 테스트와 전체 테스트가 통과한다.
- 실제 Worker 재검증에서 코드 결함으로 인한 오류가 재현되지 않는다.

## 비범위

- Worker가 실제로 실패한 경우를 성공으로 바꾸지 않는다.
- `PARTIAL`의 의미나 Worker별 응답 생성 규칙을 변경하지 않는다.
- 사용자가 별도로 실행 중인 Antigravity 세션을 종료하거나 설정을 변경하지 않는다.
