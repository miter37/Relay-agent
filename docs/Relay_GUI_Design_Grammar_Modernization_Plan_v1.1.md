# Relay GUI Design Grammar Modernization Plan v1.1

> v1.0 대체본. v1.0의 방향(Orca/Codex Desktop 문법, 아이콘 액션 전환, 모노크롬 우선)은 유지하되, 착수 전에 반드시 확정해야 할 **색 대비·타이포그래피·Qt 기술 제약**을 검증된 값으로 채웠다.
> 이 문서는 **다른 사람이 이 문서만 보고 구현할 수 있는 수준**을 목표로 한다. 값이 적혀 있으면 그대로 쓰고, "결정 필요"라고 적힌 항목만 논의한다.
>
> **구현 완료 (2026-08-06).** 실제 Windows 화면 리뷰에서 발견된 사항으로 §4·§9·§10이 일부 개정되었다. 개정 내역은 §15에 있다.

---

## 1. 목적과 성공 기준

Relay GUI를 **상용 개발자 도구 수준의 차분한 다크 표면**으로 끌어올린다. 목표는 장식이 아니라 다음 다섯 가지다.

1. **타이포그래피가 의도적일 것** — 폰트 패밀리·크기·굵기·자간이 6단 스케일 안에서만 결정된다. (현재 가장 약한 부분)
2. **색이 중립적일 것** — 화면의 80% 이상이 무채색이고, 색은 상태·강조·포커스에만 쓰인다.
3. **반복 행동이 아이콘일 것** — Refresh/Edit/Delete/Run 같은 동작이 16px 아이콘 + 툴팁으로 통일된다.
4. **밀도가 일정할 것** — 행 높이, 컨트롤 높이, 여백이 토큰 하나에서 나온다.
5. **기능이 그대로일 것** — API, 시그널, 동작, 단축키는 하나도 바뀌지 않는다.

**성공 기준(측정 가능):** §13 승인 체크리스트 전 항목 통과 + `ruff` 무경고 + 전체 `unittest` 통과 + 화면 캡처 확보(**실제 플랫폼에서** — §15 참조).

---

## 2. 전제와 기술 제약 (검증 완료)

이 절의 값은 2026-08-06에 실제 환경에서 확인했다. 추측이 아니다.

| 항목 | 확인된 사실 | 구현에 미치는 영향 |
|---|---|---|
| PySide6 버전 | **6.11.1** (`pyproject.toml`: `PySide6>=6.8,<7`) | `QFont.setFamilies()`, `QFont.setFeature()` 모두 사용 가능 |
| `PySide6.QtSvg` | **import 가능** (PySide6-Essentials 포함) | 아이콘을 SVG 문자열 → `QSvgRenderer`로 렌더링해도 안전 |
| `QFont.setFamilies()` | **동작 확인** | 폰트 폴백 체인을 코드에서 정확히 지정 가능 |
| GUI 코드의 색 리터럴 | `design_tokens.py` **외 0건** | 토큰 교체만으로 전체 테마가 바뀐다. Phase A 리스크 낮음 |
| GUI 코드의 한글 문자열 | **0건** (영어 UI). 단 `relay/profiles.py`, `relay/target_workspace.py`에는 한글 존재 | UI 문자열은 영어 유지. **폰트 스택에는 한글 폴백 필수**(사용자 데이터에 한글이 들어옴) |
| 텍스트 `QPushButton` 총량 | **75개 / 11개 파일** (§5.2 인벤토리) | 롤아웃 범위의 실제 크기 |

### 2.1 Qt Style Sheet가 **지원하지 않는** 속성 (중요)

구현자가 여기서 시간을 낭비하지 않도록 명시한다. 아래는 QSS에 써도 무시된다.

- `letter-spacing`, `word-spacing` → **`QFont.setLetterSpacing()`으로 코드에서 처리**
- `line-height` → 아이템 `padding`과 레이아웃 `spacing`으로 대체
- `text-transform` → 문자열 자체를 대문자로 쓰거나 `QFont.setCapitalization()`
- `box-shadow`, `transition`, `animation`, `opacity`, `gap` → 사용 금지. 필요하면 `QGraphicsDropShadowEffect`/`QGraphicsOpacityEffect`
- `font-family` 콤마 폴백 목록 → **QSS에서 신뢰할 수 없음. 반드시 `QFont.setFamilies()` 사용**

### 2.2 QSS `font-size`와 `QFont`의 충돌 규칙

QSS의 `font-size`는 위젯에 설정된 `QFont`를 **덮어쓴다**. 두 방식을 섞으면 자간·굵기가 예측 불가능해진다. 따라서 이 프로젝트의 규칙을 다음과 같이 확정한다.

- **위젯 단위 타이포는 전부 `design_typography.apply_type(widget, role)`로만 설정한다.** (`QLabel`, `QPushButton`, `QLineEdit`, `QHeaderView`, `QTabBar` 등)
- **QSS는 색·배경·테두리·반경·패딩만 담당한다.** `font-size`/`font-weight`는 QSS에서 **전부 제거**한다.
- 예외: `QTreeWidget::item`, `QTabBar::tab`, `QHeaderView::section` 같은 **서브컨트롤 셀렉터**는 `QFont`를 받을 수 없으므로 필요한 경우에만 QSS `font-size`를 남긴다. 남기는 경우 해당 줄에 `# type-scale exception` 주석을 단다.

### 2.3 기존 테스트가 강제하는 불변 조건

`tests/test_gui_design_system.py`가 이미 다음을 검사한다. 새 토큰은 **이 검사를 통과해야 한다.**

```
contrast_ratio(text.primary,   {bg.canvas, bg.surface, bg.surfaceRaised, bg.input}) >= 4.5
contrast_ratio(text.secondary, {bg.canvas, bg.surface, bg.surfaceRaised, bg.input}) >= 4.5
contrast_ratio(text.muted,     bg.surfaceRaised)                                    >= 4.5
contrast_ratio(text.primary,   accent.primary)                                      >= 4.5   ← §6.3에서 교체
```

---

## 3. 레퍼런스 분석

### 3.1 Orca (stablyai/orca, YC 백드 오픈소스 ADE)

- 다수의 코딩 에이전트를 **git worktree로 격리**해 병렬 실행하는 ADE. 사이드바에 저장소/워크트리/PR/이슈가 상주하고, 중앙에 에이전트 터미널·diff·브라우저를 스플릿 페인으로 배치한다.
- 별도 프로젝트 `stablyai/orca-minimal-icons`("Official minimal icon theme")를 운영할 만큼 **아이콘 문법을 제품 정체성으로 취급**한다.
- 주 행동은 "Add Repo", "Create Worktree"처럼 화면당 한두 개만 강조되고, 나머지는 아이콘·컨텍스트 메뉴로 물러난다.

**Relay가 가져올 것:** 내비게이션의 지속성, 화면당 강한 행동 1개, 아이콘 세트의 일관성.

### 3.2 Codex Desktop (2026년 7월 ChatGPT 데스크톱 앱으로 통합)

- 하나의 데스크톱 셸이 Chat / Work / **Codex** 세 모드를 노출한다. Codex 모드는 diff 인라인 편집, 사이드 패널 PR 리뷰, 멀티 리포 프로젝트를 갖는 개발자 전용 표면이다.
- 문법 요약: **quiet, dense, developer-focused** — 거의 검정에 가까운 중립 표면 위 미묘한 톤 분리, 억제된 경계선, 작은 반경, 컴팩트한 컨트롤, 모노크롬 우선 위계.
- 툴바 공통 동작(새로고침·실행·중지·복사·열기·터미널 토글·IDE 토글)은 **아이콘 액션 + 호버 이름**으로 제공된다.

**⚠️ 정확성 단서 (v1.0에서 누락된 부분):** 현재 Codex 앱은 Appearance 설정에서 **베이스 테마(라이트/다크/시스템)와 서페이스·강조색을 사용자가 커스터마이즈**할 수 있다. 따라서 이 문서가 참조하는 "근검정 중립 + 절제된 청색"은 고정 사양이 아니라 **기본 다크 테마**다. 우리가 벤치마킹하는 대상은 그 기본값이다.

**Relay가 가져올 것:** 아이콘 액션 문법, 4단 표면 톤, 모노크롬 우선, 작은 반경·높은 밀도, 상태 색의 제한적 사용.

---

## 4. 디자인 원칙 (문법)

1. **차분함이 기본.** 중립 표면이 화면의 80% 이상. 색은 상태·강조·포커스에만.
2. **표면 톤은 4단.** canvas → sidebar/topbar → surface → surfaceRaised. 경계는 두 종류(subtle/strong), 포커스는 한 종류.
3. **반복 행동은 아이콘 + 툴팁.** 16px 모노크롬 아이콘, `setToolTip()` + `setAccessibleName()` 필수.
4. **주 행동은 영역당 하나.** ~~화면당 하나~~ → **주 작업 영역(region)당 하나**로 완화한다. (예: Profiles 화면은 "목록 영역"의 `New Profile`과 "편집 폼 영역"의 `Save`가 각각 primary인 것이 자연스럽다.)
5. **상태는 색만으로 전달하지 않는다.** 점/아이콘 + 텍스트 + 색.
6. **밀도와 휴식의 균형.** 행 높이 26px, 컨트롤 높이 28px, 큰 카드·과한 패딩 제거.
7. **타이포는 6단 스케일 밖으로 나가지 않는다.** (§7)
8. **기능 변경 없음.** 시그널·슬롯·public 속성명·동작·단축키 보존. 위젯 **속성 이름은 유지**한다(예: `refresh_button`은 아이콘이 되어도 이름 그대로).
9. **테마는 토큰으로.** 화면 코드에 색 리터럴·로컬 `setStyleSheet()` 금지.
10. **UI 문자열은 영어.** 현재 GUI 전체가 영어이므로 툴팁도 영어로 통일한다. (v1.0의 한글 툴팁 제안은 폐기)

---

## 5. 현재 상태 감사

### 5.1 토큰

- `design_tokens.py`는 `#0F172A` 계열 네이비-블루 "operations console" 팔레트.
- `accent.primary=#1769AA`, `accent.cyan=#6DD6F7`가 전체를 푸르스름하게 만든다.
- 반경 control 6 / panel 10, 전역 13px, 그 외 크기는 QSS에 하드코딩(22/18/15/12px).
- **`accent.cyan`은 5곳에서만 참조**된다 (`design_styles.py` 26, 74, 81, 96, 127행). 제거 비용이 낮다.

### 5.2 텍스트 버튼 전체 인벤토리 (75개)

| 파일 | 개수 | 버튼 |
|---|---|---|
| `main_window.py` | 8 | Refresh health, + Register Task, Settings, Runs, Tasks, Profiles, Projects, Routines |
| `job_detail.py` | 7 | Stop Task Run, Check progress, Run again, Schedule, Open folder, Open full log, Copy answer |
| `projects.py` | 15 | Refresh×3, New Project, Run, Edit, Delete, Add/Remove node, Add/Remove connection, Add/Remove output, Cancel run, Reexecute from node |
| `tasks.py` | 13 | Refresh×2, Register Task, Run Task, Edit×2, Delete×2, Add input, Move up, Move down, + Add files, + Add from Task Run |
| `agent_apps.py` | 8 | Run test, Cancel, Save agent, + Add agent app, Edit, Test, Enable, Delete |
| `routines.py` | 7 | Refresh×2, New Routine, Run now, Edit, Delete, Preview next occurrences |
| `schedule_detail.py` | 7 | Run now, Pause, Resume, Edit, Copy, Delete, Open output |
| `schedule_editor.py` | 3 | Preview, Cancel, Create schedule |
| `settings.py` | 3 | Enable auto-start, Run deep doctor, Verify & enable Antigravity |
| `profiles.py` | 3 | New Profile, Save, Delete |
| `runs.py` | 1 | Load more |

### 5.3 구조적 문제

| 현재 패턴 | 위치 | 문제 |
|---|---|---|
| 사이드바에 Schedules 목록 + Settings + 5개 내비 버튼 혼재 | `main_window.py:189-228` | 1차 내비게이션과 도구가 섞임. Settings가 Runs보다 위에 있음 |
| 상세 헤더 액션 6개 나열 | `job_detail.py:47-63` | 헤더가 버튼 줄로 가득 참 |
| `StatusBadge`가 색 테두리 + 텍스트뿐 | `design_widgets.py:11` | 점/아이콘 없음 → 색 의존도 높음 |
| `text.muted` 대비 3.5:1 | `design_tokens.py` | 기존 문서(`Readability_Hardening_Plan`)에서도 지적된 미해결 항목 |

---

## 6. 토큰 사양 (확정값)

### 6.1 색 — 최종 팔레트

`relay/gui/design_tokens.py`의 `COLORS`를 아래로 **전면 교체**한다.

```python
COLORS: dict[str, str] = {
    # 표면 4단
    "bg.canvas":        "#131313",
    "bg.sidebar":       "#181818",
    "bg.topbar":        "#181818",
    "bg.surface":       "#1C1C1C",
    "bg.surfaceRaised": "#242424",
    "bg.input":         "#1F1F1F",
    # 상호작용 표면 (신설 — 이게 없으면 QSS에 rgba 리터럴이 새어 들어온다)
    "bg.hover":         "#2A2A2A",
    "bg.pressed":       "#303030",
    "bg.selected":      "#26364F",
    # 경계
    "border.subtle":    "#2E2E2E",
    "border.strong":    "#3D3D3D",   # 신설: 입력 필드·구분선 강조
    "border.focus":     "#7AA2F7",
    # 텍스트
    "text.primary":     "#EDEDED",
    "text.secondary":   "#B0B0B0",
    "text.muted":       "#999999",
    # 강조 (선택·포커스·진행·활성 인디케이터)
    "accent.primary":   "#4C8DFF",
    "accent.onPrimary": "#0B0B0B",   # 신설: accent 표면 위 텍스트
    # 주 행동 버튼 (Codex/Linear 계열 밝은 중립 버튼)
    "action.primaryBg": "#EDEDED",   # 신설
    "action.primaryFg": "#131313",   # 신설
    # 상태
    "state.success":    "#5BD48A",
    "state.warning":    "#E3B341",
    "state.danger":     "#F07A75",
    "state.info":       "#79A9FF",
}
```

**제거되는 토큰:** `accent.cyan`. `design_styles.py`의 5개 참조를 다음으로 교체한다.

| 위치 | 현재 | 교체 |
|---|---|---|
| `:26` `QPalette.Link` | `accent.cyan` | `accent.primary` |
| `:74` `QPushButton:hover` border | `accent.cyan` | `border.strong` + `background: bg.hover` |
| `:81` `sidebarButton:hover/:checked` | `accent.cyan` | `text.primary` + `background: bg.hover` (활성은 §9.2의 좌측 인디케이터로) |
| `:96` `QTabBar::tab:selected` | `accent.cyan` | `text.primary` + `border-bottom: 2px solid accent.primary` |
| `:127` `QTextBrowser a` | `accent.cyan` | `accent.primary` |

### 6.2 검증된 대비표

아래 수치는 프로젝트 자체 `contrast_ratio()`로 실제 계산한 값이다. **전부 4.5:1 이상**이다.

| 전경 \ 배경 | canvas `#131313` | sidebar/topbar `#181818` | surface `#1C1C1C` | input `#1F1F1F` | surfaceRaised `#242424` | hover `#2A2A2A` | pressed `#303030` |
|---|---|---|---|---|---|---|---|
| `text.primary` #EDEDED | 15.87 | 15.17 | 14.56 | 14.08 | 13.26 | 12.26 | 11.27 |
| `text.secondary` #B0B0B0 | 8.57 | 8.19 | 7.86 | 7.60 | 7.16 | 6.62 | 6.09 |
| `text.muted` #999999 | 6.52 | 6.23 | 5.98 | 5.79 | 5.45 | 5.04 | **4.63** |

| 색 | canvas | surface | surfaceRaised |
|---|---|---|---|
| `state.success` #5BD48A | 9.94 | 9.11 | 8.30 |
| `state.warning` #E3B341 | 9.55 | 8.76 | 7.98 |
| `state.danger` #F07A75 | 6.85 | 6.29 | 5.73 |
| `state.info` #79A9FF | 7.90 | 7.25 | 6.60 |
| `accent.primary` #4C8DFF | 5.81 | 5.32 | 4.85 |
| `border.focus` #7AA2F7 | 7.38 | 6.77 | 6.16 |

| 조합 | 비율 | 판정 |
|---|---|---|
| `accent.onPrimary` on `accent.primary` | **6.15** | 통과 |
| `action.primaryFg` on `action.primaryBg` | **15.87** | 통과 |
| ~~`text.primary` on `accent.primary`~~ | **2.73** | **실패 — 절대 이 조합을 쓰지 말 것** |
| `text.primary` on `bg.selected` | 10.41 | 통과 |

### 6.3 기존 테스트 수정 (필수)

`tests/test_gui_design_system.py:58`

```python
# 변경 전 — 새 팔레트에서 2.73으로 실패한다
self.assertGreaterEqual(contrast_ratio(COLORS["text.primary"], COLORS["accent.primary"]), 4.5)

# 변경 후
self.assertGreaterEqual(contrast_ratio(COLORS["accent.onPrimary"], COLORS["accent.primary"]), 4.5)
self.assertGreaterEqual(contrast_ratio(COLORS["action.primaryFg"], COLORS["action.primaryBg"]), 4.5)
```

`:54-57`의 루프에 `bg.hover`, `bg.pressed`를 추가하고, `text.muted`도 전 표면에 대해 4.5:1을 요구하도록 강화한다.

### 6.4 형태·간격

```python
SPACING = {"xxs": 2, "xs": 4, "sm": 8, "md": 12, "lg": 16, "xl": 24, "xxl": 32}   # xxs 신설
RADIUS  = {"badge": 4, "control": 5, "panel": 8}                                   # 6→5, 10→8, badge 신설

METRICS = {                     # 신설 — 밀도를 코드 한 곳에서 통제
    "controlHeight": 28,        # 버튼·입력 필드 최소 높이
    "iconButton": 28,           # 아이콘 버튼 정사각 크기
    "iconSize": 16,             # 아이콘 픽셀 크기
    "navIconSize": 18,          # 사이드바 아이콘
    "rowHeight": 26,            # 트리/리스트/테이블 행
    "rowPadding": 5,            # 현재 SPACING["sm"]=8 → 5
    "topBarHeight": 48,
    "sidebarWidth": 200,
}
```

---

## 7. 타이포그래피 사양 (신규 — v1.0에서 가장 부실했던 절)

### 7.1 새 모듈 `relay/gui/design_typography.py`

```python
"""Relay의 타이포 스케일. 위젯 폰트는 여기서만 결정된다."""
from __future__ import annotations
from dataclasses import dataclass
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QWidget

UI_FAMILIES = [
    "Segoe UI Variable Text",   # Windows 11 기본
    "Segoe UI",                 # Windows 10
    "SF Pro Text",              # macOS
    "Inter",
    "Noto Sans",
    "DejaVu Sans",              # Linux 폴백
    # 한글 폴백 — 사용자 데이터에 한글이 들어온다 (relay/profiles.py 참조)
    "Malgun Gothic",            # Windows
    "Apple SD Gothic Neo",      # macOS
    "Noto Sans KR",             # Linux
]
MONO_FAMILIES = [
    "Cascadia Mono", "Consolas", "SF Mono", "Menlo",
    "JetBrains Mono", "DejaVu Sans Mono",
    "D2Coding", "Malgun Gothic",
]

@dataclass(frozen=True)
class TypeRole:
    size: int          # px
    weight: int        # QFont.Weight 정수값
    tracking: float    # letter-spacing, px (AbsoluteSpacing)
    mono: bool = False
    uppercase: bool = False

TYPE_SCALE: dict[str, TypeRole] = {
    "title.page":    TypeRole(20, QFont.DemiBold, -0.2),
    "title.detail":  TypeRole(16, QFont.DemiBold, -0.1),
    "title.section": TypeRole(13, QFont.DemiBold,  0.0),
    "body":          TypeRole(13, QFont.Normal,    0.0),
    "body.strong":   TypeRole(13, QFont.Medium,    0.0),
    "caption":       TypeRole(12, QFont.Normal,    0.0),
    "overline":      TypeRole(11, QFont.DemiBold,  0.6, uppercase=True),
    "mono":          TypeRole(12, QFont.Normal,    0.0, mono=True),
}

def application_font() -> QFont: ...      # QApplication.setFont()에 넘길 기본 폰트(body)
def font_for(role: str) -> QFont: ...     # 캐시된 QFont 반환
def apply_type(widget: QWidget, role: str) -> None: ...   # widget.setFont(font_for(role))
```

구현 요점:

- `QFont.setFamilies(UI_FAMILIES)` — **`setFamily()`(단수)가 아니다.** 단수는 폴백이 없다.
- 크기는 `setPixelSize()`로 지정한다. `setPointSize()`는 OS DPI 설정에 따라 두 번 스케일된다.
- 자간은 `font.setLetterSpacing(QFont.AbsoluteSpacing, role.tracking)`.
- 대문자는 `font.setCapitalization(QFont.AllUppercase)` — 문자열 자체는 건드리지 않는다(테스트가 `.text()`를 검사하므로).
- **숫자 정렬(선택, 권장):** 표·타임스탬프 라벨에 `font.setFeature("tnum", 1)` (Qt 6.7+, 6.11에서 사용 가능)으로 tabular figures를 켜면 시간·카운트 열이 흔들리지 않는다.
- `font_for()`는 `dict` 캐시를 쓴다. 위젯마다 `QFont`를 새로 만들면 대형 트리에서 느려진다.

### 7.2 역할 배정표

| 역할 | 크기/굵기/자간 | 적용 대상 | 현재 값 |
|---|---|---|---|
| `title.page` | 20 / 600 / −0.2 | `#pageTitle` (top bar 페이지 제목), `MetricCard.value_label` | 22 / 600 |
| `title.detail` | 16 / 600 / −0.1 | `#detailTitle`, `job_detail.title_label`, 각 상세 뷰 `title_label` | 18 / 600 |
| `title.section` | 13 / 600 / 0 | `#sectionTitle`, `SectionHeader`, `QGroupBox::title`, `EmptyState` 제목 | 15 / 600 |
| `body` | 13 / 400 / 0 | 기본. `QApplication.setFont()` | 13 / 400 |
| `body.strong` | 13 / 500 / 0 | 선택된 행, 활성 내비 라벨, 다이얼로그 primary 버튼 | 없음 |
| `caption` | 12 / 400 / 0 | `#mutedText`, 폼 라벨, 헬프 텍스트, 타임스탬프 | 12 / 400 |
| `overline` | 11 / 600 / +0.6 / UPPER | `StatusBadge`, `QHeaderView::section`, 사이드바 그룹 헤더, 카운트 배지 | 없음 |
| `mono` | 12 / 400 / 0 / mono | 로그 `QTextBrowser`, JSON/Result 패널, ID 표기 | 없음(기본 폰트로 렌더링 중) |

**주의:** `MetricCard.value_label`이 현재 `objectName="pageTitle"`을 재사용하고 있다(`design_widgets.py:62`). 의미가 다르므로 `apply_type(self.value_label, "title.page")`로 바꾸고 objectName은 `metricValue`로 분리한다.

### 7.3 QSS에서 제거할 폰트 선언

`design_styles.py`에서 아래 줄의 `font-size`/`font-weight`를 **삭제**하고 `apply_type()`으로 옮긴다.

`:44`, `:45`(전역 13px → `QApplication.setFont()`), `:56`(pageTitle), `:57`(detailTitle), `:58`(sectionTitle), `:59`(mutedText), `:66`(healthBadge), `:76`(primaryAction), `:99`(statusBadge)

남겨도 되는 예외(서브컨트롤): `QHeaderView::section`, `QTabBar::tab`, `QMenu::item` — 각각 `# type-scale exception` 주석 필수.

---

## 8. 아이콘 시스템

### 8.1 새 모듈 `relay/gui/design_icons.py`

```python
"""토큰 색으로 렌더링되는 내장 16px 스트로크 아이콘 세트."""
ICON_PATHS: dict[str, str] = {  # 24x24 viewBox 기준 SVG path 데이터
    "refresh": "...", "play": "...", "stop": "...", "pause": "...",
    ...
}

def icon(name: str, tone: str = "default") -> QIcon:
    """tone: default | accent | danger | muted

    Normal/Active/Disabled 세 모드의 픽스맵을 모두 담은 QIcon을 캐시해 반환한다.
      Normal   → text.secondary (tone별 기본색)
      Active   → text.primary   (hover)
      Disabled → text.muted
    """
```

구현 요점:

- SVG 문자열에 `{stroke}` 플레이스홀더를 두고 색을 치환한 뒤 `QSvgRenderer`로 `QImage`에 렌더링한다. (§2 QtSvg 가용 확인 완료)
- **고DPI:** `pixmap = QPixmap(size * dpr)` 로 렌더링하고 `pixmap.setDevicePixelRatio(dpr)`를 호출한다. 이 두 줄이 없으면 125%/150% 배율에서 아이콘이 뭉갠다.
- `(name, tone, size, dpr)` 키로 캐시한다.
- 알 수 없는 이름이 오면 **조용히 빈 아이콘을 반환하지 말고 `KeyError`를 던진다.** 오타가 런타임에 조용히 사라지면 안 된다.

### 8.2 아이콘 목록 (필수 32종)

| 그룹 | 이름 |
|---|---|
| 실행 제어 | `play`, `stop`, `pause`, `rerun`, `activity`(진행 확인) |
| 편집 | `plus`, `minus`, `pencil`, `trash`, `copy`, `arrow-up`, `arrow-down` |
| 열기 | `folder-open`, `file-text`(로그), `external-link` |
| 스케줄 | `clock`, `calendar`, `repeat` |
| 상태 | `dot`, `check-circle`, `alert-triangle`, `x-circle`, `info` |
| 도구 | `refresh`, `search`, `filter`, `power`(enable), `beaker`(test), `chevron-down`, `chevron-right`, `x` |
| 내비 | `list`(Runs), `checklist`(Tasks), `user`(Profiles), `folder-tree`(Projects), `repeat`(Routines), `gear`(Settings) |

같은 의미에는 **반드시 같은 아이콘**을 쓴다(§13 승인 기준 3).

### 8.3 공통 위젯 (`design_widgets.py`에 추가)

```python
class IconButton(QPushButton):
    """아이콘만 표시하는 정사각 액션 버튼."""
    def __init__(self, icon_name: str, tooltip: str, *, tone="default", parent=None):
        super().__init__(parent)
        self.setObjectName("iconAction")
        self.setProperty("tone", tone)          # QSS 셀렉터용: default|accent|danger
        self.setIcon(icon(icon_name, tone))
        self.setIconSize(QSize(METRICS["iconSize"], METRICS["iconSize"]))
        self.setFixedSize(METRICS["iconButton"], METRICS["iconButton"])
        self.setToolTip(tooltip)
        self.setAccessibleName(tooltip)         # ← 스크린리더용. 누락 금지
        self.setCursor(Qt.PointingHandCursor)

class NavButton(QPushButton):
    """사이드바 1차 내비게이션 항목: 아이콘 18px + 라벨, 좌측 활성 인디케이터."""

class LabeledButton(QPushButton):
    """아이콘 + 텍스트 조합. primary/secondary 톤 지원."""
```

**툴팁 문구 규칙:** 영어, 동사 원형으로 시작, 마침표 없음, 40자 이내. 예: `"Refresh the Task list"`, `"Stop this Task Run"`, `"Open the output folder"`.

**툴팁 지연:** `QApplication.setStyle()` 이후 `app.setEffectEnabled()`로는 조절되지 않는다. `QToolTip`의 기본 지연을 바꾸려면 위젯에 이벤트 필터가 필요하므로, **v1.1에서는 Qt 기본 지연을 그대로 쓴다.** (v1.0의 "400ms" 요구는 구현 비용 대비 가치가 낮아 폐기)

---

## 9. 컴포넌트 문법

### 9.1 버튼 3계층 — 어떤 버튼이 어느 계층인지 판단하는 규칙

| 계층 | objectName | 형태 | 판단 기준 |
|---|---|---|---|
| **Primary** | `primaryAction` | `action.primaryBg` 배경 + `action.primaryFg` 글자, 텍스트(선택적 선행 아이콘) | 이 영역에서 사용자가 하려는 **주 작업**. 영역당 1개 |
| **Secondary** | (기본) | 투명 배경 + `border.subtle` 테두리 + `text.primary` | 의미가 아이콘으로 명확히 전달되지 않는 행동 (`Run deep doctor`, `Load more`, `Reexecute from node`) |
| **Icon** | `iconAction` | 배경 없음, hover 시 `bg.hover`, 28×28 | **반복적이고 의미가 보편적인** 행동 (Refresh, Edit, Delete, Run, Stop, Copy, Open) |

**아이콘으로 만들지 말아야 할 것:** 되돌리기 어려운 데 이름이 없으면 뜻을 모르는 행동. 위 표의 Secondary 예시가 그 경우다. `Delete`는 아이콘으로 하되 **확인 다이얼로그를 반드시 유지**한다(현재 동작 보존).

### 9.2 사이드바

현재(`main_window.py:189-228`)는 Schedules 목록 → Settings → Runs → Tasks → Profiles → Projects → Routines 순서다. 다음으로 재배치한다.

```
┌─ sidebarNav (200px) ────────┐
│  ○ Runs          ← NavButton │   1차 내비게이션 (아이콘 18px + 라벨 13px)
│  ○ Tasks                     │   활성: 좌측 2px accent.primary 인디케이터
│  ○ Profiles                  │        + bg.hover 배경 + body.strong 라벨
│  ○ Projects                  │
│  ○ Routines                  │
│                              │
│  SCHEDULES        ← overline │   접이식 그룹 (11px UPPER, text.muted)
│    Daily report ×            │
│    Weekly sync               │
│                              │
│  ─────────── (stretch) ───── │
│  ⚙ Settings                  │   최하단 고정
└──────────────────────────────┘
```

- `settings_button`을 **레이아웃 최하단으로 이동**한다(`addStretch(1)` 뒤). 위젯 속성명·시그널은 유지.
- `schedule_list`는 `overline` 헤더를 가진 그룹으로 감싼다. `setMaximumHeight(150)`은 유지.
- 활성 인디케이터는 QSS에서 `border-left: 2px solid` + 비활성 시 `border-left: 2px solid transparent`로 구현한다(폭이 변하지 않도록).

### 9.3 상단 바

- 높이 `METRICS["topBarHeight"]`(48px) 고정.
- 좌: `Relay` 브랜드(`caption`, `text.muted`) + 페이지 제목(`title.page`).
- 우: 헬스 점(6px, 상태색) + 상태 단어(`caption`) + 마지막 확인 시각(`caption`, `text.muted`) + `IconButton("refresh")` + primary 버튼.
- `health_label`의 텍스트 형식(`"Health: Healthy"`, `"Unhealthy: claude"`)은 **변경 금지** — `tests/test_g1_gui.py:49,66,81`이 이 문자열을 검사한다. 점은 별도 위젯으로 **추가**한다.

### 9.4 상태 표현

`StatusBadge`를 점 + 라벨 구조로 확장한다. **단, `QLabel` 상속과 `.text()` 반환값은 유지한다** — `tests/test_gui_design_system.py:33`이 `badge.text() == "Running"`을 검사한다.

구현 방법: `QLabel`을 유지하고 좌측 점을 `paintEvent` 오버라이드 또는 `setPixmap`이 아닌 **텍스트 앞 여백 + QSS `border-left: 6px`**로 처리한다. 가장 단순한 안전책:

```python
# QSS: QLabel#statusBadge { padding-left: 14px; border-left: 3px solid <state color>; }
# 색은 [state="..."] 속성 셀렉터로. text()는 그대로 "Running".
```

배지 폰트는 `overline`(11px/600/UPPER). 배경은 `bg.surface`, 테두리는 제거하고 좌측 색 막대만 남긴다.

### 9.5 밀도

`design_styles.py`에서:

- `QTreeWidget::item, QListWidget::item, QTableWidget::item` padding `SPACING["sm"]`(8) → `METRICS["rowPadding"]`(5), `min-height: 26px` 추가.
- 입력 위젯·버튼에 `min-height: 28px` 추가.
- 포커스 링을 `border: 2px`(`:87`) → **`border: 1px solid border.focus` + `outline: 1px solid border.focus`가 불가하므로, 1px 테두리 + `bg.selected` 배경 조합**으로 바꾼다. 현재의 2px는 포커스 시 레이아웃이 1px 밀린다.
  → 대안이자 권장: 평상시에도 `border: 1px solid border.subtle`을 유지하고 포커스 시 **색만** `border.focus`로 바꾼다. 두께 변화 없음.

### 9.6 폼·다이얼로그·빈 상태

- 폼 라벨: `caption` + `text.secondary`.
- 다이얼로그 footer 순서: `Cancel`(secondary) → `Submit`(primary). 현재 `schedule_editor.py:135,138`, `agent_apps.py:117,120`이 이미 이 순서다. 유지.
- 빈 상태: 선 아이콘(48px, `text.muted`) + 제목(`title.section`) + 설명(`caption`) + 행동 1개.
- 경고·에러는 `InlineNotice`만 사용. 색 리터럴 금지.

---

## 10. 화면별 전환 매핑 (75개 버튼 전수)

표기: **[I]** = `IconButton`(아이콘+툴팁), **[P]** = primary 텍스트 버튼, **[S]** = secondary 텍스트 버튼, **[N]** = `NavButton`

### `main_window.py`
| 현재 | 처리 | 아이콘 / 툴팁 |
|---|---|---|
| `Refresh health` | **[I]** | `refresh` / "Refresh daemon health" |
| `+ Register Task` | **[P]** | `plus` 선행 아이콘 + 텍스트 `Register Task` ※ §12 테스트 수정 |
| `Runs`/`Tasks`/`Profiles`/`Projects`/`Routines` | **[N]** | `list`/`checklist`/`user`/`folder-tree`/`repeat` |
| `Settings` | **[N]** | `gear`, 최하단 배치 |

### `job_detail.py` — 헤더 액션 6개를 우측 아이콘 그룹으로
| 현재 | 처리 | 아이콘 / 툴팁 |
|---|---|---|
| `Stop Task Run` | **[I]** danger | `stop` / "Stop this Task Run" |
| `Check progress` | **[I]** | `activity` / "Check progress now" |
| `Run again` | **[I]** accent | `rerun` / "Run again with the same inputs" |
| `Schedule` | **[I]** | `clock` / "Create a Schedule from this Run" |
| `Open folder` | **[I]** | `folder-open` / "Open the output folder" |
| `Open full log` | **[I]** | `file-text` / "Open the full log file" |
| `Copy answer` | **[I]** | `copy` / "Copy the answer" |

※ `rerun_button`의 objectName이 현재 `primaryAction`이다(`:55`). 아이콘화 시 `iconAction` + `tone="accent"`로 바꾼다.

### `tasks.py`
| 현재 | 처리 | 아이콘 / 툴팁 |
|---|---|---|
| `Refresh` ×2 (`:279`, `:366`) | **[I]** | `refresh` / "Refresh" |
| `Register Task` (`:282`) | **[P]** | 텍스트 유지 ※ 테스트 검사 중 |
| `Run Task` (`:369`) | **[I]** accent | `play` / "Run this Task" |
| `Edit` (`:373`) | **[I]** | `pencil` / "Edit this Task" |
| `Delete` (`:376`) | **[I]** danger | `trash` / "Delete this Task" |
| `Add input`/`Edit`/`Delete`/`Move up`/`Move down` (`:177-181`) | **[I]** | `plus`/`pencil`/`trash`/`arrow-up`/`arrow-down` |
| `+ Add files` (`:674`) | **[S]** | `plus` 선행 + 텍스트 `Add files` |
| `+ Add from Task Run` (`:677`) | **[S]** | `plus` 선행 + 텍스트 ※ 테스트 검사 중 |

### `projects.py`
| 현재 | 처리 | 아이콘 / 툴팁 |
|---|---|---|
| `Refresh` ×3 (`:63`,`:137`,`:569`) | **[I]** | `refresh` |
| `New Project` (`:66`) | **[P]** | `plus` + 텍스트 |
| `Run` (`:140`) | **[I]** accent | `play` / "Run this Project" |
| `Edit`/`Delete` (`:143`,`:146`) | **[I]** / **[I]** danger | `pencil` / `trash` |
| `Add node`/`Remove node` (`:276`,`:278`) | **[I]** | `plus` / `minus` |
| `Add connection`/`Remove connection` (`:291`,`:293`) | **[I]** | `plus` / `minus` |
| `Add output`/`Remove output` (`:306`,`:308`) | **[I]** | `plus` / `minus` |
| `Cancel run` (`:574`) | **[I]** danger | `stop` / "Cancel this Project Run" |
| `Reexecute from node` (`:590`) | **[S]** | 텍스트 유지 — 아이콘으로 의미 전달 불가 |

### `routines.py`
| 현재 | 처리 | 아이콘 / 툴팁 |
|---|---|---|
| `Refresh` ×2 | **[I]** | `refresh` |
| `New Routine` | **[P]** | `plus` + 텍스트 |
| `Run now` | **[I]** accent | `play` / "Run this Routine now" |
| `Edit`/`Delete` | **[I]** / **[I]** danger | `pencil` / `trash` |
| `Preview next occurrences` | **[S]** | 텍스트 유지 |

### `schedule_detail.py`
| 현재 | 처리 | 아이콘 / 툴팁 |
|---|---|---|
| `Run now` | **[I]** accent | `play` |
| `Pause` / `Resume` | **[I]** | `pause` / `play` — **두 버튼 유지**(기능 변경 금지) |
| `Edit` | **[I]** | `pencil` |
| `Copy` | **[I]** | `copy` / "Duplicate this Schedule" |
| `Delete` | **[I]** danger | `trash` |
| `Open output` | **[I]** | `folder-open` |

### `agent_apps.py`
| 현재 | 처리 | 비고 |
|---|---|---|
| `+ Add agent app` | **[P]** | `plus` + 텍스트 |
| `Edit` / `Delete` | **[I]** / **[I]** danger | `pencil` / `trash` |
| `Test` | **[I]** | `beaker` / "Run a capability test" |
| `Enable` | **[I]** | `power` / "Enable this Agent App" — **확인 다이얼로그 유지 필수**(보안 감사 연계) |
| `Run test` / `Cancel` / `Save agent` | **[S]** / **[S]** / **[P]** | 다이얼로그 footer, 텍스트 유지 |

### `profiles.py`, `runs.py`, `settings.py`, `schedule_editor.py`
| 현재 | 처리 | 비고 |
|---|---|---|
| `New Profile` | **[P]** | 목록 영역 primary |
| `Save` | **[P]** | 편집 폼 영역 primary (§4 원칙 4 완화 적용) |
| `Delete` (profiles) | **[I]** danger | `trash` |
| `Load more` (runs) | **[S]** | 텍스트 유지 |
| `Enable auto-start` / `Run deep doctor` / `Verify & enable Antigravity` | **[S]** ×3 | 상태 토글·설명적 라벨. **전부 텍스트 유지** ※ 테스트 검사 중 |
| `Preview` / `Cancel` / `Create schedule` | **[S]** / **[S]** / **[P]** | 다이얼로그 footer |

**합계:** 아이콘 45 · primary 8 · secondary 16 · nav 6 = 75

---

## 11. 구현 순서

각 Phase는 **독립적으로 커밋 가능하고, 그 시점에 앱이 정상 실행되어야 한다.**

### Phase A — 토큰과 타이포 기반

**변경 파일:** `design_tokens.py`, `design_typography.py`(신설), `design_styles.py`, `app.py`, `design_widgets.py`, `tests/test_gui_design_system.py`

1. `COLORS`를 §6.1로 교체, `SPACING`/`RADIUS`/`METRICS`를 §6.4로 교체.
2. `design_typography.py`를 §7.1대로 신설.
3. `app.py`에서 `app.setFont(application_font())` 호출 추가 (`setStyleSheet` 호출 **앞**).
4. `design_styles.py`에서 `font-size`/`font-weight` 제거(§7.3), `accent.cyan` 5개 참조 교체(§6.1), 밀도 규칙 적용(§9.5).
5. `design_widgets.py`의 각 위젯에 `apply_type()` 적용.
6. `tests/test_gui_design_system.py`를 §6.3대로 수정 + 타이포 테스트 추가.

**완료 조건**
- `py -m unittest tests.test_gui_design_system -v` 통과
- 새 테스트: `TYPE_SCALE`의 모든 역할이 `font_for()`에서 유효한 `QFont`를 반환하고, `families()[0]`이 빈 문자열이 아님
- 새 테스트: `application_stylesheet()`에 `"font-size"`가 §7.3 예외 3곳 외에는 없음
- 앱 실행 시 시각적으로 중립 다크로 전환됨(캡처 1장)

### Phase B — 아이콘 시스템

**변경 파일:** `design_icons.py`(신설), `design_widgets.py`, `design_styles.py`, `tests/test_gui_icons.py`(신설)

1. §8.2의 32종 아이콘 path 정의.
2. `icon()` 함수 + DPI 대응 + 캐시.
3. `IconButton` / `NavButton` / `LabeledButton` 구현.
4. QSS에 `QPushButton#iconAction` 규칙 추가 (배경 투명 / `:hover` `bg.hover` / `:pressed` `bg.pressed` / `[tone="danger"]:hover` 배경에 danger 틴트 / `:focus` 1px `border.focus`).

**완료 조건**
- 새 테스트: `ICON_PATHS`의 모든 키에 대해 `icon(name).pixmap(16,16).isNull() is False`
- 새 테스트: 알 수 없는 이름은 `KeyError`
- 새 테스트: `IconButton`이 항상 비어 있지 않은 `toolTip()`과 `accessibleName()`을 가짐

### Phase C — Shell (상단 바 + 사이드바)

**변경 파일:** `main_window.py`, `tests/test_g1_gui.py`, `tests/test_gui_design_system.py`

1. 상단 바를 §9.3으로 재구성. `health_label` 문자열 형식 보존.
2. 사이드바를 §9.2로 재배치(Settings 최하단, Schedules 그룹 분리, `NavButton` 전환).
3. `register_task_button`, `health_refresh_button` 전환.

**완료 조건**
- `py -m unittest tests.test_g1_gui tests.test_g4_schedule_gui -v` 통과
- 오프스크린 캡처에서 활성 내비 인디케이터·아이콘·툴팁 확인
- **시그널 연결 회귀 없음:** `_show_runs/_show_tasks/_show_profiles/_show_projects/_show_routines/_show_settings` 전부 동작

### Phase D — 화면별 롤아웃

순서: **Runs → Run detail → Tasks → Profiles → Projects → Routines → Schedules → Settings → Agent Apps**

각 화면마다 동일 절차:
1. §10 매핑표대로 버튼 교체. **속성명·시그널·`setVisible`/`setEnabled` 로직은 그대로 둔다.**
2. 헤더를 `SectionHeader`(제목 + 카운트 `caption` + 우측 액션 그룹)로 통일.
3. 빈 상태를 `EmptyState`로 통일.
4. 해당 화면 테스트 실행 → §12에 해당하면 테스트 수정.
5. 오프스크린 캡처 1장.

**화면별 완료 조건:** 해당 테스트 파일 통과 + 캡처 확보 + 그 화면에 색 리터럴·로컬 `setStyleSheet` 0건.

### Phase E — 통합 검증

1. `scripts/capture_gui_screens.py` 신설: 9개 화면 × 2해상도(1280×720, 1024×700) 오프스크린 캡처를 `docs/assets/`에 저장.
2. 키보드: Tab 순회로 모든 아이콘 버튼에 도달 가능하고 포커스 링이 보이는지 확인.
3. DPI 100/125/150%에서 아이콘·폰트 확인 (`QT_SCALE_FACTOR=1.25` 등).
4. 검증 명령:
   ```
   py -m ruff check relay tests
   py -m unittest discover -s tests
   py build_release.py
   ```

---

## 12. 테스트 마이그레이션 목록 (정확한 위치)

### 12.1 반드시 깨지는 것 — 수정 필요

| 파일:라인 | 현재 단언 | 조치 |
|---|---|---|
| `test_gui_design_system.py:58` | `contrast_ratio(text.primary, accent.primary) >= 4.5` | §6.3대로 `accent.onPrimary`/`action.primaryFg`로 교체 |
| `test_g1_gui.py:50` | `register_task_button.text() == "+ Register Task"` | `"Register Task"`로(선행 `+`는 아이콘이 됨) |
| `test_g2_gui.py:102` | `add_from_run_button.text() == "+ Add from Task Run"` | `"Add from Task Run"`로 |
| `test_gui_design_system.py:54-58` | 대비 루프 | `bg.hover`/`bg.pressed` 추가, `text.muted` 전 표면 검사 |

### 12.2 깨지지 않지만 확인할 것

| 파일:라인 | 단언 | 왜 안전한가 |
|---|---|---|
| `test_gui_design_system.py:33` | `badge.text() == "Running"` | §9.4에서 `.text()` 반환값 보존 |
| `test_gui_design_system.py:43` | `action_button.text() == "New Task"` | `EmptyState` 행동은 텍스트 유지 |
| `test_gui_design_system.py:51` | `"QPushButton#primaryAction" in stylesheet` | 셀렉터 유지 |
| `test_g1_gui.py:49,66,81` | `health_label.text()` 형식 | §9.3에서 문자열 형식 보존 |
| `test_phase3_gui.py:38` | `create_button.text() == "Register Task"` | primary 텍스트 유지 |
| `test_g4_settings.py:29` | `autostart_button.text() == "Enable auto-start"` | secondary 텍스트 유지 |
| `test_g4_settings.py:49,63` | `"Checking"/"Running" in button.text()` | 동적 텍스트, 텍스트 버튼 유지 |
| `test_phase3/4/5_gui.py` `title_label.text()` | 제목 문자열 | 라벨 텍스트 미변경 |

### 12.3 새로 추가할 테스트

- `tests/test_gui_icons.py` — Phase B 완료 조건 3건
- `test_gui_design_system.py`에 추가:
  - 모든 `TYPE_SCALE` 역할이 유효한 `QFont` 반환
  - QSS에 `font-size`가 예외 3곳 외 없음
  - QSS에 `letter-spacing`/`line-height`/`box-shadow`/`transition`이 **없음**(Qt 미지원 속성 유입 차단)
  - `MainWindow`의 모든 `IconButton` 자손이 `toolTip()`과 `accessibleName()`을 가짐

---

## 13. 승인 체크리스트

구현 완료 판정은 아래 전부가 참일 때만 내린다.

1. ☐ `relay/gui/` 안에 색 리터럴(`#RRGGBB`)이 `design_tokens.py` 외 **0건**
2. ☐ `relay/gui/` 안에 `setStyleSheet(` 호출이 `app.py` 외 **0건**
3. ☐ 모든 `IconButton`이 비어 있지 않은 `toolTip()` + `accessibleName()` 보유
4. ☐ 같은 동작이 모든 화면에서 **같은 아이콘 이름과 같은 툴팁 문구** 사용 (Refresh, Edit, Delete, Run, Stop, Copy, Open folder)
5. ☐ 위젯 폰트가 `apply_type()` 밖에서 설정된 곳 없음 (§7.3 예외 3곳 제외)
6. ☐ §6.2 대비표의 모든 조합이 테스트로 강제됨
7. ☐ 화면당(영역당) primary 버튼 1개 원칙 준수
8. ☐ 위험 행동(Delete, Enable Agent App, Cancel run)의 확인 다이얼로그가 **전부 유지**됨
9. ☐ 시그널·슬롯·public 속성명·단축키 변경 **0건**
10. ☐ `ruff check` 무경고, `unittest discover` 전체 통과, `build_release.py` 성공
11. ☐ 9개 화면 × 2해상도 캡처 확보, DPI 100/125/150% 육안 확인 완료

---

## 14. 결정이 필요한 열린 항목

착수 전에 답이 필요한 것은 **두 개뿐**이다. 나머지는 이 문서에서 확정했다.

| # | 항목 | 선택지 | 권장 |
|---|---|---|---|
| 1 | 주 행동 버튼의 색 | (a) 밝은 중립 `#EDEDED` 배경 + 검정 글씨 (Codex/Linear 계열) — 대비 15.87 <br> (b) 절제된 청색 `#4C8DFF` 배경 + `#0B0B0B` 글씨 — 대비 6.15 | **(a)** — 더 현대적이고 강조색을 상태 표시에만 남길 수 있다. 이 문서는 (a)를 기준으로 작성됨 |
| 2 | 라이트 테마 | (a) 지금은 다크만 <br> (b) 토큰 구조를 라이트까지 열어둠 | **(a)** — 단, `design_tokens.py`가 이미 "future light theme"을 전제로 작성되어 있으므로 `COLORS`를 함수(`palette(mode)`)로 감싸는 리팩터는 하지 않고 dict 교체만 한다 |

### 이 문서에서 v1.0으로부터 **폐기한** 항목

- 한글 툴팁("새로고침") → 영어로 통일 (§4 원칙 10)
- 반경 "4~5px" 같은 범위 표기 → `control: 5` 확정 (§6.4)
- `text.muted = #707070` → **자체 대비 테스트를 통과하지 못함**(2.86~3.62:1). `#999999`로 교체 (§6.1)
- `accent.primary`에 `text.primary`를 얹는 안 → 2.73:1로 실패. `accent.onPrimary` 신설 (§6.2)
- 툴팁 표시 지연 400ms → Qt에서 위젯별 이벤트 필터가 필요해 비용 대비 가치 낮음. 기본값 사용 (§8.3)
- "화면당 primary 1개" → "영역당 1개"로 완화 (§4 원칙 4)

---

## 15. 실제 화면 리뷰 후 개정 (2026-08-06)

Phase A~E 구현 뒤 **실제 Windows Qt 플랫폼**에서 6개 화면을 렌더링해 검토한 결과 아래를 개정했다.
(오프스크린 Qt 플랫폼은 이 샌드박스에 폰트 백엔드가 없어 `QFontDatabase.families()`가 비어 있고 모든 글자가 두부 상자로 렌더링된다. 시각 검증은 **반드시 실제 플랫폼**에서 해야 한다.)

### 15.1 문법 개정

| # | 항목 | v1.1 최초안 | 개정 | 이유 |
|---|---|---|---|---|
| 1 | 목록/셸의 등록 행동 | `Register Task`·`New Project`·`New Routine`·`New Profile`·`Add agent app`를 **primary 텍스트 버튼**으로 유지 | 전부 **`plus` IconButton(`tone="accent"`)** | 사용자 요구. 부수 효과로 좁은 목록 컬럼(약 280px)에서 제목·카운트·버튼이 서로 잘리던 문제가 사라짐 |
| 2 | 상단 바 구성 | `Relay` 워드마크 + 섹션명 + 상태 + primary 버튼 | **섹션명 + 상태 + 아이콘 액션** (워드마크 제거) | 사이드바가 이미 활성 섹션을 표시해 "Relay + 섹션명" 병기가 중복이고 어색함. 브랜드는 창 제목이 담당 |
| 3 | 헬스 표시 | `healthBadge`에 테두리 + 배경 (§9.3) | **테두리·배경 제거, 색 점 + 평문**(`healthDot` 신설) | 배지가 버튼처럼 보였음. 상태는 컨트롤이 아니다 |
| 4 | 목록 카운트 | 헤더 행에 제목·아이콘과 나란히 배치 | **헤더 아래 자기 행으로 분리** | 좁은 컬럼에서 제목과 카운트가 동시에 잘렸음 |
| 5 | 섹션 제목 | 각 뷰가 `<h2>` 제목을 따로 보유 | 뷰 레벨 `<h2>` **제거** (상단 바 + 목록 컬럼 제목만) | Runs/Projects/Routines에서 같은 단어가 2~3회 반복됐음 |

### 15.2 Qt 제약 추가 발견 — §2.1에 이어서

**QSS로 서브컨트롤을 덮어쓰면 스타일이 그리던 글리프가 사라진다.** 대체 글리프는 `image: url(...)`, 즉 **디스크 상의 이미지 파일**로만 넣을 수 있어 토큰 기반 테마와 맞지 않는다. 따라서 아래 두 규칙은 **정의하지 않는 것이 정답**이다.

| 셀렉터 | 덮어썼을 때 증상 | 조치 |
|---|---|---|
| `QComboBox::drop-down` | 드롭다운 화살표가 통째로 사라져 콤보가 빈 입력칸처럼 보임 | 규칙 삭제 → 네이티브 화살표 사용 (다크 팔레트에서 정상 렌더링 확인) |
| `QCheckBox::indicator`, `QRadioButton::indicator` | 체크 표시가 사라져 체크된 상태가 **단색 사각형**으로만 보이고 해제 상태와 구분 불가 | 규칙 삭제 → 네이티브 체크 글리프 사용 |

### 15.3 개별 결함 수정

| 파일 | 결함 | 수정 |
|---|---|---|
| `design_icons.py` | `onPrimary` 톤의 disabled 색이 `action.primaryFg`(#131313) → 비활성 primary 버튼의 어두운 표면 위에서 아이콘이 보이지 않음 | disabled를 `text.muted`로 |
| `design_icons.py` | `rerun` 아이콘 경로가 뭉개져 판독 불가 | `refresh`와 같은 계열의 단순 원형 화살표로 교체 |
| `job_detail.py` | Run 미선택 상태에서 Stop/Check/Rerun/Schedule/Open folder 아이콘이 모두 보임 | 생성 시 숨김. `set_job`이 Run의 `actions`에 따라 다시 노출 |
| `settings.py` | `"Verify & enable Antigravity"`의 `&`가 Qt 니모닉으로 소비돼 **"Verify _enable"**로 렌더링 | `&&`로 이스케이프 (4곳) |
| `settings.py` | `Enable auto-start`·`Verify && enable Antigravity`가 컨테이너 전체 너비로 늘어남 | `QHBoxLayout` + `addStretch(1)`로 자연 너비 |
| `settings.py` | 탭 안에 `Settings` 제목이 또 있어 상단 바와 중복 | 제거하고 `Relay daemon`부터 시작. 섹션 제목을 `title.section`으로 통일 |
| `main_window.py` | Schedule이 하나도 없을 때 사이드바에 **빈 테두리 상자**가 남아 고장난 패널처럼 보임 | Schedule이 1개 이상일 때만 그룹 노출 |

### 15.4 §12 테스트 마이그레이션 추가분

| 파일:라인 | 변경 |
|---|---|
| `test_g1_gui.py` | `register_task_button.text()` → `accessibleName() == "Register a new Task"` |
| `test_phase3_gui.py` | `create_button.text()` → `accessibleName() == "Register a new Task"` |
| `test_gui_design_system.py` | `register_task_button.objectName()` `primaryAction` → `iconAction` + accessibleName 검사 추가 |

### 15.5 검증 방법 (재현용)

```
# 실제 플랫폼으로 6개 화면 렌더링 — QT_QPA_PLATFORM을 offscreen으로 두지 말 것
py -m ruff check relay tests
py -m unittest discover -s tests
py build_release.py
```
