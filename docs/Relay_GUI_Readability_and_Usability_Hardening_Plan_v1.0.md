# Relay GUI Readability and Usability Hardening Plan v1.0

## 1. 목적

현재 GUI의 최우선 문제는 장식의 부족이 아니라 가독성과 일관성의 부족이다. 이 작업은 모든 메뉴, 화면, 입력 폼, 표, 상세 보기, 팝업에서 사용자가 다음을 즉시 알아볼 수 있게 만드는 것을 목표로 한다.

1. 무엇이 제목, 본문, 보조 설명, 입력값인지
2. 어떤 항목이 선택되었고 어디에 키보드 포커스가 있는지
3. 무엇이 실행 가능한 버튼이고 무엇이 비활성 상태인지
4. 성공, 진행, 주의, 실패 중 어떤 상태인지
5. 다음에 취할 수 있는 가장 안전한 행동이 무엇인지

시각 방향은 화려한 콘솔이 아니라 **단순하고 차분한 업무 도구**로 정한다. 그라데이션, 글로우, 과도한 카드, 장식용 색은 사용하지 않는다. 중립색 표면과 한 가지 주 강조색을 기본으로 하고, 성공·주의·실패 색은 실제 상태 표시에만 쓴다.

## 2. 현재 확인된 문제

### 2.1 전역 스타일 적용 범위 누락

- 전역 QSS의 기본 전경색이 `QApplication, QMainWindow`에만 지정되어 일반 `QWidget`, `QLabel`, 폼 라벨 등이 운영체제 기본 팔레트의 검정색을 유지할 수 있다.
- 실제 1280×720 오프스크린 캡처에서 다수 일반 라벨이 어두운 배경에 검정색으로 렌더링되어 읽기 어려운 현상이 재현되었다.
- `QDialog`, `QMessageBox`, `QMenu`, `QToolTip`, `QComboBox` 팝업 목록, 체크박스, 라디오 버튼, 스핀박스, 스크롤바, 스플리터, 진행 표시 등 공통 Qt 요소의 명시적 스타일이 없다.

### 2.2 대비가 약한 토큰과 상태 조합

- 현재 `text.muted`는 `bg.surfaceRaised`에서 약 3.5:1로 일반 본문 기준에 부족하다.
- 현재 밝은 파란 기본 버튼 위 흰색 글씨도 충분한 대비를 보장하지 못한다.
- 비활성, 읽기 전용, placeholder, 선택, hover 상태가 서로 너무 비슷하거나 운영체제 기본색에 의존한다.

### 2.3 과거 라이트 테마 색상의 잔존

- `main_window.py`, `job_detail.py`, `routines.py` 등에 밝은 배경용 상태 색상과 인라인 hex 색상이 남아 있다.
- 같은 의미의 상태가 화면마다 다른 전경색·배경색 조합을 사용한다.
- 공통 위젯도 로컬 `setStyleSheet()`를 사용해 전역 규칙을 우회하므로 테마 수정이 전체에 일관되게 반영되지 않는다.

### 2.4 정보 구조와 시각적 위계 문제

- 왼쪽 영역에 검색, 네 개 필터, Schedule, 설정, 주 메뉴, Run 목록이 동시에 몰려 있어 1차 탐색과 페이지별 도구가 혼재한다.
- 빈 공간은 크지만 실제 정보가 있는 영역은 좁고, 중요한 행동과 보조 행동의 구분이 약하다.
- 제목, 섹션명, 보조 문구, 빈 상태 문구가 기본 라벨에 의존해 위계가 일정하지 않다.

## 3. 품질 기준

### 3.1 색상과 대비

- 일반 텍스트와 배경: 최소 4.5:1
- 큰 제목 텍스트와 배경: 최소 3:1
- 포커스 테두리, 입력 경계, 선택 상태 등 UI 구분 요소: 최소 3:1
- `text.primary`, `text.secondary`, `text.muted`를 실제 사용 가능한 모든 표면과 조합해 자동 검사한다.
- 상태는 색만으로 전달하지 않고 텍스트, 아이콘 또는 형태를 함께 사용한다.
- 비활성 컨트롤도 상태 구분은 분명해야 하며, 값이나 라벨을 읽을 수 없을 정도로 흐리게 만들지 않는다.

### 3.2 단순한 팔레트

- 기본 배경 계층은 `canvas`, `surface`, `raised/input` 세 단계까지만 사용한다.
- 일반 경계선은 한 종류, 키보드 포커스 경계선은 한 종류로 제한한다.
- 기본 행동은 접근 가능한 파란색 한 종류를 쓴다.
- 성공·주의·실패·진행 색은 Badge, Notice, 작은 아이콘과 경계에만 사용한다.
- 큰 면적의 원색 채움, 글로우, 그라데이션, 장식용 그림자는 제거한다.

### 3.3 상호작용 상태

모든 컨트롤은 `normal`, `hover`, `focus`, `pressed`, `checked/selected`, `disabled`, `read-only`, `error` 상태를 구분해야 한다. 마우스 없이 Tab 이동만으로 현재 위치를 알아볼 수 있어야 한다.

## 4. 구현 순서

### Phase 0 — 화면·위젯 전수 목록과 기준 캡처

1. 1280×720과 1024×700에서 현재 화면 기준 캡처를 만든다.
2. 모든 top-level 화면과 팝업을 아래 점검표에 등록한다.
3. 코드의 인라인 색상, 로컬 stylesheet, 상태별 `QColor` 사용을 전수 검색한다.
4. 각 화면에 `unreviewed`, `readable`, `interaction-verified` 상태를 부여한다.

산출물: 화면 점검표, 문제 위치 목록, 변경 전 스크린샷 세트.

### Phase 1 — P0 전역 가독성 복구

1. Qt `QPalette`와 전역 QSS 양쪽에 기본 배경·전경·선택·비활성·링크 색을 명시한다.
2. `QWidget`과 `QLabel`을 포함한 일반 위젯의 기본 전경색을 확정해 운영체제 기본 검정색 유입을 막는다.
3. 아래 공통 요소를 전역 스타일에 포함한다.
   - `QDialog`, `QMessageBox`, `QInputDialog`
   - `QMenu`, `QToolTip`, `QComboBox QAbstractItemView`
   - `QLineEdit`, text editor/browser, spin/date/time controls
   - `QCheckBox`, `QRadioButton`, `QGroupBox`, form labels
   - tree/list/table/header, tab, scrollbar, splitter, progress bar, status bar
4. 기본 버튼의 배경/글자 조합과 muted 색을 대비 기준에 맞게 조정한다.
5. tooltip, placeholder, disabled, read-only, selection, focus 상태를 각각 명시한다.

완료 조건: 기본 QLabel을 포함한 모든 표준 위젯이 어두운 배경에서 읽히며, 공통 대비 자동 검사가 통과한다.

### Phase 2 — 인라인 색상 제거와 공통 의미 체계 통합

1. `main_window.py`, `job_detail.py`, `routines.py`, `schedule_detail.py`, `design_widgets.py`의 인라인 hex와 라이트 테마 상태표를 제거한다.
2. 상태 표현은 `StatusBadge`, 안내는 `InlineNotice`, 위험 행동은 `dangerAction`, 기본 행동은 `primaryAction`으로 통일한다.
3. 로컬 stylesheet 대신 semantic object name과 dynamic property를 사용한다.
4. 건강 상태, Task Run 상태, Project 단계, Routine 실행 상태가 같은 상태 사전을 공유하게 한다.
5. HTML을 표시하는 `QTextBrowser` 콘텐츠에도 본문·링크·표·오류 색을 포함한 공통 문서 CSS를 적용한다.

완료 조건: GUI 코드에 승인되지 않은 색상 리터럴이 없고 같은 상태가 모든 화면에서 같은 모습과 문구를 사용한다.

### Phase 3 — 화면별 단순화 및 가독성 검수

다음 순서로 한 화면씩 완료한다. 각 묶음은 일반·빈 상태·로딩·오류·비활성 상태를 함께 검수한다.

1. **공통 Shell과 Runs**
   - top bar, health indicator, sidebar, status bar, banner
   - 검색/필터를 Runs 전용 toolbar로 이동해 1차 내비게이션과 분리
   - Run 목록, 선택 행, 빈 상세, loading/error 상태
2. **Task Run 상세와 New Task**
   - Overview, result, artifacts, logs, events 탭
   - 긴 본문·JSON·로그의 배경, 선택, 스크롤, 링크 가독성
   - 파일/폴더 선택, Artifact 연결, 제출 오류
3. **Tasks**
   - 목록/상세, 생성·편집·실행·Save as Task 대화상자
   - 버전, Worker, 결과 상태, 기본/보조 행동 위계
4. **Projects**
   - 목록/상세/연결 요약, Project editor, Run monitor
   - 노드/연결은 장식보다 상태·입출력 role·실패 위치를 우선 표시
5. **Routines와 Schedules**
   - 목록/상세/editor/preview/history
   - enabled, next run, overlap/missed policy와 실패 상태를 명확히 구분
6. **Settings와 Agent Apps**
   - 일반 설정, 보안 우회 경고, Agent App 목록과 wizard
   - 위험 설정은 별도 경고 영역과 명시적 확인을 사용

화면 단순화 원칙:

- 한 화면에 주 행동은 하나만 강한 색을 쓴다.
- 보조 행동은 중립 outline, 삭제/중지는 danger 표현을 쓴다.
- 정보가 없는 큰 패널은 빈 설명과 다음 행동으로 대체한다.
- 카드가 정보 그룹을 실제로 나누지 않으면 카드로 만들지 않는다.
- ID·로그·JSON 외에는 시스템 기본 UI 글꼴을 사용한다.

### Phase 4 — 팝업과 플랫폼별 상태 전수 검수

1. `QMessageBox`의 information, warning, question, critical 상태를 확인한다.
2. Task/Project/Routine/Schedule/Agent App의 모든 custom dialog를 확인한다.
3. combo popup, context menu, tooltip, file/folder dialog, input dialog를 확인한다.
4. Windows 100%, 125%, 150% DPI에서 글자 잘림과 버튼 footer를 확인한다.
5. Ubuntu/macOS CI에서는 offscreen 생성 및 기본 palette/QSS 테스트를 수행한다.

완료 조건: 팝업의 제목, 본문, 입력값, validation 메시지, Cancel/Submit 버튼이 모두 읽히며 footer 순서와 포커스가 일관된다.

### Phase 5 — 회귀 방지와 최종 승인

1. 토큰 대비를 계산하는 단위 테스트를 추가한다.
2. 대표 위젯의 실제 palette와 dynamic property를 검사하는 offscreen 테스트를 추가한다.
3. 모든 top-level 화면과 custom dialog를 생성하는 GUI smoke test를 추가한다.
4. 1280×720 및 1024×700 기준 스크린샷을 화면별로 캡처해 한 세트로 검수한다.
5. 키보드 Tab 순서, focus 표시, selected/disabled/read-only 상태를 수동 점검한다.
6. GUI 집중 테스트, 전체 unittest, Ruff, release build를 통과시킨다.

## 5. 화면 점검표

| 영역 | 화면/팝업 | 필수 상태 |
|---|---|---|
| Shell | top bar, health, navigation, banner, status bar | normal, disconnected, warning, error |
| Runs | search/filter, Schedule list, Run tree, empty/detail | empty, loading, selected, running, partial, failed |
| New Task | editor, file picker, input dialog | normal, focus, disabled, validation error |
| Run detail | overview, result, Artifact, logs, events | long text, JSON, link, error, read-only |
| Tasks | list, detail, editor, runner, save dialog | empty, selected, create/edit/run error |
| Projects | list, detail, editor, Run monitor | empty, connected nodes, running, blocked, failed |
| Routines | list, detail, editor, preview/history | enabled, disabled, due, skipped, failed |
| Schedules | list, detail, editor | enabled, disabled, validation error |
| Settings | general, security bypasses | normal, warning, disabled, pending |
| Agent Apps | list, wizard, deep-test result | empty, needs test, healthy, failed, disabled |
| Native/common | message, question, input, menu, tooltip, combo popup | normal, hover, focus, selected, disabled |

## 6. 구현 단위와 예상 순서

- 1차: Phase 0–1. 글자가 안 보이는 P0 문제를 먼저 해소한다.
- 2차: Phase 2. 색상과 상태 표현의 중복을 제거한다.
- 3차: Phase 3을 화면 묶음별로 구현하고 바로 캡처·검수한다.
- 4차: Phase 4–5로 팝업, DPI, 키보드, 플랫폼, 전체 회귀를 닫는다.

각 차수는 독립적인 검증 가능한 변경으로 유지한다. 전체 레이아웃을 한 번에 다시 작성하지 않으며, 기능/API 동작은 바꾸지 않는다. 화면별 변경이 끝날 때마다 실제 GUI를 실행해 확인하고 다음 화면으로 넘어간다.

## 7. 최종 완료 기준

1. 어느 화면에서도 배경과 글자색이 섞여 내용을 읽지 못하는 경우가 없다.
2. 일반 텍스트, 보조 텍스트, 버튼, 선택, 포커스가 정량 대비 기준을 만족한다.
3. 모든 메뉴와 팝업에서 입력값과 행동 버튼이 명확히 보인다.
4. 사용자는 현재 위치, 현재 상태, 다음 행동을 색상 설명 없이도 알 수 있다.
5. 새 화면은 색상 리터럴이나 별도 상태표 없이 공통 토큰과 위젯만으로 구성할 수 있다.
6. 1024×700에서 핵심 기능이 잘리지 않고, 1280×720에서 불필요한 빈 장식 공간이 없다.
