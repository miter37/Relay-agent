# Relay × Buzz UI/UX 벤치마크 및 구현 가이드

> 조사일: 2026-08-12
> 상태: 조사 완료, Artifact preview 1차 slice 및 JSON data surface 1차 slice 구현 완료
> 범위: `block/buzz`의 실제 GitHub 소스와 현재 Relay GUI 소스 비교

2026-08-12에 이 문서의 Artifact preview 방향 중 1차 slice를 구현했다. 공통 `ArtifactExplorerView`에 역할·publication status·format·크기 목록, 중앙 preview surface, 접이식 path/metadata panel, Windows 기본 앱 열기·폴더 열기·경로 복사 액션, unsupported 파일의 경로 fallback, format/status visual labels를 추가했다. 구현은 [artifacts.py](../relay/gui/artifacts.py), [design_styles.py](../relay/gui/design_styles.py), 회귀 검증은 [test_artifacts_gui.py](../tests/test_artifacts_gui.py)에 있다.

같은 날 JSON을 무조건 raw `<pre>`로 노출하던 주요 상세 화면도 공통 표 렌더러로 전환했다. 객체는 `Field / Value / Type` 속성표, 객체 배열은 실제 데이터 표, 중첩 객체·배열은 셀 안의 하위 표로 표시하며 Raw JSON은 진단용 보조 경로로 남긴다. Artifact 화면에는 이 구조화된 Tree view와 함께 Markdown 문법을 사용하지 않는 동일 크기 들여쓰기 보고서형 Text view를 제공하고 Text view를 기본값으로 선택한다. 구현은 [json_display.py](../relay/gui/json_display.py)와 [artifacts.py](../relay/gui/artifacts.py), 회귀 검증은 [test_json_display.py](../tests/test_json_display.py)와 [test_artifacts_gui.py](../tests/test_artifacts_gui.py)에 있다. Run Workspace 내부 통합과 Review drawer는 다음 slice로 남아 있다.

## JSON 데이터 표면의 공통 규칙

상용 개발자 도구의 공통 패턴은 “JSON을 숨긴다”가 아니라 “사람이 먼저 읽는 구조화된 뷰를 기본값으로 두고 원문을 보조로 둔다”는 것이다. [Postman Responses](https://learning.postman.com/latest-v-12/docs/use/send-requests/response-data/responses)는 Preview·Raw를 나누고 Preview에서 JSON/XML을 읽게 하며, [Postman Visualizer](https://learning.postman.com/docs/use/send-requests/response-data/visualizer)는 객체 배열을 실제 표·차트로 바꿀 수 있게 한다. [Stripe Workbench](https://docs.stripe.com/workbench/overview)는 데이터 맵과 상세 inspector를 분리하고, [GitHub Actions run logs](https://docs.github.com/en/actions/how-tos/monitor-workflows/use-workflow-run-logs)는 실행 요약·단계·로그를 계층적으로 보여주며 실패 지점으로 사용자를 안내한다. Relay는 이 원칙을 실행 결과 검토에 맞게 다음처럼 적용한다.

- 단일 객체: 한 행에 한 속성을 두고 `Field / Value / Type`으로 의미를 고정한다. 문자열의 따옴표, null의 `null` 반복, boolean의 Python식 표기를 제거해 읽기 부담을 줄인다.
- 객체 배열: `#`와 필드명을 열로 승격한다. 행마다 값이 달라도 전체 배열에서 관찰된 필드의 합집합을 사용하고, 없는 값은 빈 값으로 둔다.
- 중첩 데이터: 접힌 문자열이나 `array — 5 items` 같은 요약으로 끝내지 않고 부모 셀 안의 작은 하위 표로 유지한다. 깊이와 행 수에는 상한을 두어 거대한 결과가 화면을 장악하지 않게 한다.
- 원문/진단: Raw JSON은 사라지지 않는다. API 복사·정밀 diff·문제 신고가 필요한 사용자를 위해 보조 액션으로 남긴다.
- 실행 맥락: JSON만 예쁘게 보이는 것으로 끝내지 않고, 화면의 상단에는 상태·출처·생성 시점을 두고, 결과물은 Artifact preview에서 포맷별 렌더러와 경로 액션으로 이어지게 한다.
- 사용자 선택: Artifact JSON은 `Text view`를 기본으로 하되 `Tree view` 토글을 제공한다. 텍스트형은 Markdown을 파싱하지 않고, 필드명·컨테이너명·배열 인덱스를 bold 처리한 동일 크기 텍스트 행과 들여쓰기로 긴 결과를 빠르게 훑게 한다.
- 중복 결과물: Task Run의 `Result` 그룹이 이미 제공하는 `result.json`은 `Files` 그룹에서 다시 보여주지 않는다. 실제 Result는 유지하고 부수 파일 목록의 중복 항목만 제거해 사용자가 같은 결과물을 두 번 판단하지 않게 한다.
- Artifact actions: 파일 액션은 플랫폼명을 노출하지 않는 `Open`으로 통일하고 Qt `QDesktopServices`를 통해 Windows 기본 앱, macOS `open`, Linux 데스크톱 기본 앱으로 연결한다. `Show folder`는 Artifact Explorer가 계산한 파일의 즉시 부모 디렉터리를 그대로 전달한다.
- Report typography: 모든 필드명과 값은 동일한 13px 크기로 렌더링한다. 구조를 읽는 데 필요한 필드명·컨테이너명·배열 인덱스만 bold 처리하고, 계층은 줄바꿈과 18px CSS padding 및 명시적 non-breaking spaces로 전달한다.
- Run Overview: 읽기용 HTML 표면은 마우스·키보드 선택을 허용해 별도 복사 버튼 없이 드래그 복사할 수 있어야 한다. 메타데이터는 보더리스 key/value 목록으로 보여주고, Requested task는 `<pre>`가 아닌 줄바꿈 가능한 블록으로 렌더링해 가로 스크롤을 만들지 않는다.
- Polling stability: 동일한 Run 응답은 기존 문서를 다시 주입하지 않는다. 내용이 실제로 바뀌더라도 사용자가 텍스트를 선택 중이면 새 문서를 즉시 교체하지 않고 selection 종료 뒤 반영해 복사 동작과 화면 안정성을 우선한다.

이 규칙은 `Task Run`의 Inputs·Progress·Review artifacts·Events, Project의 Overview와 raw Definition, Routine receipt, Schedule settings/history, Main Window의 Inputs·Events, Artifact JSON preview에 적용됐다. 새 JSON 화면은 개별 `<pre>` 포맷터를 만들지 말고 `render_json_html()`을 사용해야 한다.

## 1. 이 문서의 목적

Relay의 기능은 이미 단순 데모 수준을 넘어섰다. Task, Task Run, Project, Project Run, Artifact, Review gate, Orchestrator, Routine, CLI, daemon API가 모두 존재한다. 그러나 현재 GUI는 이 기능들이 하나의 자연스러운 작업 경험으로 연결되기보다, 관리 화면·테이블·탭·새로고침의 조합처럼 느껴질 수 있다.

이 문서는 Buzz의 README나 홍보 화면을 모방하기 위한 문서가 아니다. Buzz 저장소의 실제 레이아웃, React 컴포넌트, 상태 관리, 스크롤 보존, live update, drawer, loading/empty/error 처리 코드를 읽고 다음을 남기기 위한 구현 기준서다.

- Buzz가 실제 코드에서 어떤 UX 계약을 구현했는가
- Relay의 현재 구조와 어떤 차이가 있는가
- Relay의 실행·검수·결과물 도메인에 무엇을 가져올 수 있는가
- 무엇은 가져오면 안 되는가
- 다음 구현을 어떤 순서와 검수 기준으로 진행할 것인가

이 문서에서 `벤치마크`는 시각 스타일의 복사가 아니라 다음 네 가지의 비교를 뜻한다.

1. 정보 구조: 사용자가 무엇을 중심으로 화면을 이해하는가
2. 상태 구조: 갱신·선택·스크롤·로딩 상태가 어떻게 보존되는가
3. 상호작용 구조: 상세 정보와 액션이 어디에서 이어지는가
4. 완성도 구조: 빈 상태·오류·보류·성공·재실행까지 어떻게 설명되는가

## 2. 조사 범위와 근거

### 2.1 Buzz 저장소에서 읽은 주요 파일

Buzz는 Tauri + React + TypeScript + Vite 기반 데스크톱 앱이며, `src/app`, `src/features`, `src/shared`로 기능과 공통 UI를 나눈다. 실제 저장소 설명과 데스크톱 구조는 [Buzz README](https://github.com/block/buzz/blob/main/README.md)와 [desktop 구조](https://github.com/block/buzz/tree/main/desktop)에서 확인했다.

앱 셸과 표면:

- [AppShell.tsx](https://github.com/block/buzz/blob/main/desktop/src/app/AppShell.tsx)
- [AppShellChannelSurface.tsx](https://github.com/block/buzz/blob/main/desktop/src/app/AppShellChannelSurface.tsx)
- [AppTopChrome.tsx](https://github.com/block/buzz/blob/main/desktop/src/app/AppTopChrome.tsx)
- [BuzzThemeSurfaces.tsx](https://github.com/block/buzz/blob/main/desktop/src/app/BuzzThemeSurfaces.tsx)
- [sidebar.tsx](https://github.com/block/buzz/blob/main/desktop/src/shared/ui/sidebar.tsx)

채널·패널·대화:

- [ChannelScreen.tsx](https://github.com/block/buzz/blob/main/desktop/src/features/channels/ui/ChannelScreen.tsx)
- [ChannelPane.tsx](https://github.com/block/buzz/blob/main/desktop/src/features/channels/ui/ChannelPane.tsx)
- [ChannelScreenHeader.tsx](https://github.com/block/buzz/blob/main/desktop/src/features/channels/ui/ChannelScreenHeader.tsx)
- [RightAuxiliaryPane.tsx](https://github.com/block/buzz/blob/main/desktop/src/features/channels/ui/RightAuxiliaryPane.tsx)
- [FocusThreadDrawer.tsx](https://github.com/block/buzz/blob/main/desktop/src/features/channels/ui/FocusThreadDrawer.tsx)

스크롤과 live update:

- [MessageTimeline.tsx](https://github.com/block/buzz/blob/main/desktop/src/features/messages/ui/MessageTimeline.tsx)
- [TimelineMessageList.tsx](https://github.com/block/buzz/blob/main/desktop/src/features/messages/ui/TimelineMessageList.tsx)
- [useAnchoredScroll.ts](https://github.com/block/buzz/blob/main/desktop/src/features/messages/ui/useAnchoredScroll.ts)
- [VirtualizedList.tsx](https://github.com/block/buzz/blob/main/desktop/src/shared/ui/VirtualizedList.tsx)
- [useLiveChannelUpdates.ts](https://github.com/block/buzz/blob/main/desktop/src/features/channels/useLiveChannelUpdates.ts)
- [workflow hooks](https://github.com/block/buzz/blob/main/desktop/src/features/workflows/hooks.ts)

프로젝트·워크플로:

- [ProjectsView.tsx](https://github.com/block/buzz/blob/main/desktop/src/features/projects/ui/ProjectsView.tsx)
- [ProjectsListHeaderBar.tsx](https://github.com/block/buzz/blob/main/desktop/src/features/projects/ui/ProjectsListHeaderBar.tsx)
- [WorkflowsView.tsx](https://github.com/block/buzz/blob/main/desktop/src/features/workflows/ui/WorkflowsView.tsx)
- [WorkflowCard.tsx](https://github.com/block/buzz/blob/main/desktop/src/features/workflows/ui/WorkflowCard.tsx)
- [WorkflowDetailPanel.tsx](https://github.com/block/buzz/blob/main/desktop/src/features/workflows/ui/WorkflowDetailPanel.tsx)

공통 UI:

- [PageHeader.tsx](https://github.com/block/buzz/blob/main/desktop/src/shared/ui/PageHeader.tsx)
- [TopChromeBackdrop.tsx](https://github.com/block/buzz/blob/main/desktop/src/shared/ui/TopChromeBackdrop.tsx)
- [shared UI directory](https://github.com/block/buzz/tree/main/desktop/src/shared/ui)

### 2.2 Relay에서 확인한 주요 파일

- [main_window.py](../relay/gui/main_window.py): 전역 셸, 네비게이션, 화면 전환, polling timer
- [job_detail.py](../relay/gui/job_detail.py): Task Run 상세, 탭, 로그, Answer, Artifact
- [project_runs.py](../relay/gui/project_runs.py): Project Run 목록, Pipeline, Artifact, Timeline, Orchestrator, Inspector
- [reviews.py](../relay/gui/reviews.py): Review inbox, 후보 결과물, 코멘트, Confirm/Rerun/Reject
- [artifacts.py](../relay/gui/artifacts.py): 공통 Artifact Explorer와 format-aware preview
- [design_tokens.py](../relay/gui/design_tokens.py): Relay 색상·간격·메트릭·상태 토큰
- [design_styles.py](../relay/gui/design_styles.py): Qt 전역 스타일
- [scroll_state.py](../relay/gui/scroll_state.py): 갱신 시 scroll/selection 보존 보조 기능

### 2.3 조사에서 확인하지 못한 것

현재 환경에서는 in-app browser runtime을 사용할 수 없어 Buzz를 직접 실행하여 클릭하는 시각 검수는 하지 못했다. 따라서 이 문서는 실제 GitHub 소스와 공개된 구조를 근거로 한 코드 수준의 벤치마크다. Buzz의 화면 이미지나 README만 보고 추정한 문서가 아니다.

## 3. Buzz가 세련되게 느껴지는 구조적 이유

### 3.1 화면이 아니라 작업 맥락을 중심으로 한다

Buzz의 `AppShell`은 단순히 여러 화면 중 하나를 보여주는 컨테이너가 아니다. navigation, channels, unread state, notifications, presence, agent runtime, relay connection, settings, terminal, huddle 등을 전역에서 조정한다.

이 구조의 결과는 다음과 같다.

- 화면을 이동해도 앱의 전역 상태가 쉽게 끊기지 않는다.
- 현재 community/channel/agent 맥락이 내비게이션과 본문에 함께 남는다.
- 새로운 이벤트가 어느 화면에서 발생했는지와 무관하게 unread·notification·presence로 연결된다.
- 상세 화면이 독립적인 작은 앱처럼 보이지 않고 하나의 workspace 안에 있는 panel처럼 보인다.

Relay의 현재 `MainWindow`도 전역 셸을 가지고 있지만, 실제 사용자 모델은 `Runs`, `Project Runs`, `Reviews`, `Tasks`, `Projects`, `Routines` 같은 화면 단위에 가깝다. 도메인 객체 기준으로는 정확하지만, 사용자가 해야 할 일 기준으로는 다음 질문을 직접 해결해야 한다.

> 지금 내가 처리해야 할 작업은 무엇인가?  
> 이 실행은 어느 단계에 있고, 다음 행동은 무엇인가?  
> 결과물을 확인하려면 어느 화면과 어느 탭으로 가야 하는가?

### 3.2 Feature slice와 작은 UI 단위의 조합

Buzz의 채널 화면은 하나의 거대한 컴포넌트가 아니다. `ChannelScreen`은 route와 데이터 조합을 담당하고, `ChannelPane`은 현재 패널 레이아웃을 담당하며, timeline, thread, composer, members, profile, auxiliary panel 등이 각각 역할을 맡는다.

이 방식은 Relay에도 중요한 시사점을 준다.

현재 `project_runs.py`는 많은 기능을 한 파일에 담고 있다. 이 파일을 단순히 더 길게 고치는 대신 다음과 같은 UI 책임을 분리해야 한다.

```text
RunWorkspace
 ├─ RunHeader
 ├─ RunStageRail
 ├─ RunActivityTimeline
 ├─ RunArtifactSurface
 ├─ RunReviewPanel
 ├─ RunNodeInspector
 └─ RunActionBar
```

이 분리는 파일을 예쁘게 나누기 위한 것이 아니다. 각 영역이 자신의 선택 상태·갱신 방식·빈 상태·액션을 독립적으로 가지게 하기 위한 것이다.

### 3.3 한 화면 안에서 깊이를 만든다

Buzz의 workflow detail은 목록에서 항목을 선택하면 오른쪽 detail panel이 열리고, 그 안에서 실행 기록을 펼칠 수 있다. 실행을 펼치면 execution trace와 approval이 같은 맥락에 나타난다.

이 방식은 다음의 전환 비용을 줄인다.

```text
목록 → 별도 상세 화면 → 실행 탭 → trace 탭 → 승인 화면
```

대신 다음처럼 동작한다.

```text
목록 → 오른쪽 상세 패널 → 실행 항목 펼침 → trace와 액션 확인
```

Relay의 Project Run에도 이 원리를 적용할 수 있다.

- Pipeline에서 노드를 클릭하면 inspector가 같은 workspace 안에 열린다.
- Artifact를 클릭하면 Artifact preview가 같은 workspace 안에서 열린다.
- Review가 필요하면 review panel이 현재 Run 위에 열리거나 오른쪽에 고정된다.
- 원래 Pipeline의 위치와 선택은 유지된다.

## 4. Relay의 현재 GUI에 대한 진단

### 4.1 강점

현재 Relay에는 이미 제품의 핵심 자산이 있다.

- 실행 기록이 durable하다.
- Task와 Project의 재사용 모델이 있다.
- Project Run에 Pipeline, Attempt, Timeline, Receipt, Artifact, Orchestrator 데이터가 있다.
- Review gate가 사람과 Orchestrator 양쪽을 지원한다.
- Artifact UID와 lineage가 있다.
- PNG, SVG, PDF, HTML, JSON, Markdown, text 등 형식별 preview를 제공하려는 공통 Explorer가 있다.
- CLI와 daemon API가 GUI와 같은 도메인 모델을 사용한다.

즉 Relay는 기능을 새로 발명해야 하는 상태가 아니라, 이미 있는 신뢰성 모델을 사용자에게 더 좋은 순서와 표면으로 보여줘야 하는 상태다.

### 4.2 데모처럼 느껴지는 원인 A: 화면 분할이 사용자 목적보다 도메인 객체를 따른다

현재 메인 네비게이션은 객체 종류별로 나뉜다.

```text
Runs
Project Runs
Reviews
Tasks
Profiles
Projects
Routines
Schedules
Settings
```

개발자에게는 분명하지만 일반 사용자는 “Project Run이 끝났는데 검수하려면 Reviews로 이동해야 하는가?”를 판단해야 한다.

개선 방향은 객체 메뉴를 모두 없애는 것이 아니다. 기본 진입점을 사용자 목적에 맞추고, 객체별 관리 화면은 보조 위치에 두는 것이다.

권장 상위 구조:

```text
Work
 ├─ Needs attention
 ├─ Running
 ├─ Recent results
 └─ All runs

Build
 ├─ Tasks
 ├─ Projects
 └─ Routines

System
 ├─ Agents
 ├─ Profiles
 └─ Settings
```

초기 구현에서는 네비게이션을 전부 재작성하지 않고, 기존 메뉴를 유지한 채 `Needs attention`와 `Run Workspace`를 먼저 추가하는 것이 안전하다.

### 4.3 데모처럼 느껴지는 원인 B: 상세 정보가 탭으로 흩어진다

Task Run 상세는 Overview, Task, Inputs, Progress, Answer, Artifacts, Logs, Events로 나뉘고 Project Run도 Pipeline, Artifacts, Timeline, Orchestrator를 사용한다.

탭은 많은 데이터를 담기에는 편하지만, 실행을 이해하는 순서를 숨긴다. 사용자는 다음을 스스로 조립해야 한다.

```text
이 Task가 무엇을 요청받았는가
→ 어떤 Worker가 실행했는가
→ 어떤 시도가 있었는가
→ 어떤 결과물이 나왔는가
→ 검수가 필요한가
→ 다음 행동은 무엇인가
```

이 흐름은 화면의 기본 구조로 표현되어야 한다. 탭은 전체 raw data나 진단용 세부 내용에 남기고, 기본 화면은 요약·단계·결과물·다음 액션 중심으로 구성해야 한다.

### 4.4 데모처럼 느껴지는 원인 C: polling과 재렌더링이 제품 상태보다 앞선다

현재 `MainWindow`에는 다음과 같은 주기 갱신이 있다.

- active runs: 1초
- logs: 1초
- Project Runs: 2초 주기 기반
- finished/schedules/reviews: 3초

이 구조는 데이터를 빨리 가져오는 데는 유리하지만, UI 입장에서는 다음 문제가 생긴다.

- 사용자가 읽고 있는 내용을 다시 씀
- 선택된 item과 현재 tab을 데이터 재설정 코드가 덮어씀
- 빈 상태와 로딩 상태가 짧게 번쩍일 수 있음
- 실제 변경이 없어도 위젯을 다시 그릴 수 있음
- 화면이 살아 있는 workspace가 아니라 polling되는 보고서처럼 느껴짐

현재 `preserve_scroll`은 필요한 회귀 방지책이다. 그러나 장기적으로는 다음 정책이 필요하다.

```text
새 데이터 수신
 ├─ 선택된 ID가 유지되는가?
 ├─ 현재 읽기 위치가 유지되어야 하는가?
 ├─ 실행이 아직 진행 중인가?
 ├─ 사용자가 현재 tail을 보고 있는가?
 └─ 현재 화면에 실제로 영향을 주는 변경인가?
```

그 결과에 따라 부분 갱신, 보류, 새 업데이트 표시, 자동 tail 이동을 구분해야 한다.

### 4.5 데모처럼 느껴지는 원인 D: Review가 다음 단계가 아니라 별도 관리함처럼 보인다

현재 `ReviewsView`는 Review 목록, 설명, Artifact Explorer, 코멘트 입력, Confirm/Rerun/Reject를 모두 제공한다. 기능은 맞다.

하지만 사용자 여정은 다음처럼 끊길 수 있다.

```text
Project Run 완료
→ Reviews 메뉴 클릭
→ Review 선택
→ Artifact 선택
→ 결과 검토
→ 코멘트 입력
→ 재실행
```

목표는 다음이다.

```text
Project Run 완료
→ 현재 화면이 Needs review 상태로 전환
→ 결과물 미리보기와 검수 기준이 바로 보임
→ Confirm 또는 Rerun with feedback
```

Reviews inbox는 유지해도 된다. 다만 이것은 “모든 검수 대기 건을 모아보는 보조 inbox”가 되어야 하고, 개별 Run의 검수는 Run Workspace 안에서 완결되어야 한다.

## 5. Buzz의 스크롤·갱신 설계에서 가져올 것

### 5.1 단순한 scrollTop 저장보다 semantic anchor가 필요하다

Buzz의 `useAnchoredScroll`은 단순히 `scrollTop` 숫자를 저장하지 않는다. 현재 사용자가 보고 있던 메시지 ID와 화면 내 상대 위치를 anchor로 잡는다.

```text
현재 화면의 기준 행 = message_id X
X의 컨테이너 상단으로부터의 위치 = 120px
```

새 행이 앞이나 뒤에 추가되거나 이미지 높이가 변해도 X가 같은 위치에 남도록 보정한다.

Relay의 적용 대상:

- Project Run Pipeline의 선택 노드
- Timeline의 현재 이벤트
- Artifact tree의 선택 Artifact UID
- Review inbox의 현재 Review ID
- Logs의 현재 attempt/offset
- 테이블과 트리의 선택 row ID

### 5.2 사용자가 과거를 읽는 동안 새 이벤트를 억지로 삽입하지 않는다

Buzz의 메시지 timeline은 사용자가 bottom에 있지 않을 때 새 도착 데이터를 별도 buffer에 둔다. 사용자는 현재 읽던 위치를 잃지 않고, “새 메시지” affordance를 통해 이동한다.

Relay의 Project Run Timeline도 같은 정책이 필요하다.

- 사용자가 timeline 하단에 있으면 새 이벤트를 자동 표시
- 사용자가 중간이나 위를 보고 있으면 위치 유지
- `새 이벤트 3개` 버튼이나 pill 표시
- 버튼을 누르면 새 이벤트 위치로 이동
- 실패·검수 필요·완료 같은 중요한 상태는 별도 attention badge로 알림

로그는 추가로 다음을 구분해야 한다.

- Auto-scroll ON + 사용자가 tail에 있음: 자동 이동
- Auto-scroll ON + 사용자가 위로 이동함: 자동 이동 중지
- 사용자가 다시 tail로 이동: 자동 이동 재개
- attempt나 stream을 바꿈: 새 범위로 이동하되 이전 선택 상태는 보존

### 5.3 갱신 주기는 화면과 상태에 따라 달라야 한다

Buzz의 workflow hooks는 모든 쿼리를 무조건 계속 polling하지 않는다.

- workflow 목록은 focus 복귀 시 stale 여부에 따라 갱신
- 실행 목록은 실제 active run이 있을 때만 1초 polling
- approval은 별도 cadence를 사용
- focus가 아니면 active polling을 중지

Relay도 다음 원칙으로 바꾸는 것이 좋다.

| 화면/상태 | 권장 갱신 방식 |
|---|---|
| 완료된 Run 목록 | 화면 진입·focus 복귀·수동 새로고침 |
| 실행 중인 Run | 선택된 Run 중심의 짧은 polling |
| 완료된 Project Run 상세 | 자동 갱신 최소화, 사용자 요청 시 갱신 |
| Timeline | 실행 중일 때만 증분 갱신 |
| Review 대기 | 대기 중일 때만 status 갱신, 선택 내용 보존 |
| Artifact preview | 파일 변경·새 preview 응답이 있을 때만 다시 렌더링 |
| Health | 낮은 주기 또는 수동 갱신 |

## 6. Review gate를 위한 목표 UX

### 6.1 상태를 눈에 띄게 하되 방해하지 않는다

Review가 필요한 경우 Run header는 다음 정보를 한 번에 보여줘야 한다.

```text
Needs review
Project Run: Relay Next Step 제안서
검수 대상: A1 현황 조사
검수자: Orchestrator
재실행: 0 / 2
```

여기서 중요한 것은 `Needs review`가 단순한 색상 badge가 아니라 다음 행동을 설명하는 상태여야 한다는 점이다.

- 무엇을 검토해야 하는가
- 누가 검토하는가
- 어떤 기준을 쓰는가
- 실패하면 어떻게 되는가
- 사용자가 지금 눌러야 할 버튼은 무엇인가

### 6.2 결과물을 검수 화면의 주 콘텐츠로 둔다

검수 화면의 우선순위는 다음과 같다.

1. 결과물 preview
2. 검수 기준과 평가 결과
3. 실행 근거와 Artifact lineage
4. 코멘트와 액션
5. raw JSON, 로그, 세부 메타데이터

현재 Artifact Explorer는 형식별 preview와 path action을 제공하므로 좋은 기반이다. 다음 단계는 이를 Review panel의 중심으로 배치하는 것이다.

권장 레이아웃:

```text
┌─────────────────────────────────────────────────────────┐
│ Run header · Needs review · Attempt 1/3                 │
├───────────────┬───────────────────────┬─────────────────┤
│ Artifact list │ Primary preview       │ Review panel    │
│               │ HTML/PDF/PNG/SVG      │ Guidelines      │
│ - result      │                       │ Auto evaluation │
│ - sources     │                       │ Comment        │
│ - report      │                       │ Confirm        │
│               │                       │ Rerun           │
└───────────────┴───────────────────────┴─────────────────┘
```

좁은 창에서는 오른쪽 Review panel을 drawer로 바꾸고, Artifact preview를 우선 유지한다.

### 6.3 Orchestrator review와 human review를 같은 표면에 표현한다

둘의 권한은 다르지만 사용자 경험은 가능한 한 같은 흐름이어야 한다.

```text
Human review:
  결과물 확인 → 코멘트 입력 → Confirm 또는 Rerun

Orchestrator review:
  가이드라인 기반 평가 → 자동 Confirm 또는 Rerun
  → 필요 시 Human handoff
```

Orchestrator panel에는 최소한 다음을 보여줘야 한다.

- 사용한 guideline
- 평가 항목
- 평가 결과와 근거
- 자동으로 내린 결정
- 사용한 rerun 횟수 / 최대 횟수
- 자동 검수가 중단되어 사람에게 넘어간 이유

자동 검수 결과는 사람의 확인을 대체한다는 인상을 주면 안 된다. Relay의 신뢰성 경계는 실행·전달·증거 보존이며, AI 결과의 사실성이나 품질을 보증하는 것이 아니다.

## 7. Artifact Explorer를 제품 경험으로 확장하는 방향

### 현재 장점

현재 [artifacts.py](../relay/gui/artifacts.py)는 다음을 이미 제공한다.

- 공통 Artifact 모델
- Result/Files/Review candidate 그룹
- JSON 구조 tree
- JSON Lines
- CSV/TSV table
- Markdown/text/HTML preview
- image/SVG/PDF 처리
- raw view
- path, containing folder, copy path
- double-click으로 외부 파일 열기
- preview scroll 보존

### 다음에 필요한 개선

1. 목록에서 각 결과물의 역할·상태·크기·최근 변경을 한눈에 표시한다.
2. 중앙 preview를 기본값으로 하고 메타데이터는 접을 수 있게 한다.
3. HTML은 안전한 내부 렌더링과 외부 링크 표시를 분리한다.
4. PNG/SVG/PDF는 미리보기 실패 시 즉시 경로와 `Windows에서 열기`를 제공한다.
5. 검수 대상 Artifact에는 `candidate`, `published`, `rejected`, `superseded`를 명확히 표시한다.
6. 재실행 후 새 Artifact가 생기면 이전 Artifact와 attempt 번호를 연결한다.
7. Artifact preview를 Run Workspace와 Review panel 양쪽에서 동일하게 사용한다.

## 8. Relay에 적용할 디자인 시스템 원칙

### 8.1 기존 토큰을 버리지 않는다

Relay의 `design_tokens.py`와 `design_styles.py`는 이미 semantic color, spacing, radius, status presentation을 정의하고 있다. Buzz를 참고한다고 해서 색상 체계를 새로 갈아엎을 필요는 없다.

먼저 부족한 것은 다음의 컴포넌트 계약이다.

```text
PageHeader
SectionHeader
StatusBadge
InlineNotice
EmptyState
LoadingSkeleton
SplitPane
ContextDrawer
ActionBar
ArtifactCard
TimelineRow
ReviewChecklist
```

각 컴포넌트는 색상뿐 아니라 다음을 정의해야 한다.

- 상태별 문구
- 기본 높이와 간격
- hover/focus/disabled
- keyboard navigation
- loading/empty/error
- 갱신 시 유지해야 하는 상태

### 8.2 시각 위계

한 화면에는 다음 세 단계의 정보 위계를 유지한다.

```text
Level 1: 현재 Run의 목적과 상태
Level 2: 지금 필요한 다음 행동
Level 3: 근거·메타데이터·raw detail
```

현재 Relay는 Level 3 정보가 풍부한 반면 Level 1과 Level 2가 상대적으로 약하다. 따라서 더 많은 정보를 추가하기보다 우선순위를 바꿔야 한다.

### 8.3 상태 문구는 사람이 읽는 행동 언어로 쓴다

다음과 같은 내부 상태명은 개발자에게는 유용하지만 사용자에게는 충분하지 않다.

```text
pending_human
delivery_failed
waiting_approval
output_selection_failed
```

화면에서는 내부 상태를 보존하되 다음처럼 번역한다.

```text
검수 필요
결과 전달을 확인해야 함
승인 대기 중
최종 결과물 선택 실패 — 확인 필요
```

내부 code는 tooltip·세부 inspector·raw view에 남긴다.

## 9. 단계별 구현 권장안

### Phase 0: UX 계약과 상태 모델 정리

목표는 시각 개편이 아니라 화면이 지켜야 할 상태 계약을 확정하는 것이다.

- Run Workspace의 상태 전이 정의
- selected Run/Node/Artifact/Review ID의 보존 규칙 정의
- polling 중 보존할 UI 상태 정의
- `Needs review`, `Rerunning`, `Confirmed`, `Handed off` 상태 정의
- Artifact preview와 Review panel의 공통 인터페이스 정의
- 기존 QTabWidget을 당장 제거하지 않고 새 surface와 병행

완료 기준:

- 각 갱신 경로에 “무엇을 보존해야 하는가”가 문서화됨
- UI 상태와 daemon payload 상태가 구분됨
- 기존 CLI/API 계약을 변경하지 않음

### Phase 1: Run Workspace shell

- Project Run의 header에 상태·현재 단계·다음 액션을 배치
- Pipeline을 수평/수직 stage rail 또는 명확한 단계 목록으로 재구성
- 선택한 노드의 inspector를 context panel로 유지
- Timeline과 Artifact summary를 같은 본문 흐름에 노출
- 기존 Pipeline/Artifacts/Timeline/Orchestrator 탭은 고급 보기로 보존

완료 기준:

- Run을 열었을 때 첫 화면만으로 목적·상태·현재 단계·다음 행동을 알 수 있음
- 노드를 선택해도 전체 Run 화면이 교체되지 않음
- polling 뒤에도 선택 Run과 선택 Node가 유지됨

### Phase 2: Review surface

- Review 대기 상태를 Run header와 attention badge에 표시
- 결과물 primary preview를 중앙에 배치
- 검수 가이드라인과 Orchestrator 평가를 오른쪽 panel에 배치
- Confirm과 Rerun with feedback을 같은 액션 영역에 배치
- 재실행 횟수와 attempt history를 항상 보이게 함
- Reviews inbox는 여러 대기 건을 관리하는 보조 화면으로 유지

완료 기준:

- 사용자가 Reviews 메뉴를 찾지 않고 Run 화면에서 검수를 끝낼 수 있음
- Confirm 시 publication 상태가 명확히 바뀜
- Rerun 시 코멘트가 새 round와 새 Task Run에 연결됨
- 최대 재실행 횟수 초과 시 이유와 사람에게 넘기는 방법이 보임

### Phase 3: 갱신·스크롤·성능 모델

- active Run만 짧은 polling
- inactive/완료 Run은 focus 또는 수동 갱신
- selected ID 기반 부분 갱신
- Pipeline/Timeline/Artifact 목록에 semantic anchor 적용
- 사용자가 위를 읽는 중에는 새 이벤트를 buffer
- `새 업데이트` 표시와 tail 이동을 분리
- HTML/JSON/table 재렌더링 시 현재 preview scroll 보존

완료 기준:

- 실행 중 갱신이 현재 읽는 위치를 움직이지 않음
- 새 데이터가 없을 때 불필요한 전체 위젯 재구성이 없음
- 새 이벤트 수와 이동 시점이 예측 가능함
- 대량 Artifact와 긴 Timeline에서 UI가 멈추지 않음

### Phase 4: 표면과 상호작용 완성도

- persistent sidebar 폭 조절·접기·상태 저장 검토
- top chrome과 content surface 위계 정리
- shared header/section/notice/skeleton/empty/error 컴포넌트 정리
- drawer open/close, Esc, focus restore, reduced motion 검토
- keyboard shortcut과 tab order 정리
- 1280×720, 1024×700, DPI 100/125/150에서 실제 Qt 검수

완료 기준:

- 기능을 처음 보는 사용자가 화면 구조를 설명할 수 있음
- 상태별로 같은 시각 언어가 반복됨
- 모든 주요 액션에 hover/focus/disabled/error 상태가 있음
- GUI regression tests와 real-Qt visual pass가 모두 통과함

## 10. 구현 시 지켜야 할 것과 지키지 말아야 할 것

### 가져올 것

- 지속되는 app shell
- 목적 중심의 workspace
- context panel과 drawer
- semantic scroll anchor
- active 상태 중심의 갱신
- skeleton/empty/error/attention 상태
- 결과물 중심의 review surface
- 공통 UI primitives
- keyboard/focus/reduced-motion 고려

### 그대로 복사하지 않을 것

- Buzz의 Nostr relay·community·channel·presence 도메인
- 채팅 중심의 timeline을 Relay의 실행 기록에 그대로 적용하는 것
- 기능 수를 늘리기 위한 복잡한 side panel 중첩
- 장식적인 animation이나 gradient를 제품 완성도의 대체물로 사용하는 것
- Relay의 local-first, daemon API, CLI, SQLite, Artifact lineage 계약을 UI 개편 때문에 흔드는 것
- 자동 검수를 품질 보증처럼 표현하는 것

Relay의 핵심 문장은 다음으로 유지한다.

> 실행은 자동화하고, 근거는 보존하며, 결과물의 사용 여부는 명시적으로 결정한다.

## 11. 검수 체크리스트

### 사용성

- [ ] 새 사용자가 Run의 목적과 현재 상태를 5초 안에 알 수 있는가?
- [ ] 다음에 해야 할 행동이 한 곳에 분명히 보이는가?
- [ ] Review를 위해 별도 메뉴를 찾아야 하지 않는가?
- [ ] 결과물을 보면서 코멘트와 재실행을 바로 할 수 있는가?
- [ ] 실패했을 때 내부 error code가 아니라 해결 방향이 보이는가?

### 상태 보존

- [ ] polling 후 selected Run이 유지되는가?
- [ ] selected Node가 유지되는가?
- [ ] selected Artifact UID가 유지되는가?
- [ ] Review panel의 입력 코멘트가 갱신으로 사라지지 않는가?
- [ ] 사용자가 위를 읽는 중 새 이벤트가 scroll 위치를 바꾸지 않는가?
- [ ] 로그 tail 자동 이동이 사용자의 수동 scroll-up을 무시하지 않는가?

### Review gate

- [ ] human review와 Orchestrator review의 차이가 표시되는가?
- [ ] 가이드라인이 실제 평가 기준으로 보이는가?
- [ ] Orchestrator의 평가 근거가 표시되는가?
- [ ] 현재 round와 최대 rerun 횟수가 보이는가?
- [ ] Confirm 후 candidate/published 상태가 구분되는가?
- [ ] Rerun 후 이전 결과물과 새 결과물이 연결되는가?
- [ ] 최대 횟수 초과 시 human handoff가 명확한가?

### Artifact

- [ ] HTML/PDF/PNG/SVG를 가능한 경우 앱 안에서 볼 수 있는가?
- [ ] preview 불가 시 경로가 표시되는가?
- [ ] 더블클릭으로 Windows 기본 앱에서 열리는가?
- [ ] Artifact 목록에서 role과 publication status를 알 수 있는가?
- [ ] preview 갱신 후 scroll이 유지되는가?
- [ ] raw view와 안전한 preview가 구분되는가?

### 품질과 접근성

- [ ] 실제 Qt 플랫폼에서 확인했는가?
- [ ] 1280×720과 1024×700에서 주요 액션이 보이는가?
- [ ] DPI 100/125/150에서 잘리지 않는가?
- [ ] 키보드만으로 Run 선택·Artifact 선택·Confirm/Rerun이 가능한가?
- [ ] disabled·loading·error 상태가 구분되는가?
- [ ] reduced motion 환경에서 drawer와 전환이 usable한가?

## 12. 후속 구현의 권장 시작점

가장 위험이 낮고 효과가 큰 첫 구현은 전체 GUI 재작성보다 다음 세 가지다.

1. 현재 Project Run 상세 상단에 `Run summary + next action` 영역을 강화한다.
2. 현재 Artifact Explorer를 중앙 preview surface로 승격한다.
3. Review가 필요한 Run에 오른쪽 Review panel을 열어 Confirm/Rerun을 연결한다.

이 세 가지가 안정화되면 이후 Pipeline rail, Timeline anchor, attention inbox, 네비게이션 재편을 단계적으로 진행한다.

구현 순서는 반드시 다음을 따른다.

```text
상태 계약
→ Run Workspace 구조
→ Review surface
→ Artifact 중심 preview
→ 갱신/스크롤 안정화
→ 시각·키보드·애니메이션 완성도
```

처음부터 Buzz와 비슷한 외형을 만들면 Relay의 실행·검수 도메인과 겉모습이 분리될 위험이 있다. 먼저 Relay의 고유한 사용자 여정을 하나의 workspace로 만들고, 그 위에 Buzz에서 검증된 상호작용 원칙을 적용해야 한다.
