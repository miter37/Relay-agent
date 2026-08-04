# Relay Agent 오케스트레이션 시나리오 검증 보고서 v1.1

| 항목 | 값 |
|---|---|
| 검증일 | 2026-08-04 |
| 역할 | Orchestration Agent (Relay 사용) |
| Python | `D:\Python314\python.exe` |
| Worker | bundled mock Codex (`mocks/codex.cmd`) |
| 실행 경로 | 임시 Relay Home + 실제 Engine / ProjectService / ProjectRuntime / Catalog / Search API |
| 러너 | `tests/test_agent_orchestration_scenarios.py` |
| 운영 DB 조작 | 없음 (임시 홈에서만 수행) |

## 1. 요약

오케스트레이션 에이전트 관점에서 제안했던 **S1–S6 시나리오를 모두 실행**했다.  
Task 등록, Artifact 입력 연결, 순차/병렬 Project, Project 간 Artifact 재사용, 실패 영수증·재실행, Catalog·검색·본문 조회까지 **전부 통과**했다.

| 지표 | 결과 |
|---:|---:|
| Doctor deep audit (codex) | 통과 |
| 등록 Task | 11 |
| 추적된 단독 Task Run | 4 (체인 2 + 실패 1 + 복구 1) |
| Catalog Task Run 수 | 12 (Project step Run 포함) |
| 등록 Project | 3 |
| 완료 Project Run | 3 |
| Run 검색 히트 | 11 |
| Artifact 검색 히트 | 11 |
| 실패 Run 검색 히트 | 1 |
| Artifact 본문 필드 | `text` (`available=true`) |
| 시나리오 판정 | **6/6 통과** |

이전 보고서(v1.0)에서 관찰됐던 “새 실행이 검색에 안 잡힘” 문제는 **이번 실행에서는 재현되지 않았다.**

---

## 2. 시나리오 설계 (검증 전 합의안)

| ID | 이름 | 목적 |
|---|---|---|
| S1 | 단독 체인 | Task 결과 Artifact → 다음 Task 입력 |
| S2 | 순차 Project | research → clean → report |
| S3 | 병렬 후 통합 | market ∥ risk → synthesis |
| S4 | Project 간 재사용 | Project A report → Project C 외부 입력 |
| S5 | 실패·재실행 | 실패 영수증 보존 후 복구 실행 |
| S6 | Catalog-first | 목록·검색·본문·lineage로 후보/자산 탐색 |

---

## 3. 시나리오별 결과

### S1 — 단독 Task 결과 이어받기 ✅

```text
Collect source material
  → Artifact 01KZ5YTSP1P6MX74ENY9SBCS4P
  → Summarize source material (입력 A1)
```

| 항목 | 값 |
|---|---|
| 원본 Task Run | `01KZ5YTS91DDA30568H0KGJ2B9` |
| 원본 Artifact | `01KZ5YTSP1P6MX74ENY9SBCS4P` |
| 소비 Task Run | `01KZ5YTSQRXYX9CVVGQFDA6WWV` |
| 소비 lineage count | `1` |
| 양쪽 status | `completed` |
| 영수증 | `task_summary`, `result_summary` 기록 |

**판정:** 통과. Artifact UID로 다음 Task 입력을 연결하고 provenance를 확인할 수 있다.

---

### S2 — 순차형 Project ✅

**Project:** Market research report  
**Project ID:** `01KZ5YTT7058K0QQY57QV96K86`  
**Project Run ID:** `01KZ5YTT7ZRX7K7WB1W26DBR04`

```text
research → clean → report
```

| 항목 | 값 |
|---|---|
| status | `completed` |
| step 수 | 3 |
| step status | research / clean / report 모두 `completed` |
| 실행된 step Task Run | 3 |
| 외부 입력 | 0 |

**판정:** 통과. 순차 의존성과 앞 단계 결과 → 다음 단계 연결이 정상이다.

---

### S3 — 병렬 분기 후 통합 ✅

**Project:** Product launch review  
**Project ID:** `01KZ5YTVV6PRTRG1JP4JYQJ4QZ`  
**Project Run ID:** `01KZ5YTVVW1V266VHA42WJ5T0T`

```text
market ─┐
        ├─ synthesis
risk   ─┘
```

| 항목 | 값 |
|---|---|
| status | `completed` |
| step 수 | 3 |
| step status | market / risk / synthesis 모두 `completed` |
| 실행된 step Task Run | 3 |

**판정:** 통과. 독립 branch 실행 후 통합 Task가 완료됐다.

---

### S4 — Project 간 Artifact 재사용 ✅

Project A(S2)의 최종 `report` Artifact를 Project C의 외부 입력으로 전달했다.

**Project C:** Research-to-adoption plan  
**Project ID:** `01KZ5YTXDVVRJ8VJTVCRD5X34R`  
**Project Run ID:** `01KZ5YTXEQBFXZNAEJ3VM3J2Z8`

```text
Project A report Artifact
  → Project C: adopt → review
```

| 항목 | 값 |
|---|---|
| 원본 Project Run | `01KZ5YTT7ZRX7K7WB1W26DBR04` |
| 원본 Artifact | `01KZ5YTVR2CH0Q719EWH7XZAVZ` |
| 소비 Project Run | `01KZ5YTXEQBFXZNAEJ3VM3J2Z8` |
| 외부 입력 수 | 1 |
| 원본 Artifact lineage 소비자 수 | 1 |
| 소비 Project Run status | `completed` |

**판정:** 통과. 프로젝트 경계를 넘어 Artifact를 업무 자산으로 재사용했다.

---

### S5 — 실패 영수증과 재실행 ✅

Worker 명령을 존재하지 않는 경로로 바꿔 실패를 유도한 뒤, 명령을 복구하고 같은 Task를 재실행했다.

**실패 Run**

| 항목 | 값 |
|---|---|
| Task Run | `01KZ5YTYH11C624ZJVBY9CB32E` |
| status | `failed` |
| failure_reason | `codex has no capability audit for its installed version. Run relay doctor --worker codex --deep.` |
| result_summary | `null` |
| artifact | 없음 |

**복구 Run**

| 항목 | 값 |
|---|---|
| Task Run | `01KZ5YTYJVR618XXMEBB2TA6ZN` |
| status | `completed` |
| result_summary | `Mock answer from codex` |
| Artifact | `01KZ5YTYZMNKTQW3ZF0T1TTE0G` |

**판정:** 통과. 실패 원인이 영수증에 남고, 환경 복구 후 재실행이 성공한다.

---

### S6 — Catalog-first 탐색 ✅

오케스트레이션 에이전트가 “이미 있는 일/결과”를 찾는 경로를 검증했다.

| 조회 | 결과 |
|---|---:|
| Task Catalog | 11 |
| Task Run Catalog | 12 |
| Project Catalog | 3 |
| Project Run Catalog | 3 |
| Run 검색 (`Mock`) | 11 |
| 실패 Run 검색 (`recovery`, status=failed) | 1 |
| Artifact 검색 (`RELAY_ARTIFACT_OK`) | 11 |
| Artifact content | `available=true`, canonical field `text` |
| 복구 Artifact lineage | artifact 메타 존재 |

**판정:** 통과. Catalog pagination 대상 목록, 요약·상태, 검색, 본문 필드 계약이 Agent 읽기 경로로 사용 가능하다.

---

## 4. 등록된 업무 자산 목록

### Tasks (키 → 이름)

| key | name |
|---|---|
| standalone_source | Collect source material |
| standalone_summary | Summarize source material |
| research | Research inputs |
| clean | Clean research |
| report | Write research report |
| market | Analyze market potential |
| risk | Analyze delivery risk |
| synthesis | Synthesize launch decision |
| adopt | Draft adoption plan |
| review_plan | Review adoption plan |
| failure | Failure recovery probe |

### Projects

| key | name | summary |
|---|---|---|
| sequential | Market research report | Collect, clean, and report market research in sequence. |
| parallel_join | Product launch review | Analyze market and risk in parallel, then synthesize a launch decision. |
| cross_project | Research-to-adoption plan | Reuse a prior Project report as input to a new adoption plan. |

---

## 5. 제품 관점 해석

이번 검증이 보여 주는 것은 “mock이 답을 잘한다”가 아니라, Relay가 **업무 오케스트레이션 원장**으로 동작한다는 점이다.

1. **정의** — Task / Project를 등록할 수 있다.  
2. **실행** — Task Run / Project Run이 남는다.  
3. **결과물** — Artifact UID가 생긴다.  
4. **연결** — 다음 Task·다른 Project가 그 UID를 입력으로 받는다.  
5. **실패** — 실패도 업무 기록으로 남고 재실행 가능하다.  
6. **탐색** — Catalog와 검색으로 다시 찾을 수 있다.

이는 Product Identity의  
**Design the work. Inspect the checkpoints.**  
와 Catalog 계획의  
**Relay는 목록·요약·ID를 주고, 선택은 Agent가 한다**  
와 일치한다.

---

## 6. 범위와 한계

### 포함한 것

- 임시 Relay Home에서의 실제 Engine/Runtime 경로
- mock Codex deep audit 후 실행
- Artifact lineage / content (`text`) 계약
- Catalog 및 FTS 검색 조회

### 포함하지 않은 것

- 실제 Claude / Codex / Antigravity 추론 품질
- GUI 클릭 경로
- Routine 스케줄 대기(시간 기반)
- 운영 중인 사용자 Relay Home 데이터

### 참고

- v1.0에서는 신규 Run/Artifact 검색 0건이 관찰됐으나, **v1.1 동일 러너 재실행에서는 검색 히트가 정상**이었다.  
  자동 색인 회귀 테스트는 계속 유지하는 것이 안전하다.

---

## 7. 재현 방법

```powershell
cd D:\APPs\Relay-agent
D:\Python314\python.exe tests\test_agent_orchestration_scenarios.py
```

종료 코드 `0`과 JSON 결과의 `doctor_ok: true`, 각 시나리오 필드가 채워지면 동일 검증으로 본다.

---

## 8. 결론

| 시나리오 | 판정 |
|---|---|
| S1 단독 Artifact 체인 | ✅ 통과 |
| S2 순차 Project | ✅ 통과 |
| S3 병렬 통합 Project | ✅ 통과 |
| S4 Project 간 Artifact 재사용 | ✅ 통과 |
| S5 실패 영수증 + 재실행 | ✅ 통과 |
| S6 Catalog / 검색 / 본문 | ✅ 통과 |

**종합: 오케스트레이션 시나리오 검증 통과 (6/6).**  
현재 Relay 1.1.0 코드 경로에서, Agent가 Task·Project를 등록·연결·재사용·탐색하는 업무 루프는 mock Worker 기준으로 성립한다.
