# Relay GUI — Project Runs 화면 설계 v1.1

> Project는 이 앱의 핵심 기능인데, 정작 **Project 실행이 어떻게 진행되고 어디서 막혔는지 볼 화면이 없다.**
> 이 문서는 사이드바에 신설할 `Project Runs` 화면의 설계다. 실제 DB 스키마와 실제 실패 사례를 근거로 작성했다.

### v1.1 실행 콘솔 보완

구현 기준은 다음 세 가지 읽기 모델을 분리하는 것이다.

| 영역 | 답해야 하는 질문 | 표시 책임 |
|---|---|---|
| Pipeline | Project 흐름과 차단 원인은 무엇인가? | DAG, 병렬 분기, 실패→차단 인과, 상태 중심 카드와 연결선 |
| Artifacts | 실제 결과물을 확인할 수 있는가? | 최종 Artifact 고정 영역, Task별 전체 Artifact 목록, 형식별 Preview |
| Inspector | 선택한 노드의 근거는 무엇인가? | 시도 이력, Worker 증거, 입력 연결, Artifact, 로그/답변/재실행 |
| Timeline | 시간이 어디에서 소요됐나? | 시도별 막대, 재시도 간격, 병렬 구간, 미실행/차단 마커 |

Pipeline 카드에는 실행 원장 열을 반복하지 않는다. 노드 클릭은 Inspector를 열고, Artifact 칩 더블클릭은 Artifacts 탭의 해당 Preview로 이동한다. Artifacts 탭은 처음 열 때 목록만 보여주며 자동 선택하지 않는다. JSON은 구조 트리로, HTML·Markdown·이미지는 형식에 맞게 렌더링한다. 선택된 노드·Artifact와 Inspector 상태는 같은 Project Run의 새 응답이 도착해도 유지한다. 응답 실패는 영구 `Loading…` 대신 해당 섹션의 `Unavailable` 상태와 오류 원인을 표시한다.

---

## 1. 문제 정의 — 지금 사용자가 겪는 일

2026-08-07에 실제로 발생한 Project Run `01KZDETGD8AR0TBC9RWGSC1991`(오늘의 화제 인물 브리핑)의 상태다.

| 노드 | 상태 | 내용 |
|---|---|---|
| `pick` | completed | 인물 선정 성공 |
| `brief` | completed | 브리프 작성 성공 |
| `image` | **failed** | `ALL_WORKERS_FAILED` |
| `page` | **blocked** | image가 실패해서 아예 실행되지 못함 |

**사용자가 GUI에서 볼 수 있는 것:** Runs 화면에 개별 Task Run 3개가 흩어져 보인다. 그중 하나가 실패했다는 것만 알 수 있다.

**사용자가 볼 수 없는 것:**
- 이 3개가 하나의 Project 실행이라는 사실
- `page`는 아예 시도조차 못 했다는 사실 (Runs 목록에 존재하지 않으므로 **보이지 않는다**)
- `image`가 막혀서 `page`가 차단됐다는 인과관계
- 그래서 이 Project가 최종적으로 실패했다는 결론

Project 상세의 `Runs` 탭이 있긴 하지만 `run_id / status / created_at / trigger` 4열짜리 평면 표라서 (`projects.py:209`) 위 질문에 하나도 답하지 못한다.

**지금 원인을 알아내려면** CLI로 최소 5단계를 거쳐야 한다.

```sh
relay project-run show <PRID>          # 실패했다는 것만 확인
relay project-run steps <PRID>         # image가 failed, page가 blocked임을 확인
relay show <TASK_RUN_ID>               # 그 노드의 Task Run 상태
relay logs <TASK_RUN_ID>               # 실제 로그
# + attempts 테이블을 봐야 어느 worker가 왜 죽었는지 앎
```

GUI를 쓰는 사용자에게는 이 경로가 아예 없다.

---

## 2. 이 화면이 답해야 할 질문 (우선순위 순)

설계의 모든 결정은 이 순서를 따른다.

1. **성공했나?** — 한눈에, 색과 단어로.
2. **실패했다면 어디서 막혔나?** — 노드 이름과 이유. *사용자가 추론하게 하지 않는다.*
3. **그래서 무엇이 실행되지 못했나?** — 차단된 하위 노드. 이건 Runs 화면에 아예 나타나지 않는 정보다.
4. **결과물은 무엇인가?** — 최종 산출물과 여는 방법.
5. **시간이 어디에 쓰였나?** — 단계별 소요와 대기 구간.
6. **재시도가 있었나?** — 시도 이력.
7. **지금 뭘 할 수 있나?** — 재시도·승인·취소.

1~3번이 이 화면의 존재 이유다. 4~7번은 그다음이다.

---

## 3. 실제로 사용 가능한 데이터 (실측)

DB를 직접 조회해 확인했다. 추측이 아니다.

| 레벨 | 테이블 | 쓸 수 있는 것 |
|---|---|---|
| 1. Run | `project_runs` | status, trigger_type, submitted_via, created_at, completed_at, `warnings_json`, `final_artifact_ids_json`, routine_id |
| 2. 노드 | `project_run_steps` | node_id, task_id, task_version, status, active_task_run_id, **error_code, error_message**, started_at, completed_at, `input_manifest_json`, `resolved_connections_json` |
| 3. 시도 | `project_step_runs` | node_id, **step_attempt**, task_run_id, worker_override, status, created_at, completed_at |
| 4. Task Run | `jobs` | requested_worker, **actual_worker**, error_code, result_status, 산출물 |
| 5. Worker 시도 | `attempts` | worker, status, error_code, started_at, completed_at (fallback 체인) |
| 그래프 | `project_snapshot_json` | `project_definition`의 nodes·connections → **DAG 모양 그대로** |
| 승인 | `approvals` | node_id, token, status, reviewer, reason, decided_at |

실제 S3 Run에서 뽑은 시도 이력이다. 이 데이터가 이미 있다는 게 중요하다.

```
collect       attempt 1  failed     (DAEMON_RESTARTED)
collect       attempt 2  completed
detail_page   attempt 1  completed
summary_card  attempt 1  failed     (SCHEMA_MISMATCH)
summary_card  attempt 2  completed
```

### 3.1 확인된 결함 — 설계 전에 알아야 할 것

- **`project_runs.started_at`이 항상 `NULL`이다.** 5개 Run 전부 그렇다. 채우는 코드가 없다. → 현재는 `created_at`을 시작으로, 또는 첫 단계의 `started_at`을 실제 시작으로 써야 한다. (§8에서 수정 제안)
- `failed`/`blocked` 단계는 `started_at`·`completed_at`이 비어 있다. 타임라인에서 이 구간은 "실행되지 않음"으로 그려야 한다.
- Catalog 목록 응답에는 **어느 노드가 실패했는지가 없다.** `failure_reason` 문자열만 있다. 목록에서 실패 노드명을 보여주려면 §8의 보완이 필요하다.

---

## 4. 정보 구조

### 4.1 사이드바 위치

```
Runs            ← Task Run (개별 실행)
Project Runs    ← 신설. Project 실행
─────
Tasks
Profiles
Projects
Routines
─────
Settings
```

**근거:** 위 두 개는 "무슨 일이 있었나"(실행 이력), 아래 네 개는 "무엇을 정의했나"(정의). `Runs`와 `Project Runs`를 붙여야 이 대비가 드러나고, 이름이 짝을 이뤄 관계도 자명해진다.

### 4.2 화면 레이아웃

기존 Runs 화면의 목록+상세 문법을 그대로 따르되, 상세를 3단으로 쌓는다.

```
┌─ Project Runs ────────────────────────────────────────────────────────────┐
│ [검색]  [Project ▾] [상태 ▾] [트리거 ▾] [기간 ▾]                          │
├───────────────┬───────────────────────────────────────────────────────────┤
│ 목록           │  ⓐ 판정 헤더 (verdict)                                    │
│               │     "image 단계에서 실패 · 이후 1개 단계 차단됨"           │
│ ▾ 조치 필요 2  │     [실패 지점부터 재시도] [노드 지정 재실행] [출력 폴더]  │
│   ● 인물브리핑 │  ─────────────────────────────────────────────────────── │
│     실패 3/4  │  ⓑ [파이프라인] [아티팩트] [타임라인]       ← 탭          │
│   ○ 상식카드   │                                                           │
│     승인대기   │     pick ✓ ──┬──▶ image ✗ ──▶ page ⊘                     │
│ ▾ 실행 중 1    │              └──▶ brief ✓ ──────┘                        │
│   ◐ 찬반브리핑 │                                                           │
│     2/4       │  ─────────────────────────────────────────────────────── │
│ ▾ 완료 12      │  ⓒ 노드 인스펙터 (선택된 노드)                            │
│   ✓ 상식카드   │     image · 시도 1회 · claude · 6분 3초 · TERMINATED      │
│   ...         │     입력: A1 ← pick(result)                              │
│               │     [로그] [답변] [산출물] [이 노드부터 재실행]            │
├───────────────┴───────────────────────────────────────────────────────────┤
│ ⓓ 최종 산출물: card.html (output) [열기] [경로 복사]                       │
└───────────────────────────────────────────────────────────────────────────┘
```

---

## 5. 핵심 컴포넌트

### ⓐ 판정 헤더 — 이 화면의 심장

**한 줄로 결론을 말한다. 사용자가 상태를 조합해 추론하게 만들지 않는다.**

| 상태 | 문구 |
|---|---|
| completed | `완료 · 4단계 · 2분 13초 · 산출물 2개` |
| failed | `**image 단계에서 실패** · ALL_WORKERS_FAILED · 이후 1개 단계가 차단됨` |
| awaiting_approval | `topic 단계 승인 대기 중 · 3분 경과` |
| running | `image 단계 실행 중 · 2/4 완료 · 1분 12초 경과` |
| cancelled | `취소됨 · 2/4 단계까지 진행` |

실패 문구의 세 요소는 모두 **앱이 계산한다**:
- 실패 노드 = `steps` 중 `status=failed`인 첫 노드 (위상 순서 기준)
- 이유 = 그 노드의 `error_code`
- 차단 수 = `status=blocked`인 노드 개수

지금 사용자가 CLI 5번을 거쳐야 얻는 결론이 이 한 줄이다.

`error_code`는 코드일 뿐이므로 사람 말로 옮긴 짧은 설명을 함께 둔다 (`ALL_WORKERS_FAILED` → "모든 워커가 실패했습니다"). 이미 `SKILL.md` §12에 코드별 의미가 정리돼 있어 그대로 쓴다.

### ⓑ-1 파이프라인 뷰 (기본 탭)

**DAG를 레벨 배치로 그린다.** 의존 깊이가 같은 노드를 같은 열에 둔다.

```
   레벨0        레벨1        레벨2
  ┌──────┐   ┌───────┐   ┌──────┐
  │ pick │──▶│ image │──▶│ page │
  │  ✓   │ ├▶│  ✗    │ ┌▶│  ⊘   │
  └──────┘ │ └───────┘ │ └──────┘
           │ ┌───────┐ │
           └▶│ brief │─┘
             │  ✓    │
             └───────┘
```

레벨은 `ProjectSpec`에 이미 있는 위상 정렬로 계산한다 (`topological_order()`, `predecessor_map()`). 노드가 10개 이하인 현실적 규모에서 충분히 읽힌다.

**노드 카드에 담을 것** (한 카드에 4줄 이내):
- `node_id` + 상태 아이콘
- Task 이름
- 소요 시간, worker
- 시도 2회 이상이면 `재시도 1회` 배지
- 실패면 `error_code`

**시각 규칙:**
- 상태는 아이콘+단어+색 조합 (색만으로 전달 금지 — 디자인 원칙 5)
- **차단된 노드는 흐리게(dimmed) + 점선 테두리.** "실행되지 않음"이 "실패"와 다르다는 걸 형태로 구분한다.
- 실패 노드에서 차단 노드로 가는 간선은 점선. 인과가 눈에 보이게.

노드 클릭 → ⓒ 인스펙터 갱신.

### ⓑ-2 타임라인 뷰

가로축 시간, 세로축 노드. 각 시도가 하나의 막대.

```
        0s        60s       120s      180s      240s
pick    ▓▓▓▓▓▓▓
image           ░░░░░░░░░░░░░░░░░░░░░░░░░  (실패)
brief           ▓▓▓▓▓▓▓▓▓
page                                        (실행 안 됨)
```

**이 뷰가 답하는 것:** "총 9분 걸렸는데 실제 작업은 3분이었다" 같은 질문. 실제 S3 Run이 정확히 그랬다 (06:00:59~06:10:00 중 실작업 약 3분, 나머지는 재시도 대기). 병렬로 도는 팬아웃 구간도 여기서만 보인다.

재시도는 같은 행에 분리된 막대 2개로 그린다.

### ⓑ-3 아티팩트 뷰

왼쪽은 `Final Artifacts`를 상단에 고정하고, 아래에 Task별로 모든 Artifact를 그룹화한다. 오른쪽은 선택한 Artifact를 크게 Preview한다. 처음 진입할 때는 목록만 보여주며, 목록 클릭은 Preview를 열고 Pipeline의 Artifact 칩 더블클릭은 이 탭으로 이동해 해당 항목을 즉시 선택한다.

| 형식 | Preview |
|---|---|
| JSON | 필드·배열 인덱스를 접고 펼치는 구조 트리 |
| HTML | 읽기 전용 HTML 렌더링 |
| Markdown·텍스트 | 문서/텍스트 렌더링 |
| 이미지 | 이미지 미리보기 |
| PDF·ZIP·기타 | 메타데이터와 외부 앱 열기 |

### ⓒ 노드 인스펙터

선택한 노드의 **모든 레벨을 한 곳에** 펼친다. 이게 CLI 5단계를 대체하는 부분이다.

- **시도 이력** — `attempt 1 실패 (SCHEMA_MISMATCH) → attempt 2 완료`. `project_step_runs`에서 그대로 온다.
- **활성 Task Run** — worker(요청/실제), 모델, 소요, 오류. `jobs` + `attempts`에서. fallback이 일어났으면 "요청 auto → 실제 claude"를 명시.
- **입력** — `A1 ← pick(result)` 형태로 어느 상위 노드의 어느 role이 어떤 별칭으로 들어왔는지. `resolved_connections_json`에 있는데 **지금은 어디에도 노출되지 않는다.** 연결이 의도대로 걸렸는지 확인할 유일한 수단이다.
- **산출물** — 이 노드가 만든 artifact와 role.
- **액션** — 로그 열기 / 답변 보기 / 이 노드부터 재실행.

### ⓓ 최종 산출물 스트립

`final_artifact_ids_json`의 항목을 role과 함께. 열기·경로 복사.

완료된 Run에서 사용자가 실제로 원하는 것은 이것 하나다. 스크롤 없이 닿는 위치에 고정한다.

### 목록(좌측) 그룹핑

```
▾ 조치 필요   ← failed, awaiting_approval
▾ 실행 중     ← running, queued
▾ 완료        ← completed (날짜별 하위 그룹)
```

**"조치 필요"를 맨 위에 고정한다.** 어제 실패한 Run이 방금 성공한 Run보다 중요하다. Runs 화면의 상태별 그룹핑과 같은 문법이되 우선순위 기준이 다르다.

행 구성: `상태점 · Project 이름 · 진행 3/4 · 상대시각`. 실패면 실패 노드명까지 (§8 보완 필요).

---

## 6. 상태별 화면

| 상태 | 판정 헤더 | 파이프라인 | 액션 |
|---|---|---|---|
| running | 현재 실행 노드, 경과 | 실행 중 노드 펄스 표시 | 취소 |
| awaiting_approval | 대기 노드, 경과 | 해당 노드 강조 | **승인 / 거부 / 수정 후 승인** |
| completed | 단계 수, 총 소요, 산출물 수 | 전체 완료 | 출력 폴더, 다시 실행 |
| failed | **실패 노드 + 이유 + 차단 수** | 실패·차단 구분 표시 | **실패 지점부터 재시도**, 노드 지정 재실행 |
| cancelled | 어디까지 진행됐는지 | 취소 시점 표시 | 다시 실행 |

승인 대기는 **차단 상태**라는 점에서 실패와 성격이 같다. 목록의 "조치 필요"에 함께 넣는다.

---

## 7. 갱신 정책

- 터미널 상태(completed/failed/cancelled) Run은 폴링하지 않는다.
- 실행 중 Run이 선택돼 있을 때만 2초 간격 갱신. 목록은 5초.
- 기존 `MainWindow`의 QTimer 패턴을 그대로 쓴다.

---

## 8. 백엔드 보완 필요 사항

화면을 제대로 만들려면 아래가 필요하다. 모두 이번 조사에서 실측으로 확인한 결함이다.

| # | 항목 | 이유 | 규모 |
|---|---|---|---|
| 1 | `project_runs.started_at`을 실제로 채운다 | 항상 NULL이라 진짜 시작 시각을 알 수 없다. 첫 단계 dispatch 시점에 기록 | 작음 |
| 2 | Catalog 목록에 `failed_node_id`, `blocked_step_count` 추가 | 목록에서 실패 노드를 보여주려면 필요. 없으면 행마다 상세 조회(N+1) | 작음 |
| 3 | 단계 응답에 `attempt_count` 포함 | 지금은 `project_step_runs`를 따로 조회해야 재시도 횟수를 안다 | 작음 |
| 4 | (선택) `error_code` → 사람 말 매핑을 API가 제공 | GUI·CLI·에이전트가 같은 문구를 쓰게 | 중간 |

1~3은 GUI 작업 전에 처리하는 편이 낫다. 없으면 GUI가 우회 로직을 갖게 되고 그게 부채가 된다.

---

## 9. 구현 단계

**Phase 1 — 뼈대** (이 화면의 가치 대부분이 여기서 나온다)
사이드바 항목 + 목록(그룹핑·필터) + 판정 헤더 + 단계 표(ⓑ-3) + 최종 산출물 스트립.
파이프라인 그래프 없이도 §2의 질문 1~4에 답할 수 있다.

**Phase 2 — 인스펙터**
노드 선택 → 시도 이력·Task Run·입력 연결·산출물·액션. CLI 5단계를 대체하는 핵심.

**Phase 3 — 파이프라인 뷰**
레벨 배치 DAG. 인과(차단)를 형태로 보여주는 부분.

**Phase 4 — 타임라인 뷰**
시간 분포와 병렬 구간.

Phase 1만으로도 지금보다 압도적으로 낫다. 3·4는 표현력 강화다. 실행 원장 세부값은 Pipeline Inspector의 선택 노드 근거와 기존 API 데이터로 유지하며, 별도 Steps 탭은 노출하지 않는다.

---

## 10. 승인 기준

1. 실패한 Project Run을 열었을 때 **클릭 없이** 실패 노드·이유·차단 수를 읽을 수 있다.
2. 차단된 노드가 화면에 **보인다** (지금은 Runs에 아예 없어서 안 보인다).
3. 노드 하나를 클릭하면 시도 이력과 실제 worker 오류까지 같은 화면에서 확인된다.
4. 완료된 Run에서 최종 산출물을 스크롤 없이 열 수 있다.
5. 실패 지점부터 재시도가 이 화면에서 가능하다.
6. 승인 대기 Run을 이 화면에서 승인·거부할 수 있다.
7. 색을 빼도 상태가 구분된다 (아이콘+단어).
8. 터미널 상태 Run은 폴링하지 않는다.
9. 노드 20개짜리 Project에서도 레이아웃이 깨지지 않는다.
