> 에이전트는 추측 없이 Task와 Project를 결정적으로 등록할 수 있다.
  > 사람은 그 정의를 JSON을 해독하지 않고도 이해하고 안전하게 수정할 수 있다.

  이를 위해서는 새 기능을 옆에 덧붙이기보다, 현재 흩어진 input_schema, output_contract, Artifact role, A1/A2, final output, delivery를 하나의 일관된 개념 체계로 재정리해야 합니
  다.

  ## 중심 개념: Task Interface

  Task 등록 화면에 여러 계약 필드를 늘어놓는 대신, 사용자가 이해할 개념은 하나만 둡니다.

  > 이 Task는 무엇을 받아서 무엇을 만드는가?

  Task Interface는 세 부분으로 구성합니다.

  Task Interface

  Parameters
  실행할 때 사용자가 입력하는 값
  예: 시장, 기간, 언어

  Artifact inputs
  다른 Task나 기존 Run에서 받는 결과물
  예: research, references, source_images

  Outputs
  이 Task가 반드시 생성하는 결과물
  예: report, sources, presentation

  여기서 기존 기능과의 관계를 명확히 정리해야 합니다.

   현재 개념                   변경 방향
  ━━━━━━━━━━━━━━━━━━━━━━━━━━  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   input_schema                Parameters로 유지하고 기존 GUI 빌더 사용
  ──────────────────────────  ───────────────────────────────────────────────
   output_contract             Task Interface의 Outputs로 재정의
  ──────────────────────────  ───────────────────────────────────────────────
   to_alias=A1                 사람이 읽는 이름 있는 Artifact input으로 대체
  ──────────────────────────  ───────────────────────────────────────────────
   from_role                   선언된 Output을 선택
  ──────────────────────────  ───────────────────────────────────────────────
   result                      모든 Task에 Relay가 제공하는 기본 Output
  ──────────────────────────  ───────────────────────────────────────────────
   Artifact role               Task Output의 실행 시 식별자
  ──────────────────────────  ───────────────────────────────────────────────
   output_selection            Project의 Final outputs로 유지
  ──────────────────────────  ───────────────────────────────────────────────
   deliver_to                  향후 Delivery 단계로 통합
  ──────────────────────────  ───────────────────────────────────────────────
   Review policy/checkpoint    현재 검수 기능 유지

  즉, 별도의 Input Contract, Output Schema, Artifact Definition 같은 화면을 여러 개 만들지 않습니다. 모두 Task Interface 한 영역에서 다룹니다.

  ———

  ## 1. Task 입출력 계약: 쉽게 작성하되 명확하게

  ### 사람이 보는 화면

  Task 편집 화면에는 기본적으로 간단한 행 편집기를 제공합니다.

  Inputs & Outputs

  Parameters
  ┌ Name          Type       Required ┐
  │ target_market Text       Yes      │
  │ language      Choice     No       │
  └───────────────────────────────────┘

  Artifact inputs
  ┌ Name          Accepts              Required ┐
  │ research      Document or JSON     Yes      │
  │ references    Any file             No       │
  └─────────────────────────────────────────────┘

  Outputs
  ┌ Name          Produces             Required ┐
  │ report        Document or HTML     Yes      │
  │ sources       JSON                 Yes      │
  └─────────────────────────────────────────────┘

  사용자가 반드시 입력해야 하는 것은 최소화합니다.

  - 이름
  - 입력인지 출력인지
  - 필수 여부

  형식, 설명, 다중 파일 여부는 필요할 때 펼치는 고급 옵션으로 둡니다.

  기본 생성값도 적극 활용합니다.

  - 모든 Task에는 result Output이 자동 제공됨
  - Artifact input을 처음 추가하면 input_1 같은 이름을 제안
  - Output을 처음 추가하면 output을 제안
  - 이름은 사용자 친화적으로 입력하되 내부 식별자는 자동 생성
  - 형식을 모르면 Any file로 시작 가능
  - 설명은 Task 지시문을 바탕으로 에이전트가 제안 가능

  ### 에이전트가 보는 계약

  에이전트에게는 모호한 자연어가 아니라 고정된 기계 계약을 제공합니다.

  {
    "interface_version": 1,
    "parameters": {
      "type": "object",
      "properties": {
        "target_market": {
          "type": "string"
        }
      },
      "required": ["target_market"]
    },
    "artifact_inputs": [
      {
        "name": "research",
        "required": true,
        "cardinality": "one",
        "accepts": ["text/markdown", "application/json"]
      }
    ],
    "outputs": [
      {
        "role": "report",
        "required": true,
        "cardinality": "one",
        "produces": ["text/markdown", "text/html"]
      }
    ]
  }

  다만 이것을 사용자가 직접 작성하게 하지는 않습니다. GUI 편집기가 생성하고, CLI 에이전트는 JSON Schema를 보고 생성합니다.

  ### 중요한 구분

  Parameters와 Artifact inputs는 절대로 섞으면 안 됩니다.

  Parameters
  - language = ko
  - target_market = Korea

  Artifact inputs
  - research = 이전 Task의 report
  - references = 기존 Artifact UID

  현재 Relay의 input_schema는 Parameters에 해당합니다. Project의 A1/A2는 Artifact inputs입니다. 두 개가 “Input”이라는 이름 아래 혼재하면 에이전트도 사람도 계속 혼동합니다.

  ———

  ## 2. Project 연결: 문자열 입력에서 포트 선택으로

  현재 방식:

  from_node = research
  from_role = market_report
  to_node = proposal
  to_alias = A1

  문제는 market_report와 A1을 사람이 추측한다는 것입니다.

  새 기본 방식:

  Research / report
          ↓
  Proposal / research

  Project 편집기에서 연결을 추가하는 흐름은 다음과 같아야 합니다.

  1. 출발 Task 선택
  2. 출발 Task가 선언한 Output 선택
  3. 도착 Task 선택
  4. 도착 Task가 선언한 Artifact input 선택
  5. Relay가 즉시 호환성 표시

  From
  Market Research
  Output: report · Document

  To
  Proposal Writer
  Input: research · Document or JSON

  ✓ Compatible

  A1, A2는 내부 런타임 호환 식별자로 남길 수 있지만, 신규 GUI와 CLI 정의에서 사용자가 직접 관리하게 해서는 안 됩니다.

  새 프로젝트 정의에는 의미 있는 to_input을 사용하고, 런타임에서 필요하면 Relay가 안정적인 alias를 계산합니다.

  {
    "from_node": "market_research",
    "from_output": "report",
    "to_node": "proposal",
    "to_input": "research"
  }

  기존 Project는 그대로 실행되어야 합니다.

  A1 → Legacy input A1
  A2 → Legacy input A2

  편집할 때 Relay가 이름 있는 입력으로 전환하도록 제안하되, 강제 변환하지 않습니다.

  ———

  ## 3. 호환성 사전 검증: 세 단계로 구분

  모든 문제를 같은 “오류”로 표시하면 사용자가 피로해집니다. 검증을 세 층으로 나누는 것이 좋습니다.

  ### 구조 검증

  확실히 잘못된 정의입니다.

  - 존재하지 않는 노드
  - 같은 노드 ID
  - 자기 연결
  - DAG 순환
  - 같은 단일 입력에 두 개의 연결
  - 존재하지 않는 Task
  - 필수 Artifact input 누락
  - 존재하지 않는 Output 연결
  - 존재하지 않는 Final output
  - 허용되지 않은 Delivery 대상

  이것은 저장을 막습니다.

  ### 호환성 검증

  계약 간의 불일치입니다.

  - HTML만 생성하는 Output을 JSON 전용 Input에 연결
  - 복수 Output을 단일 Input에 연결
  - 폴더 Output을 파일 Input에 연결
  - 선택적 Output을 필수 Input의 유일한 공급원으로 연결

  명확한 불일치는 Error로 처리합니다.

  형식 정보가 없는 기존 Task처럼 판단할 수 없는 경우는 Warning입니다.

  ### 실행 이력 검증

  계약은 맞지만 실제 실행이 불안정한 경우입니다.

  Warning
  “sources”는 필수 Output으로 선언되어 있지만
  최근 성공 Run 7개 중 2개에서 생성되지 않았습니다.

  이것은 저장을 막지 않습니다. 선언과 실제를 혼동하지 않는 것이 중요합니다.

  - Declared: Task 작성자가 약속한 계약
  - Observed: 과거 Run에서 실제 관측된 결과
  - Current: 지금 편집 중인 Project의 연결

  ———

  ## 4. 인라인 오류: 저장 버튼에서 처음 알게 하지 않기

  Project 편집 중 각 행에 상태를 표시합니다.

  Connections

  ✓ Research.report → Proposal.research
    Document formats are compatible.

  ⚠ Images.output → Proposal.references
    Output type is not declared. This can run, but cannot be verified in advance.

  ✕ Analysis.summary → Website.hero_image
    Text output cannot satisfy an image input.

  상단에는 짧은 전체 상태만 보여줍니다.

  Project readiness

  2 valid connections
  1 warning
  1 issue to fix

  저장 버튼의 상태도 의미가 있어야 합니다.

  - Error 없음: Save Project
  - Warning만 있음: Save with 1 warning
  - Error 있음: 버튼 비활성화, Fix 1 issue to save
  - 기존 Legacy Project: Save without migration 가능

  문제가 있는 행을 누르면 해당 입력으로 포커스가 이동해야 합니다. 오류 메시지는 사용자가 무엇을 해야 하는지 말해야 합니다.

  나쁜 메시지:

  PROJECT_ARTIFACT_MISSING

  좋은 메시지:

  “market_data” Output은 Market Research Task에 선언되어 있지 않습니다.

  사용 가능한 Outputs:
  - result
  - report
  - sources

  ———

  ## 5. 에이전트용 결정적 작성 흐름

  에이전트가 Task와 Project를 등록할 때 가장 중요한 것은 “한 번에 맞히게 하는 것”보다 “틀렸을 때 정확히 고칠 수 있게 하는 것”입니다.

  권장 CLI 흐름은 다음과 같습니다.

  1. 스키마 조회
  2. 초안 생성
  3. 로컬 또는 daemon 검증
  4. 구조화된 진단 수신
  5. 수정
  6. 등록
  7. 등록된 정규화 정의 재조회

  명령은 단순하게 유지합니다.

  relay task schema --machine
  relay task validate --file task.json --machine
  relay task create --file task.json --machine

  relay project schema --machine
  relay project validate --file project.json --machine
  relay project create --file project.json --machine

  create와 update도 항상 같은 검증기를 통과해야 합니다. validate는 별도 규칙이 아니라 저장 직전과 동일한 검증을 미리 실행하는 명령입니다.

  진단은 사람이 읽는 메시지와 에이전트가 수정할 수 있는 위치 정보를 함께 반환합니다.

  {
    "valid": false,
    "errors": [
      {
        "code": "CONNECTION_OUTPUT_UNKNOWN",
        "path": "/connections/1/from_output",
        "message": "Output 'market_data' is not declared by node 'research'.",
        "suggestions": ["report", "sources", "result"]
      }
    ],
    "warnings": []
  }

  필수 속성:

  - 안정적인 오류 코드
  - JSON Pointer 형태의 정확한 위치
  - 관련 노드와 포트
  - 허용 가능한 후보
  - 저장 차단 여부
  - 자동 수정 가능한지 여부

  에이전트가 문자열 오류를 해석해 다시 추측하게 만들면 안 됩니다.

  ### 등록 완료 응답

  등록 후에는 입력 JSON을 그대로 돌려주는 것이 아니라 Relay가 정규화한 최종 정의를 반환해야 합니다.

  {
    "created": true,
    "project_id": "project-123",
    "version": 1,
    "valid": true,
    "normalized_definition": {},
    "warnings": []
  }

  그러면 에이전트는 실제 등록된 상태를 다시 확인할 수 있습니다.

  ———

  ## 6. 사람과 에이전트가 같은 모델을 사용해야 함

  GUI 전용 규칙과 CLI 전용 규칙을 따로 만들면 다시 어긋납니다.

  중앙에 하나의 서비스가 있어야 합니다.

  Task/Project Definition
            ↓
  Contract Normalizer
            ↓
  Graph Validator
            ↓
  Structured Diagnostics
          ↙    ↓    ↘
        GUI   CLI   API

  GUI는 진단 결과를 시각적으로 보여주고, CLI는 JSON으로 출력할 뿐입니다. 의미는 완전히 같아야 합니다.

  예를 들어 GUI에서 유효한 연결은 CLI에서도 유효해야 하며, CLI에서 저장된 Project를 GUI가 손실 없이 편집할 수 있어야 합니다.

  이것이 진정한 “사람과 에이전트의 공동 작성” 기반입니다.

  ———

  ## 7. 실제 실행에서 계약 검증

  저장 전 검증만으로는 충분하지 않습니다. Worker가 선언과 다르게 결과물을 만들 수 있기 때문입니다.

  Task Run 종료 직전에 Relay가 Output 계약을 확인합니다.

  Declared outputs
  - report: required, one
  - sources: required, one

  Produced outputs
  - report: one
  - output: two
  - sources: none

  결과:

  Output contract not satisfied

  Missing required Output: sources
  Unexpected Outputs: output (2)

  여기서는 정책을 신중하게 설계해야 합니다.

  ### 초기 도입기

  기존 Task와의 호환성을 위해:

  - 계약 없는 Task: 기존처럼 실행
  - 계약 있는 신규 Task: 계약 검증
  - 필수 Output 누락: OUTPUT_CONTRACT_VIOLATION
  - 선언되지 않은 추가 Output: 기본 Warning
  - 형식 불일치: Error 또는 정책에 따른 Warning

  ### Review와의 관계

  계약 검증과 Review는 서로 대체하지 않습니다.

  - 계약 검증: 구조적으로 약속한 결과가 존재하는가?
  - Review: 결과의 내용과 품질이 충분한가?

  Task 실행
   → Output 계약 검증
   → Review
   → Confirm 또는 피드백 재실행

  Output 자체가 없으면 사람에게 품질 검수를 요청하기 전에 계약 오류로 처리해야 합니다.

  ———

  ## 8. 실행 이력으로 계약 개선

  Task 상세 화면에 Interface 영역을 추가합니다.

  Task Interface

  Inputs
  research       Required · Document/JSON

  Outputs
  report         Required · Document/HTML
                 Observed in 12/12 successful Runs

  sources        Required · JSON
                 Observed in 10/12 successful Runs
                 ⚠ Missing in 2 Runs

  여기서 사용자가 할 수 있는 일:

  - 계약 수정
  - 관측된 역할을 Output으로 추가
  - 더 이상 생성되지 않는 Output 제거
  - 형식 범위를 조정
  - 해당 역할이 누락된 Run 열기

  에이전트도 같은 정보를 API로 읽을 수 있어야 합니다.

  하지만 자동으로 계약을 바꾸면 안 됩니다. 실행 이력은 “제안 근거”이고, 계약은 명시적인 정의입니다.

  Observed role “chart” appeared in 8 recent Runs.
  Add it to this Task’s declared Outputs?

  ———

  ## 9. Wait 노드: 계약 기반이 안정된 뒤

  Wait는 유용하지만 처음부터 범용 이벤트 엔진을 만들 필요는 없습니다.

  첫 버전은 세 종류면 충분합니다.

  Wait until
  - 특정 날짜·시간
  - 일정 시간 경과
  - 사용자가 Continue

  이후 확장:

  - webhook 수신
  - 특정 Artifact 도착
  - 외부 시스템 상태 변경
  - Schedule/Routine 신호

  UX에서는 Task처럼 보이되 Worker를 실행하지 않는 시스템 노드로 표시합니다.

  Wait
  Continue at Aug 15, 09:00

  Project Run은 실패하지 않고 waiting 상태가 되며, 대기 이유와 예상 재개 시간이 Workspace와 Pipeline에 보여야 합니다.

  ———

  ## 10. Delivery 노드: 기존 기능을 통합하는 방향

  Delivery 기능을 새로 중복 구현하면 안 됩니다. 현재 checkpoint의 deliver_to, final output, folder allowlist를 하나의 명시적인 단계로 통합합니다.

  Delivery

  What
  - Proposal.report

  Where
  - Folder
  - Webhook
  - API endpoint

  When
  - After review confirmation

  On failure
  - Retry 3 times
  - Notify user
  - Keep Project Run awaiting action

  첫 버전은 이미 안전장치가 있는 Folder delivery만 지원하는 것이 좋습니다. 이후 webhook과 API를 추가합니다.

  장기적으로는 노드보다 “Final delivery stage”라는 표현이 사용자에게 더 자연스러울 수도 있습니다. 내부적으로는 그래프 노드여도 GUI에서는 Project 설정 마지막 단계로 보여줄 수 있
  습니다.

  ———

  ## 조건부 연결은 계속 보류하는 것이 맞음

  현재 요구에는 다음 흐름이면 충분합니다.

  Task 실행
   → 계약 검증
   → Review
   → Confirm 또는 피드백 재실행
   → 다음 Task

  조건부 분기는 실제 사용 사례가 축적된 뒤 도입해야 합니다. 지금 추가하면 사용자는 조건식, 결과 경로, fallback 경로까지 이해해야 하고 Project 등록이 더 어려워집니다.

  우선은 다음 상황이 반복적으로 나타나는지 관찰하면 됩니다.

  - 평가 결과에 따라 서로 다른 Task로 가야 함
  - 실패 종류에 따라 다른 복구 Task가 필요함
  - 입력 값에 따라 일부 노드를 건너뛰어야 함

  이 요구가 실제로 누적되면 그때 제한된 Router를 추가합니다.

  ———

  # 사용자 여정

  ## 사람이 새 Task를 등록할 때

  1. 이름과 Instructions 입력
  2. 필요하면 Parameters 추가
  3. 다른 Task 결과를 받는다면 Artifact input 추가
  4. 생성할 결과가 있다면 Output 추가
  5. Save

  대부분의 단일 Task는 기본 result만으로 충분하므로 추가 계약을 작성하지 않아도 됩니다.

  ## 에이전트가 Task를 등록할 때

  1. task schema 조회
  2. 정의 생성
  3. validate
  4. 오류 위치와 후보를 이용해 수정
  5. create
  6. normalized_definition 확인

  ## 사람이 Project를 만들 때

  1. Task들을 추가
  2. 각 Task 카드에서 Output → Input 연결
  3. Relay가 호환 가능한 대상만 우선 제시
  4. 필요한 Review 설정
  5. Final output 선택
  6. Readiness가 Ready인지 확인
  7. 저장

  ## 에이전트가 Project를 만들 때

  1. 관련 Task 상세와 Interface 조회
  2. 이름 있는 포트로 connections 생성
  3. project validate
  4. 구조화된 오류 수정
  5. 등록
  6. 등록된 그래프 재조회

  ———

  # 실제 개발 순서

  ## Phase 1 — 개념 정리와 중앙 검증기

  가장 먼저 해야 합니다.

  - Task Interface v1 정의
  - Parameters / Artifact inputs / Outputs 구분
  - 기존 output_contract 의미 재정의
  - 이름·형식·cardinality 규칙 정의
  - 구조화된 Diagnostic 모델
  - 중앙 Task/Project validation 서비스
  - 구버전 정의의 호환 정책

  이 단계에서는 GUI를 크게 바꾸지 않습니다. 모델과 의미부터 고정합니다.

  ## Phase 2 — 에이전트 작성 경로

  Relay의 핵심 요구이므로 GUI보다 먼저 또는 동시에 진행해야 합니다.

  - task schema/validate
  - project schema/validate
  - create/update에 동일한 검증 적용
  - 구조화된 오류 위치와 수정 후보
  - 정규화된 등록 결과
  - Catalog detail에 Task Interface 노출
  - 에이전트 E2E 등록 시나리오

  ## Phase 3 — Task 편집 UX

  - 기존 input_schema 빌더를 Parameters로 명확히 이름 변경
  - 자유 JSON output_contract 편집기 제거
  - Artifact inputs/Outputs 행 빌더 추가
  - 기본값과 자동 ID 생성
  - 고급 JSON은 접힌 Diagnostics/Advanced 영역에서만 제공
  - 과거 관측 Output 제안

  ## Phase 4 — Project 연결 UX

  - from_role 자유 입력을 Output picker로 변경
  - to_alias 자유 입력을 Artifact input picker로 변경
  - 호환 가능한 포트 우선 표시
  - 각 연결 행의 인라인 상태
  - 저장 전 Readiness 요약
  - Final output도 선언된 Output picker 사용
  - Legacy A1/A2 표시와 점진적 변환

  ## Phase 5 — 실행 시 계약 집행

  - 필수 Output 존재 여부
  - 단일/복수 개수
  - 파일·폴더·미디어 타입
  - 계약 위반 전용 상태와 이벤트
  - Project 연결 실패 메시지 개선
  - Review 전에 계약 검증

  ## Phase 6 — 선언과 관측 비교

  - Task Interface 건강도
  - 최근 Run 준수율
  - 누락/추가 역할
  - 계약 변경 제안
  - Project 편집기에서 관측 근거 표시

  ## Phase 7 — Wait
  - Duration
  - Manual continue
  - 지속 가능한 waiting 상태
  - 재시작 후 복구
  - Timeline과 Pipeline 표현

  ## Phase 8 — Delivery

  - 기존 folder delivery와 final output 통합
  - 검수 완료 후 전달
  - 허용 경로·재시도·감사 이벤트
  - 이후 webhook/API 확장

  ———

  # 반드시 피해야 할 것

  - input_schema, artifact_schema, output_schema, output_contract를 각각 별도 사용자 개념으로 노출
  - 모든 Task 등록 시 입출력 계약 작성을 강제
  - MIME type이나 JSON Schema를 일반 사용자에게 직접 작성하게 함
  - 선언되지 않은 과거 Task를 갑자기 저장·실행 불가로 만듦
  - GUI와 CLI에 서로 다른 검증 규칙 구현
  - 관측된 Output을 사용자의 승인 없이 계약으로 자동 등록
  - Orchestrator가 등록된 Task/Project 계약을 몰래 수정
  - 모든 Warning 때문에 저장을 막음
  - Wait·Delivery·Router를 한꺼번에 추가해 Project 편집기를 복잡하게 만듦

  ———

  # 이 도약의 제품 정의

  이번 업그레이드는 “Project에 타입을 추가한다” 정도로 설명하면 작게 들립니다. 제품적으로는 다음과 같습니다.

  > Relay Graph Contracts
  > 사람이 읽을 수 있고, 에이전트가 결정적으로 작성하며, 실행 전에 검증되고, 실행 결과로 다시 증명되는 Task·Project 인터페이스.

  성공 여부는 기능 개수보다 아래 지표로 판단해야 합니다.

  - 에이전트의 Project 최초 등록 성공률
  - 잘못된 role/alias로 인한 런타임 실패 감소율
  - Project 저장 전 발견된 연결 오류 수
  - 사람이 기존 Project의 데이터 흐름을 이해하는 시간
  - Task 등록에 걸리는 평균 시간
  - Advanced JSON을 직접 열어야 했던 비율
  - Output 계약 준수율
  - Legacy Project 무수정 실행 성공률

  제 판단으로 가장 중요한 첫 구현 단위는 다음 네 가지입니다.

  > Task Interface v1 + 중앙 Diagnostic Validator + CLI validate + Project 연결 Picker

  이 네 가지가 하나의 세로 슬라이스로 완성되어야 합니다. 모델만 만들거나 GUI Picker만 먼저 만들면 다시 반쪽짜리가 됩니다. 이 슬라이스가 완성되면 에이전트는 정확히 등록하고, 사
  람은 같은 정의를 자연스럽게 확인·수정하며, Relay는 실행 전에 연결 오류를 설명할 수 있게 됩니다.
