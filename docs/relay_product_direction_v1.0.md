# Relay 제품 방향성 및 개발 로드맵 v1.0

- 작성일: 2026-08-03
- 문서 목적: Relay의 제품 방향, 핵심 용어와 데이터 모델, 현재 구조에서 수정할 사항, 단계별 개발 우선순위를 정리한다.
- 핵심 전환: **Chat-based AI 사용에서 Job-based 업무 운영으로 전환**

---

## 1. 결론

### 1.1 타깃

경계선은 "코딩이냐 아니냐"가 아니다. **에이전트를 다루는 자리냐, 에이전트가 만든 결과물을 자산으로 만드는 자리냐**다.

Relay는 코딩 에이전트의 세션을 관리하는 앱이 아니다. 대화하면서 코드베이스를 파고드는 자리, 즉 에이전트와 함께 앉아 있는 자리는 Claude Code와 Codex CLI가 이미 잘 차지하고 있고, Relay가 그 자리를 빼앗을 이유가 없다.

Relay가 겨냥하는 것은 **업무를 맡기고, 그 결과물을 남기는 자리**다. 조사·분석·보고서·데이터 정리·시장 모니터링·문서 초안 같은 지식 작업이 대표적이지만, 코딩 작업도 그대로 포함된다. 실제로 "이 파일을 이런 관점에서 검토해 수정해 둬", "계산기 py 파일을 만들어 둬" 같은 요청은 지금의 Relay에서도 잘 수행된다. 중요한 것은 작업의 종류가 아니라, 그 결과가 채팅 로그 안에서 사라지느냐 아니면 검색·재사용·연결 가능한 결과물로 남느냐다.

첫 사용자는 Claude Code/Codex CLI를 이미 설치했거나 설치할 의사가 있는 **파워유저·지식 작업자**다. 개발자일 필요는 없고, Agent CLI 개념을 받아들일 수 있으면 충분하다. 개발자라면 코딩 업무를 맡기는 자리로 쓰면 된다. 둘을 나눌 이유는 없다.

### 1.2 해결하는 문제

> 현재 AI가 수행한 업무와 결과물은 대부분 채팅 안에 묻힌다. 반복 업무도 매번 다시 지시해야 하며, 이전 결과물을 검색하거나 다음 업무의 입력으로 안정적으로 넘기기 어렵다.

채팅은 탐색과 일회성 질문에 적합하지만, 반복되는 업무를 운영하는 시스템으로는 부족하다.

### 1.3 제품 정의

Relay는 이러한 AI 업무를 채팅에서 분리하여 다음과 같은 정식 업무 객체로 관리한다.

1. 반복 가능하게 정의된 업무
2. 매 실행의 독립적인 기록
3. 검증되고 재사용 가능한 결과물
4. 여러 업무 간 결과물 전달 관계
5. 정해진 주기에 따른 자동 실행

> **Relay는 반복되는 AI 업무를 Task로 정의하고, 실행 기록과 결과물을 축적하며, 여러 Task를 Project로 연결해 자동 수행하는 로컬 업무 시스템이다.**

영문 제품 정의:

> **Relay turns recurring AI work into reusable tasks, durable runs, and connected projects.**

보다 기능적으로 표현하면:

> **Relay is a local system for running structured AI work, preserving its artifacts, and passing them into the next task.**

### 1.4 두 층이 같이 움직여야 한다

Relay는 두 개의 층으로 이루어진다. 둘 중 어느 하나도 부속품이 아니다.

```text
업무 층 (Work layer)
  Task · Run · Artifact · Project · Routine · lineage · 검색
    ↑
  이 층이 "AI 업무를 자산으로" 만든다.
  현재 코드에는 존재하지 않는다. 새로 만든다.
    │
    │  신뢰할 수 있는 실행 위에서만 자산이 의미를 갖는다.
    │
실행 층 (Execution layer)
  Engine · Adapter · Supervisor · Safe delivery · Audit · Fallback
    ↑
  이 층이 "결과를 믿을 수 있게" 만든다.
  현재 코드에 이미 있고 검증됐다. 남들이 갖지 못한 부분이다.
```

실행 층은 "그냥 되는 것"이 아니다. Safe working-folder delivery(격리 사본에서 작업 → delta 검증 → 실제 폴더에 반영), unattended recovery(timeout·stall·prompt 감지), capability audit(실행 전 검증)이 현재 Relay가 실제로 가진 차별점이다. 업무 층의 비전은 이 실행 층 위에 올라야 하며, 그래야 "또 다른 워크플로 도구"와 구분된다.

핵심 철학은 다음과 같다.

```text
Chat-based
질문 → 답변 → 채팅 속에 묻힘

Work-based
업무 정의 → 실행 → 결과물 → 기록 → 검색 → 재사용 → 후속 업무
```

---

## 2. Relay가 필요하다고 판단한 이유

### 2.1 현재 Chat 기반 AI 업무의 한계

현재 대부분의 AI 업무는 채팅을 중심으로 이루어진다.

- 요청과 결과가 긴 대화 중간에 섞인다.
- 어느 답변이 최종 결과인지 명확하지 않다.
- 동일한 업무를 다시 실행하려면 프롬프트와 자료를 다시 구성해야 한다.
- 과거 결과를 날짜, 업무, 결과물 종류에 따라 검색하기 어렵다.
- 이전 결과를 다른 Agent나 업무가 안정적으로 참조하기 어렵다.
- 실패, 재시도, Agent 변경 내역이 업무 단위로 관리되지 않는다.
- 매주 또는 매월 실행되는 업무라도 실행 간 비교와 누적이 어렵다.
- 결과가 메시지로 끝나며 재사용 가능한 업무 자산으로 남지 않는다.

채팅은 탐색적 사고와 일회성 질의에는 적합하지만, 반복적이고 정형화된 업무를 운영하는 시스템으로는 부족하다.

### 2.2 Relay가 제공해야 하는 변화

Relay는 AI가 생성한 답변을 단순 메시지가 아니라 **업무 수행 결과**로 다룬다.

```text
Task 정의
   ↓
Task Run 생성
   ↓
하나 이상의 Attempt 수행
   ↓
Artifact 생성 및 검증
   ↓
검색 가능한 기록으로 보존
   ↓
다른 Task 또는 Project에서 재사용
```

따라서 Relay의 가치는 Agent를 실행하는 것 자체보다 다음에 있다.

- 무엇을 수행하도록 요청했는가
- 실제로 어떤 시도가 이루어졌는가
- 어떤 결과물이 생성되었는가
- 결과물이 검증되었는가
- 어느 후속 업무가 그 결과물을 사용했는가
- 반복 실행을 통해 결과가 어떻게 변했는가

---

## 3. 제품 범위

### 3.1 Relay가 지향하는 것

- 반복 가능한 AI 업무 정의
- 사람, Agent, 서비스, Project, 예약 실행의 요청을 동일한 Run 모델로 관리
- 장시간 실행과 실패 복구
- 등록·활성화되고 현재 정의에 맞는 health check를 통과한 Worker를 통한 실행
- 결과물 검증 및 보존
- 과거 실행과 결과물 검색
- 이전 결과물을 다음 업무의 입력으로 전달
- 여러 Task를 연결한 Project 실행
- Task와 Project의 주기적 실행
- 실행 이력, Attempt, Artifact lineage 관리

### 3.2 Relay가 중심 제품으로 지향하지 않는 것

- 코딩 Agent 전용 세션 관리자
- tmux 기반 Agent 화면 관리 도구
- 범용 채팅 클라이언트
- Agent의 추론 방식을 직접 설계하는 swarm 프레임워크
- 처음부터 모든 조건 분기와 루프를 제공하는 범용 워크플로 빌더
- 클라우드 팀 협업 플랫폼
- Agent 간 통신 프로토콜 자체의 재구현

Claude Code, Codex, Hermes, OpenClaw, ACP Agent 등 특정 제품명은 Relay의 제품 정체성이나 고정 역할을 정의하지 않는다. 통합 방식에 따라 같은 시스템이 어떤 Run에서는 Relay를 호출하는 Caller가 되고, 다른 Run에서는 Task를 수행하는 Worker가 될 수 있다.

### 3.3 실행 참여자와 역할

Relay는 제품 이름이 아니라 **각 Run에서 맡은 역할**을 기준으로 실행 참여자를 구분한다.

| 역할 | 정의 |
|---|---|
| **Caller** | Relay에 Task Run 또는 Project Run을 요청한 주체. 사람, Agent, 서비스 또는 자동화 시스템이 될 수 있다. |
| **Worker** | Attempt를 실제로 수행하는 실행 백엔드. Relay에 등록·활성화되고 현재 실행 정의와 실행 파일에 맞는 health check를 통과해야 한다. |
| **Human Operator** | GUI 또는 CLI에서 Run을 관찰하고, 중단·재실행·승인·결과 검토를 수행하는 사람. Caller와 동일인일 수도 있고 별도 운영자일 수도 있다. |

예시:

```text
Caller
├─ 사용자: GUI 또는 CLI에서 직접 실행 요청
├─ Agent: Skill, CLI 또는 API로 실행 요청
└─ 서비스/자동화: API 또는 예약 정책에 따라 실행 요청
       │
       ▼
Relay
├─ Run 생성 및 멱등성 관리
├─ Worker 선택과 Attempt 관리
├─ 실행 감시, 실패 복구, 검증
└─ Artifact와 receipt 보존
       │
       ▼
Worker
├─ Built-in Agent CLI
├─ Custom Agent App
├─ 등록된 Local Agent
└─ 등록된 Script / CLI
```

Caller와 Worker는 제품별 고정 분류가 아니다. 예를 들어 어떤 Agent가 Relay API로 조사 Task를 요청하면 Caller이고, 같은 Agent가 Agent App으로 등록되어 Attempt를 수행하면 Worker다. 한 Run 안에서는 두 역할과 책임을 명확히 구분한다.

Worker가 실행 대상이 되기 위한 최소 조건은 다음과 같다.

1. Relay registry에 등록되어 있다.
2. 운영자가 명시적으로 활성화했다.
3. 현재 Worker 정의와 실행 파일 버전에 대응하는 health/deep check가 유효하다.
4. 해당 Run의 Worker·보안·경로 정책을 충족한다.

Run의 출처는 다음 세 축을 혼합하지 않고 별도로 기록한다.

```text
caller       = 누가 요청했는가: human | agent | service
trigger      = 무엇이 실행을 촉발했는가: direct | project | routine
submitted_via= 어떤 인터페이스로 들어왔는가: gui | cli | api | skill | internal
```

Project와 Routine은 호출 주체가 아니라 실행 트리거다. 실제 요청 주체의 identity와 trigger 정보를 함께 보존해야 한다.

---

## 4. 확정 용어

### 4.1 사용자에게 노출되는 핵심 용어

| 용어 | 정의 |
|---|---|
| **Task** | 미리 등록해 두고 다시 실행할 수 있는 단일 업무 정의 |
| **Task Run** | Task가 실제로 한 번 실행된 기록 |
| **Attempt** | Task Run을 완료하기 위해 특정 Agent 또는 Worker가 수행한 개별 실행 시도 |
| **Artifact** | Task Run에서 생성되거나 확정된 결과물 |
| **Project** | 여러 Task와 그 사이의 입력·출력 전달 관계를 연결한 업무 흐름 |
| **Project Run** | Project 전체가 실제로 한 번 수행된 기록 |
| **Routine** | Task 또는 Project를 일정에 따라 반복 실행하기 위한 등록 설정 |
| **Caller** | 사람, Agent 또는 서비스 중 Relay에 Run을 요청한 주체 |
| **Worker** | 등록·활성화되고 유효한 health check를 통과하여 Attempt를 수행할 수 있는 실행 백엔드 |

### 4.2 핵심 구분

```text
Task       = 무엇을 수행할 것인가
Task Run   = 그 Task가 실제로 한 번 수행된 기록
Attempt    = Task Run을 성공시키기 위해 이루어진 개별 실행 시도
Artifact   = 실행 결과로 남은 재사용 가능한 결과물
Project    = 여러 Task를 어떤 순서와 입력 관계로 실행할 것인가
Project Run= Project가 실제로 한 번 수행된 기록
Routine    = Task 또는 Project를 언제 반복 실행할 것인가
Caller     = 누가 Relay에 실행을 요청했는가
Worker     = 어느 실행 백엔드가 Attempt를 수행하는가
```

### 4.3 전체 관계

```text
Routine
  └─ Task 또는 Project를 주기적으로 실행

Task
  └─ Task Run
       ├─ Attempt 1
       ├─ Attempt 2
       └─ Artifact

Project
  └─ Project Run
       ├─ Task Run A
       │    ├─ Attempt
       │    └─ Artifact A
       ├─ Task Run B
       │    ├─ Input: Artifact A
       │    ├─ Attempt
       │    └─ Artifact B
       └─ Final Artifacts
```

---

## 5. 각 객체의 상세 정의

## 5.1 Task

Task는 재사용 가능한 단일 업무 정의다.

예시:

- 경쟁사 뉴스 수집
- 반도체 업계 주간 동향 분석
- 실적 발표 자료 분석
- 전일 시장 데이터 정리
- 월간 경영 보고서 초안 작성
- 특정 저장소의 테스트 및 품질 점검

Task는 반드시 예약 실행될 필요는 없다. 다음 경로로 실행할 수 있다.

- 사용자의 수동 실행
- 다른 Agent의 API/CLI 요청
- Project의 한 단계
- Routine의 예약 실행

권장 필드:

```text
Task
├─ task_id
├─ name
├─ description
├─ instructions
├─ input_schema
├─ output_contract
├─ default_worker_policy
├─ retry_and_fallback_policy
├─ validation_policy
├─ version
├─ created_at
└─ updated_at
```

### 일회성 실행 지원

모든 업무를 먼저 Task로 등록하도록 강요해서는 안 된다.

Relay는 다음 두 가지 실행 방식을 지원해야 한다.

1. 저장된 Task 실행
2. 일회성 Quick Run 실행

일회성 실행도 내부적으로 Task Run과 동일한 기록 구조를 사용한다. 다만 저장된 `task_id` 대신 실행 당시의 지시사항과 설정을 `task_snapshot`으로 보존한다.

만족스러운 일회성 실행은 이후 **Save as Task** 기능으로 등록할 수 있어야 한다.

---

## 5.2 Task Run

Task Run은 하나의 논리적 업무 실행 기록이다.

재시도나 Worker 변경이 발생하더라도 Task Run ID는 유지된다.

예시:

```text
Task Run TR-202
목표: 이번 주 경쟁사 동향 보고서 생성

Attempt 1: Worker A → Timeout
Attempt 2: Worker B → Rate limit
Attempt 3: Worker C → Completed

Task Run 최종 상태: Completed
Artifact: competitor_report.md
```

권장 필드:

```text
Task Run
├─ task_run_id
├─ task_id 또는 task_snapshot
├─ task_version
├─ caller_principal_id
├─ caller_type
├─ trigger_type
├─ trigger_id
├─ submitted_via
├─ input_manifest
├─ status
├─ attempts
├─ artifacts
├─ started_at
├─ completed_at
├─ summary
└─ receipt
```

권장 상태:

```text
accepted
queued
running
validating
awaiting_approval
blocked
completed
partial
failed
cancelled
```

실행 출처는 호출 주체, 트리거, 제출 인터페이스로 나누어 기록한다.

```text
caller_type: human | agent | service
trigger_type: direct | project | routine
submitted_via: gui | cli | api | skill | internal
```

`caller_principal_id`는 가능한 경우 인증된 사용자, Agent 또는 서비스 identity를 가리킨다. 자유 입력 문자열만으로 보안 정책을 결정하지 않는다.

---

## 5.3 Attempt

Attempt는 Task Run을 수행하기 위한 실제 실행 시도다.

Attempt가 필요한 이유:

- 같은 Worker를 재시도할 수 있다.
- 다른 Worker로 fallback할 수 있다.
- 인증 오류, timeout, rate limit 등 기술적 실패를 분리할 수 있다.
- Task Run의 논리적 성공 여부와 개별 프로세스 실패를 구분할 수 있다.

권장 필드:

```text
Attempt
├─ attempt_id
├─ task_run_id
├─ worker_id
├─ worker_type
├─ attempt_number
├─ status
├─ started_at
├─ completed_at
├─ exit_code
├─ error_class
├─ stdout_ref
├─ stderr_ref
├─ usage
└─ produced_artifact_ids
```

Attempt 실패 원인은 구조화해야 한다.

```text
authentication_error
rate_limit
worker_unavailable
timeout
stall
process_crash
invalid_output
validation_failed
permission_required
cancelled
unknown
```

---

## 5.4 Artifact

Artifact는 Task Run이 남긴 결과물이다.

Artifact는 파일만을 의미하지 않는다.

- Markdown 보고서
- JSON 데이터
- CSV 또는 스프레드시트
- PDF
- 이미지
- 코드 변경사항
- 구조화된 최종 응답
- 검증 결과
- 실행 요약

Artifact는 가급적 불변 객체로 관리한다. 내용이 바뀌면 기존 Artifact를 덮어쓰지 않고 새로운 Artifact ID를 생성한다.

권장 필드:

```text
Artifact
├─ artifact_id
├─ producer_task_run_id
├─ producer_attempt_id
├─ name
├─ role
├─ media_type
├─ storage_path_or_uri
├─ size
├─ sha256
├─ validation_status
├─ metadata
├─ created_at
└─ lineage
```

`role` 예시:

```text
final_report
raw_data
source_list
chart
structured_result
execution_log
supporting_document
```

Artifact는 다음 질문에 답할 수 있어야 한다.

- 어느 Task Run에서 생성됐는가
- 어느 Attempt가 만들었는가
- 검증됐는가
- 어떤 Task Run들이 이 Artifact를 입력으로 사용했는가
- 이 Artifact로부터 어떤 후속 Artifact가 생성됐는가

---

## 5.5 Project

Project는 여러 Task를 연결한 업무 흐름이다.

예시:

```text
Project: 주간 반도체 보고서

Task A: 뉴스 및 공시 수집
Task B: 기업별 영향 분석
Task C: 차트 생성
Task D: 최종 보고서 작성
```

Project는 단순한 Task 목록이 아니다. 각 Task의 어떤 출력이 다음 Task의 어떤 입력으로 연결되는지를 정의해야 한다.

```text
Task A.news_json      → Task B.news_input
Task A.source_list    → Task D.sources
Task B.analysis       → Task D.analysis_input
Task C.charts         → Task D.chart_input
```

권장 필드:

```text
Project
├─ project_id
├─ name
├─ description
├─ version
├─ task_nodes
├─ connections
├─ input_bindings
├─ output_selection
├─ failure_policy
├─ approval_points
└─ created_at / updated_at
```

초기에는 복잡한 범용 DAG보다 다음을 우선 지원한다.

1. 순차 실행: A → B → C
2. 단순 병렬: A → B와 C
3. 결과 합류: B와 C → D
4. 단계별 실패 정책
5. 특정 단계 재실행

---

## 5.6 Project Run

Project Run은 Project 전체가 한 번 실행된 기록이다.

```text
Project Run PR-24
├─ Task Run TR-101: 자료 수집
├─ Task Run TR-102: 기업 분석
├─ Task Run TR-103: 차트 생성
└─ Task Run TR-104: 최종 보고서
```

권장 필드:

```text
Project Run
├─ project_run_id
├─ project_id
├─ project_version
├─ trigger_type
├─ trigger_id
├─ task_run_ids
├─ resolved_connections
├─ status
├─ started_at
├─ completed_at
├─ final_artifact_ids
├─ warnings
└─ receipt
```

Project Run이 끝나면 통합 실행 보고를 생성해야 한다.

```text
Project Run Status: Completed

Steps
✓ 자료 수집
✓ 기업 분석
✓ 차트 생성
✓ 최종 보고서

Final Artifacts
- weekly_report.pdf
- supporting_data.xlsx

Warnings
- 기업 분석 단계에서 fallback Worker 사용

Execution Summary
- 4 Task Runs
- 5 Attempts
- 8 Artifacts
```

---

## 5.7 Routine

Routine은 Task 또는 Project를 주기적으로 실행하기 위한 등록 설정이다.

> **Routine은 업무 정의가 아니라 반복 실행 설정이다.**

```text
Task 또는 Project = 무엇을 수행할 것인가
Routine           = 언제, 어떤 조건으로 반복 실행할 것인가
```

예시:

```text
Routine: 매주 월요일 반도체 보고서
├─ target_type: project
├─ target_id: weekly-semiconductor-report
├─ schedule: 매주 월요일 08:00
├─ timezone: Asia/Seoul
├─ input_policy: 최근 성공 결과 사용
├─ overlap_policy: 이전 실행 중이면 건너뜀
├─ failure_policy: 2회 재시도 후 알림
└─ enabled: true
```

하나의 Task 또는 Project에 여러 Routine을 연결할 수 있다.

Routine 실행 시 별도의 사용자용 `Routine Run`을 만들 필요는 없다.

- Task 대상 Routine → Task Run 생성
- Project 대상 Routine → Project Run 생성

생성된 Run에 `trigger_type=routine`과 `routine_id`를 기록하면 된다.

권장 필드:

```text
Routine
├─ routine_id
├─ name
├─ target_type: task | project
├─ target_id
├─ target_version_policy
├─ schedule
├─ timezone
├─ input_bindings
├─ overlap_policy
├─ missed_run_policy
├─ notification_policy
├─ enabled
└─ last_run / next_run
```

---

## 6. Relay의 핵심 사용자 흐름

## 6.1 일회성 작업을 Task로 승격

```text
New Quick Run
   ↓
Task Run 실행
   ↓
결과 확인
   ↓
Save as Task
   ↓
입력, 출력, Worker 정책을 정리해 재사용 가능하게 등록
```

사용자가 처음부터 완전한 업무 정의를 작성하도록 요구하지 않는다. 먼저 실행한 뒤 만족스러운 업무를 Task로 저장하는 흐름이 중요하다.

## 6.2 과거 결과를 찾아 새 작업에 사용

```text
과거 Task Run 검색
   ↓
후보 Run의 요약 확인
   ↓
Artifact 목록 조회
   ↓
필요한 Artifact 선택
   ↓
A1, A2 등 입력 별칭 지정
   ↓
새 Task Run 실행
```

## 6.3 여러 Task를 Project로 연결

```text
Task A 등록
Task B 등록
Task C 등록
   ↓
Project 생성
   ↓
A의 출력 → B의 입력 연결
B의 출력 → C의 입력 연결
   ↓
Project Run 실행
   ↓
통합 결과 보고
```

## 6.4 Task 또는 Project를 Routine으로 등록

```text
Task 또는 Project 선택
   ↓
Schedule, timezone, input policy 설정
   ↓
Routine 활성화
   ↓
예약 시점마다 Task Run 또는 Project Run 생성
```

---

## 7. 최우선 기능: 과거 기록 검색

Relay가 단순 실행기가 아닌 업무 기록 시스템이 되려면 과거 기록을 사람뿐 아니라 Agent가 직접 검색할 수 있어야 한다.

> **검색은 색인 대상을 전제한다.** Artifact가 role·producer·lineage를 갖는 불변 객체로 먼저 정립되어야(8장) 검색 결과가 의미를 갖는다. 그래서 구현 순서는 lineage 모델(8장) → 검색(7장)이지, 반대가 아니다. 12장 Phase 순서도 이 의존성을 따른다.

### 7.1 검색 대상 우선순위

Agent 검색은 다음 순서로 정보를 제공하는 것이 좋다.

1. Task Run과 Project Run의 최종 요약
2. Artifact 이름, 역할, 메타데이터
3. Artifact 본문 또는 파일
4. 입력 manifest와 receipt
5. 필요할 때만 Attempt 로그, stdout, stderr

Raw log를 기본 검색 결과로 제공하면 Agent context가 다시 불필요하게 오염된다.

### 7.2 Agent용 필수 함수

초기 필수 함수:

```text
search_task_runs
get_task_run
search_project_runs
get_project_run
list_run_artifacts
search_artifacts
get_artifact_manifest
read_artifact
trace_artifact_lineage
```

보조 함수:

```text
list_tasks
get_task
list_projects
get_project
list_routines
get_latest_successful_run
compare_task_runs
```

예시:

```text
search_task_runs(
  query="지난 3개월 HBM 공급 전망",
  task_id="semiconductor-weekly",
  status="completed",
  date_from="2026-05-01",
  limit=10
)
```

검색 결과는 본문 전체가 아니라 후보를 고를 수 있는 요약을 반환한다.

```json
{
  "task_run_id": "tr-104",
  "task_id": "semiconductor-weekly",
  "executed_at": "2026-07-27T08:00:00+09:00",
  "summary": "HBM 공급 부족과 가격 상승이 주요 이슈",
  "artifact_count": 4,
  "artifact_roles": ["final_report", "raw_data", "source_list"],
  "relevance": 0.91
}
```

### 7.3 2단계 검색 원칙

```text
1단계: 검색 결과의 ID, 제목, 요약, 날짜만 조회
2단계: 필요한 Run 또는 Artifact만 명시적으로 열람
```

Agent가 검색 결과 전체를 무작정 context에 넣지 않도록 Skill 문서에 이 절차를 명확히 적어야 한다.

### 7.4 검색 기술 우선순위

초기:

- SQLite FTS5
- Task, Project, 날짜, 상태, Worker, 태그 필터
- Run summary와 Artifact 텍스트 색인
- 파일명 및 Artifact role 검색

후속:

- 의미 기반 검색
- Embedding 색인
- 유사 Run 추천
- 이전 결과 대비 변화 검색

처음부터 Vector DB를 핵심 의존성으로 넣지 않는다.

### 7.5 SKILL.md 강화

도구 함수만 추가해서는 실제 Agent가 잘 사용하지 않는다. Skill 문서에 다음을 명시한다.

- 과거에 유사한 업무가 수행됐을 가능성이 있으면 먼저 검색할 것
- 검색 후 후보 요약만 확인할 것
- 필요한 Artifact만 선택적으로 읽을 것
- 최신 성공 Run과 가장 최근 Run을 구분할 것
- 실패 Run보다 완료 Run을 우선할 것
- 결과를 사용했으면 Task Run ID와 Artifact ID를 남길 것
- 파일 전체를 context에 붙이지 말고 경로나 참조를 Worker에게 전달할 것
- 이전 결과를 수정하는 경우 원본 Artifact를 덮어쓰지 말 것

---

## 8. 최우선 기능: 이전 결과물 이어받기 (Artifact lineage)

사용자는 특정 Task Run의 결과물 중 필요한 것을 골라 새 Task Run의 입력으로 사용할 수 있어야 한다.

이 기능은 검색(7장)보다 **먼저** 구현되어야 한다. 검색은 색인 대상이 있어야 성립하는데, 그 대상은 Artifact가 producer·role·sha256을 갖는 불변 객체가 되고 그 사이의 lineage가 기록될 때 비로소 생긴다. lineage 모델이 먼저 세워지면 검색은 그 위에 자연스럽게 올라간다.

### 8.1 Task Run ID만 전달해서는 부족함

하나의 Task Run에는 여러 Artifact가 존재할 수 있다.

```text
Task Run TR-104
├─ report.md
├─ raw_data.csv
├─ chart.png
└─ sources.json
```

따라서 다음 절차가 필요하다.

1. Task Run 선택
2. 해당 Run의 Artifact 목록 조회
3. 개별 Artifact 또는 Artifact 묶음 선택
4. 입력 별칭 지정
5. 새 Task Run의 input manifest에 고정

### 8.2 A1, A2 별칭

사용자와 Agent가 결과물을 쉽게 지칭하도록 입력 별칭을 제공한다.

```text
A1 = TR-104의 report.md
A2 = TR-104의 raw_data.csv

Prompt:
A1의 분석을 참고하고 A2의 데이터를 사용해 업데이트 보고서를 작성하라.
```

A1은 실제 데이터베이스 ID를 대체하는 전역 ID가 아니다. 각 Task Run의 입력 context에서 사용하는 읽기 쉬운 별칭이다.

### 8.3 Input Manifest

새 Task Run이 어떤 결과물을 사용했는지 불변 기록으로 남겨야 한다.

```json
{
  "inputs": [
    {
      "alias": "A1",
      "source_task_run_id": "tr-104",
      "source_artifact_id": "artifact-301",
      "name": "report.md",
      "sha256": "...",
      "binding_mode": "snapshot"
    }
  ]
}
```

### 8.4 Snapshot을 기본값으로 권장

입력 전달 방식은 다음 두 가지를 고려할 수 있다.

- Reference: 원본 Artifact를 직접 참조
- Snapshot: 실행 시점의 내용과 hash를 입력으로 고정

Project와 반복 업무의 재현성을 위해 Snapshot을 기본값으로 권장한다. 원본 Artifact가 변경되거나 저장 위치가 바뀌어도 해당 Task Run의 입력은 변하지 않아야 한다.

### 8.5 Artifact lineage

다음 관계를 조회할 수 있어야 한다.

```text
Artifact A
├─ Task Run B의 입력으로 사용
├─ Task Run C의 입력으로 사용
└─ Artifact D 생성의 원천
```

Lineage는 다음 기능의 기반이 된다.

- 과거 결과의 재사용 이력
- 잘못된 원본을 사용한 후속 결과 식별
- Project Run 재현
- 특정 Artifact 변경 시 영향 범위 확인

---

## 9. 최우선 기능 3: Project

Project는 미리 등록된 Task를 연결하고, 이전 단계의 Artifact를 다음 단계로 전달하여 전체 업무를 수행한다.

### 9.1 Project는 과거 Run을 연결하지 않음

Project 정의에는 과거 Task Run ID가 아니라 Task를 연결한다.

```text
잘못된 구조
TR-101 → TR-145 → TR-202

권장 구조
Task A → Task B → Task C
```

Project가 실행될 때 각 Task에 대한 새로운 Task Run이 생성된다.

### 9.2 Project MVP 범위

초기 버전은 다음 기능에 집중한다.

- 순차 실행
- 단순 병렬 실행
- 결과 합류
- Artifact-to-input 매핑
- 단계별 상태 표시
- 실패 시 중단
- 실패 단계만 재실행
- 기술적 실패 시 Attempt 재시도 또는 Worker fallback
- 최종 Artifact 선택
- Project Run receipt

초기에는 다음을 미룬다.

- 복잡한 조건식 언어
- 반복 루프
- 동적 노드 생성
- 범용 스크립팅 엔진
- 대규모 시각적 DAG 편집 기능

### 9.3 실패와 재개

예시:

```text
Task A: 성공
Task B: 성공
Task C: 실패
```

사용자는 다음 중 하나를 선택할 수 있어야 한다.

- Task C만 동일 입력으로 재실행
- Task C를 다른 Worker로 재실행
- Task B부터 다시 실행
- Project 전체 재실행

재실행 시 이전 단계에서 어떤 Artifact snapshot을 사용했는지 명확히 기록한다.

### 9.4 Human Checkpoint

Project는 모든 단계를 무인으로 수행하는 것만 목표로 하지 않는다.

```text
자료 수집
   ↓
초안 분석
   ↓
사람 승인 또는 수정
   ↓
최종 보고서
   ↓
외부 전달
```

권장 상태:

```text
awaiting_approval
needs_input
blocked
```

사람이 수정한 내용도 새로운 Artifact로 기록하여 lineage에 포함해야 한다.

---

## 10. Routine과 기존 Schedule 기능의 전환

현재 Schedule이 단순 Job 실행 예약에 가깝다면 이를 Routine 개념으로 승격한다.

### 10.1 변경 방향

```text
기존
Schedule → 특정 Job 파라미터 실행

변경
Routine → Task 또는 Project를 반복 실행
```

### 10.2 필요한 정책

- Timezone
- 중복 실행 방지
- 이전 실행이 끝나지 않았을 때의 정책
- 누락된 실행 처리
- target의 최신 버전 사용 또는 특정 버전 고정
- 입력값 자동 선택 정책
- 실패 알림
- 성공 결과 전달 대상

권장 overlap policy:

```text
skip
queue
cancel_previous
allow_parallel
```

권장 missed-run policy:

```text
skip
run_once_on_recovery
replay_all
```

---

## 11. 현재 구조에서 수정해야 할 사항

## 11.1 제품 설명과 README

현재 제품이 Agent CLI 실행기 또는 코딩 Agent 위임 도구로 보인다면 다음 방향으로 수정한다.

기존 중심 표현:

- 여러 Agent CLI에 작업을 위임
- 장시간 Agent 실행
- 기술적 실패 시 fallback

새로운 중심 표현:

- 반복되는 AI 업무를 Task로 저장
- 실행 결과를 Task Run과 Artifact로 축적
- 과거 결과를 검색하고 재사용
- 여러 Task를 Project로 연결
- Task 또는 Project를 Routine으로 반복 실행

Agent CLI, fallback, daemon, GUI는 부속품이 아니라 "결과를 믿을 수 있게" 만드는 신뢰의 기반이다. Safe working-folder delivery(격리 복사본에서 작업 → delta 검증 → 실제 폴더에 반영), unattended recovery, capability audit은 현재 Relay가 이미 가진 차별점이다. 새 중심 메시지 위에 반드시 같이 보인다.

## 11.2 사용자 화면의 Job 용어

현재 `Job`이 사용자 화면 전체에서 사용된다면 점진적으로 다음과 같이 전환한다.

| 기존 표현 | 권장 표현 |
|---|---|
| New Job | New Run 또는 Run Task |
| Completed Jobs | Runs |
| Job Detail | Task Run Detail |
| Job Attempts | Attempts |
| Job Files | Artifacts |
| Cron / Schedule | Routines |
| Add from Job ID | Add from Task Run |

단, 일회성 요청은 `Quick Run`으로 제공한다.

## 11.3 내부 Job 모델의 마이그레이션

기존 `Job` 테이블과 API를 즉시 삭제하거나 대규모 rename하는 것은 위험하다.

권장 전환 방식:

1. 기존 Job ID를 계속 유효하게 유지한다.
2. 도메인 계층에서 기존 Job을 Task Run으로 해석한다.
3. 새 API와 GUI는 Task/Task Run 용어를 사용한다.
4. 기존 Job API는 일정 기간 compatibility alias로 유지한다.
5. 기존 완료 Job은 Task가 없는 ad-hoc Task Run으로 마이그레이션한다.
6. 기존 Schedule은 Routine으로 변환한다.

예시:

```text
Legacy Job ID: job-104
New canonical reference: task-run-104
Alias lookup: job-104도 계속 허용
```

DB 테이블명을 즉시 변경하기보다 서비스와 API 모델부터 전환한 뒤 안정화 후 물리 스키마를 정리한다.

## 11.4 GUI 정보구조

권장 좌측 메뉴:

```text
Dashboard
Tasks
Projects
Routines
Runs
Artifacts
Needs Attention
Settings
```

Dashboard:

- 실행 중인 Task Run 및 Project Run
- 승인 또는 입력이 필요한 항목
- 최근 완료 결과
- 실패한 Run
- 다음 Routine 실행

Task 상세:

```text
Overview
Runs
Artifacts
Used in Projects
Routines
Versions
Settings
```

Project 상세:

```text
Flow
Runs
Final Artifacts
Routines
Versions
Settings
```

Routine 상세:

```text
Target
Schedule
Inputs
Execution Policy
Run History
Notifications
Settings
```

Run 상세:

```text
Summary
Inputs
Attempts
Artifacts
Receipt
Lineage
Logs
```

## 11.5 Agent용 API와 CLI

Agent는 GUI를 사용하지 않으므로 다음 API/CLI가 제품의 1급 인터페이스여야 한다.

```text
relay task list
relay task run <task-id>
relay run search
relay run get <task-run-id>
relay artifact list <task-run-id>
relay artifact read <artifact-id>
relay project run <project-id>
relay routine list
```

Agent tool 함수와 CLI의 개념 및 결과 schema를 최대한 일치시킨다.

## 11.6 Skill 문서

공용 SKILL.md는 다음 시나리오를 실제 예시와 함께 포함해야 한다.

1. 새 일회성 Task Run 제출
2. 저장된 Task 실행
3. 비동기 진행 조회
4. 과거 Task Run 검색
5. 특정 Artifact 선택 및 읽기
6. Artifact를 A1/A2로 새 Run에 전달
7. Project 실행
8. 실패 Run의 remediation 확인
9. Needs Human 처리
10. 최종 receipt 확인

Skill 문서는 기능 목록이 아니라 Agent의 의사결정 지침이어야 한다.

---

## 12. 개발 우선순위

## Phase 0. 도메인 모델 확정 및 호환 계층

### 목표

기존 기능을 깨뜨리지 않고 새로운 용어와 데이터 구조의 기반을 마련한다.

### 개발 항목

- Task, Task Run, Attempt, Artifact, Project, Project Run, Routine 정의 확정
- Caller, Worker, Human Operator 역할과 책임 정의 확정
- caller identity, trigger, submitted-via 분리 모델 확정
- Worker 등록·활성화·health check 자격 조건 확정
- 기존 Job → Task Run 매핑 정책
- 기존 Schedule → Routine 매핑 정책
- 상태 값과 ID 규칙 확정
- Run trigger 모델 추가
- Task snapshot 구조 추가
- 새 API version 또는 compatibility alias 설계
- 데이터 마이그레이션 계획 및 테스트

### 완료 기준

- 기존 Job이 새 Task Run API로 조회된다.
- 기존 Job ID로도 조회할 수 있다.
- 새 실행은 trigger와 task snapshot을 보존한다.
- 새 실행은 Caller identity, trigger, 제출 인터페이스를 구분해 보존한다.
- 각 Attempt는 실행 당시 자격 조건을 충족한 Worker를 참조한다.
- Attempt와 Artifact가 Task Run 하위 객체로 일관되게 표현된다.

---

## Phase 1. Artifact 선택, 전달, lineage

### 목표

이전 실행 결과를 새로운 Task Run의 입력으로 안전하게 사용한다.

lineage를 검색보다 먼저 세운다. 검색은 색인 대상이 있어야 성립하는데, 그 대상은 Artifact가 producer·role·sha256을 갖는 불변 객체가 되고 그 사이의 lineage가 기록될 때 비로소 생긴다.

### 개발 항목

- Artifact immutable ID와 hash
- Artifact manifest 표준화
- Task Run별 Artifact 목록
- 개별 Artifact 및 묶음 선택
- A1/A2 입력 별칭
- Input manifest
- Snapshot 전달
- source Task Run과 consumer Task Run 관계
- Lineage 조회 API와 GUI
- 현재 `Add from Job ID` 기능을 Task Run/Artifact 선택 방식으로 확장
- 입력 파일 경로와 크기 검증

### 완료 기준

사용자와 Agent가 다음을 할 수 있어야 한다.

> Task Run TR-104의 `report.md`와 `raw_data.csv`만 선택하여 각각 A1과 A2로 새 Task Run에 전달하고, 새 결과에서 원본 lineage를 확인한다.

---

## Phase 2. 과거 기록 검색과 Agent 도구

### 목표

사람과 Agent가 과거 실행 및 결과물을 실제로 찾아 사용할 수 있게 한다.

### 개발 항목

- Task Run 및 Project Run 검색 API
- Artifact 검색 API
- SQLite FTS5 색인
- 날짜, 상태, Task, Project, Worker, Artifact role 필터
- Run summary 생성 및 색인
- 2단계 조회 구조
- Agent tool 함수
- CLI 검색 명령
- SKILL.md 검색 사용 지침과 예시
- 검색 결과 pagination 및 context 크기 제한

### 완료 기준

Agent가 다음 업무를 수행할 수 있어야 한다.

> 최근 3개월간 실행된 반도체 관련 Task Run을 검색하고, 가장 최근의 성공 Run에서 최종 보고서 Artifact만 선택해 가져온다.

---

## Phase 3. Task 등록과 Task Run 분리

### 목표

일회성 실행 기록과 반복 가능한 업무 정의를 분리한다.

### 개발 항목

- Task CRUD
- Task versioning
- input schema
- output contract
- Worker 및 fallback policy
- validation policy
- Task 실행 시 immutable task snapshot 저장
- Quick Run
- Save as Task
- 과거 ad-hoc Job을 Task 없는 Task Run으로 표시
- Task별 Run history와 Artifact 목록

### 완료 기준

- 동일 Task를 여러 번 실행하고 실행 결과를 비교할 수 있다.
- Task 정의를 수정해도 과거 Task Run의 당시 버전과 설정이 보존된다.
- 일회성 실행을 Task로 저장할 수 있다.

---

## Phase 4. Project MVP

### 목표

여러 Task를 연결하여 하나의 업무 흐름으로 실행한다.

### 개발 항목

- Project CRUD 및 versioning
- Task node 등록
- 순차 실행
- 단순 병렬과 합류
- Artifact-to-input mapping
- Project Run 생성
- 자식 Task Run 연결
- 실패 정책
- 실패 단계 재실행
- Project final artifacts
- Project receipt
- 간단한 Flow GUI

### 완료 기준

다음 흐름을 등록하고 실행할 수 있어야 한다.

```text
시장 자료 수집
   ↓
기업별 분석 ─┐
차트 생성   ─┼→ 최종 보고서 작성
```

각 연결에서 어떤 Artifact가 다음 Task의 어떤 입력으로 사용됐는지 조회할 수 있어야 한다.

---

## Phase 5. Routine 통합

### 목표

Task와 Project의 반복 실행을 하나의 Routine 모델로 관리한다.

### 개발 항목

- 기존 Schedule/Cron을 Routine으로 전환
- Task와 Project target 지원
- timezone
- version policy
- input policy
- overlap policy
- missed-run policy
- Routine 실행 이력
- 알림 정책
- GUI Routine 관리
- Agent용 Routine API/CLI

### 완료 기준

- 하나의 Task 또는 Project에 여러 Routine을 연결할 수 있다.
- Routine 실행은 일반 Task Run 또는 Project Run을 생성한다.
- 수동 실행과 Routine 실행의 결과 구조가 동일하다.

---

## Phase 6. 운영성과 품질 강화

### 목표

반복 업무를 장기간 안정적으로 운영할 수 있도록 한다.

### 개발 항목

- Human checkpoint
- 승인 후 외부 폴더 또는 시스템에 전달
- Run 간 비교
- Artifact diff
- 부분 재실행
- 의미 기반 검색
- 결과 품질 평가
- 알림 및 Needs Attention Inbox
- Routine과 Project 운영 대시보드
- export/import 및 backup
- receipt schema versioning

---

## 13. 우선순위 요약

| 우선순위 | 기능 | 이유 |
|---:|---|---|
| P0 | 용어·도메인 모델과 호환 계층 | 이후 기능이 동일한 개념 위에 쌓이도록 하기 위해 필요 |
| P1 | Artifact 선택 전달과 lineage | 검색·Project·재현성 모두가 의존하는 기반. producer·role·sha256·input manifest를 먼저 세운다 |
| P1 | Task와 Task Run 분리 | 반복 가능한 업무 정의와 실행 기록을 구분하기 위한 핵심 구조 |
| P2 | 과거 Run·Artifact 검색 및 Agent 함수 | lineage 모델 위에 색인을 올린다. 대상이 먼저 있어야 검색이 성립한다 |
| P2 | Project MVP | 여러 Task를 연결하여 실제 업무 자동화를 구현 |
| P2 | Routine 통합 | Task와 Project를 정기적으로 운영하기 위한 실행 계층 |
| P3 | Human checkpoint, 비교, 의미 검색 | 운영 편의와 품질을 높이는 후속 기능 |
| 보류 | 범용 DAG, swarm, 코딩 전용 고급 기능 | 핵심 제품 방향을 흐리고 개발 범위를 과도하게 넓힐 수 있음 |

실제 구현 순서는 다음을 권장한다.

```text
도메인 기반 정리
   ↓
재사용 가능한 Artifact (불변 + lineage + input manifest)
   ↓
Task 정의와 Task Run 분리
   ↓
검색 가능한 과거 기록
   ↓
연결 가능한 Project
   ↓
반복 실행 Routine
   ↓
운영·승인·비교 고도화
```

---

## 14. 제품 설계 원칙

### 원칙 1. 채팅보다 업무 객체가 중심이다

채팅 내용은 입력이나 보조 로그일 수 있지만 제품의 핵심 객체가 되어서는 안 된다.

### 원칙 2. 모든 실행은 Run으로 남는다

사람, Agent 또는 서비스 중 누가 요청했는지와 direct, Project 또는 Routine 중 무엇이 실행을 촉발했는지에 관계없이 동일한 Task Run 또는 Project Run 모델을 사용한다. Caller, trigger, 제출 인터페이스는 각각 별도로 기록한다.

### 원칙 3. Run과 Attempt를 분리한다

하나의 Task Run은 여러 Attempt를 포함할 수 있다. 재시도와 Worker fallback이 있어도 논리적 Run은 유지한다.

### 원칙 4. 결과는 Artifact로 남는다

최종 답변을 메시지로만 보존하지 않는다. 결과물에 ID, 역할, hash, 생산 Run, 검증 상태를 부여한다.

### 원칙 5. 결과 전달 관계를 기록한다

어떤 Artifact가 어느 Task Run의 입력이 되었는지 항상 추적 가능해야 한다.

### 원칙 6. Agent의 context를 불필요하게 확대하지 않는다

검색은 요약과 ID를 먼저 제공하고 필요한 결과만 선택적으로 읽게 한다.

### 원칙 7. 실행 당시 상태를 재현할 수 있어야 한다

Task version, Project version, input snapshot, Worker, Attempt, Artifact hash를 보존한다.

### 원칙 8. 사람과 Agent가 같은 업무 모델을 사용한다

GUI, CLI, Agent tool 함수는 동일한 Task, Run, Artifact 개념과 schema를 사용한다.

### 원칙 9. 자동화는 점진적으로 구성한다

일회성 실행 → Task 저장 → Project 연결 → Routine 등록의 순서로 자연스럽게 확장할 수 있어야 한다.

### 원칙 10. 실행 기술은 교체 가능해야 한다

Agent CLI, ACP, 일반 스크립트 등은 Worker adapter로 다루고 제품 데이터 모델과 분리한다.

### 원칙 11. 제품 이름이 아니라 Run별 역할을 기록한다

사람, Agent, 서비스는 누구나 Caller가 될 수 있다. 등록·활성화되고 유효한 health check를 통과한 실행 백엔드는 누구나 Worker가 될 수 있다. 같은 시스템이 통합 방식과 Run에 따라 두 역할을 모두 맡을 수 있지만, 각 Run에서는 Caller identity, trigger, 제출 인터페이스, Worker Attempt를 분리해 기록한다.

---

## 15. 권장 제품 문구

### 한국어 한 문장

> **Relay는 반복되는 AI 업무를 Task로 정의하고, 실행 결과를 Artifact로 축적하며, 여러 Task를 Project로 연결해 자동 수행하는 로컬 업무 시스템입니다.**

### 문제 중심 설명

> **Relay는 채팅 속에 묻히는 AI 업무를 검색하고 재사용할 수 있는 Task, Run, Artifact로 전환합니다.**

### 영문 한 문장

> **Relay turns recurring AI work into reusable tasks, durable runs, and connected projects.**

### 영문 기능 설명

> **Run structured AI tasks, preserve their artifacts, reuse past results, and connect tasks into repeatable projects.**

### 짧은 표현

> **From chats to tasks. From answers to artifacts.**

기존 표현을 유지한다면:

> **Agents delegate. Relay delivers.**

단, 이 문구만 사용하면 Agent 실행 도구로 오해할 수 있으므로 항상 Task, Artifact, Project 설명을 함께 붙인다.

### 신뢰성을 함께 말한다

위 문구들은 "무엇을 만드는가"만 말한다. 그것만으로는 흔한 워크플로 도구와 구분되지 않는다. 실제 차별점인 실행 신뢰성을 항상 한 줄 덧붙인다.

> **믿을 수 있는 결과만 자산이 된다. Relay는 격리된 사본에서 작업하고, 변경 내용을 검증한 뒤에만 실제 폴더에 반영합니다. 멈춘 실행은 감지해 회수하고, 검증되지 않은 Agent는 실행하지 않습니다.**

영문:

> **Every artifact is produced under supervision — isolated workspace, verified delta, atomic delivery, audited agents.**

짧은 결합형:

> **From chats to tasks. From answers to artifacts. Every artifact verified.**

---

## 16. 최종 제품 구조

```text
Relay
├─ Callers
│   ├─ Human
│   ├─ Agent
│   └─ Service / Automation
│
├─ Tasks
│   ├─ 재사용 가능한 업무 정의
│   ├─ 입력 및 출력 계약
│   └─ Worker·검증 정책
│
├─ Runs
│   ├─ Task Runs
│   │   ├─ Attempts
│   │   ├─ Inputs
│   │   ├─ Artifacts
│   │   └─ Receipts
│   └─ Project Runs
│       ├─ Child Task Runs
│       ├─ Resolved Connections
│       └─ Final Artifacts
│
├─ Artifacts
│   ├─ 검색
│   ├─ 선택 및 재사용
│   ├─ Snapshot
│   └─ Lineage
│
├─ Projects
│   ├─ Task 연결
│   ├─ Artifact 전달 매핑
│   ├─ 실패 및 승인 정책
│   └─ 전체 실행 보고
│
├─ Routines
│   ├─ Task 반복 실행
│   ├─ Project 반복 실행
│   └─ 실행·중복·알림 정책
│
└─ Workers
    ├─ Built-in Agent CLI
    ├─ Custom Agent App
    ├─ Registered Local Agent
    └─ Registered Script / CLI
```

---

## 17. 최종 판단

Relay의 핵심은 Agent가 다른 Agent를 호출하는 기술 그 자체가 아니다.

Relay의 핵심은 다음 문장으로 정리된다.

> **AI가 수행한 반복 업무와 결과물이 채팅 속에서 사라지지 않도록, 이를 재실행 가능한 Task와 검색·재사용 가능한 Artifact로 만들고, 여러 Task를 Project와 Routine으로 운영하는 것.**

따라서 앞으로 기능 우선순위를 판단할 때 다음 질문을 기준으로 삼는다.

1. 이 기능이 반복 업무를 명확한 Task로 만드는가?
2. 실행을 추적 가능한 Run과 Attempt로 남기는가?
3. 결과를 재사용 가능한 Artifact로 만드는가?
4. 과거 결과를 사람과 Agent가 쉽게 검색할 수 있게 하는가?
5. Artifact를 다음 Task로 안정적으로 전달하는가?
6. 여러 Task를 하나의 Project로 운영하는 데 필요한가?
7. Task 또는 Project를 Routine으로 반복 실행하는 데 필요한가?

이 질문에 직접 기여하지 않는 기능은 핵심 로드맵 이후로 미룬다.
