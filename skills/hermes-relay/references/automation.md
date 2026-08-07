# 반복 실행 레퍼런스 — Routine과 Schedule

Relay에는 반복 실행 수단이 둘 있다. 목적이 다르므로 먼저 고른다.

| | Routine | Schedule |
|---|---|---|
| 대상 | 등록된 **Task 또는 Project** | 완료된 **Task Run 하나**를 재생 |
| 만드는 법 | `relay routine create --target-type ...` | `relay schedule create --from-task-run <RUN_ID>` |
| 쓰는 상황 | 정식으로 등록한 파이프라인을 정기 실행 | 잘 된 일회성 실행을 그대로 반복하고 싶을 때 |
| 버전 정책 | `latest` / `pinned` 지원 | 그 Run의 스냅샷 고정 |

**Project를 매일 돌리려면 Routine을 쓴다.** Schedule은 Project를 대상으로 하지 않는다.

---

## 1. Routine

### 등록

```sh
relay routine create \
  --name "오늘의 화제 인물 브리핑" \
  --target-type project \
  --target-id <PROJECT_ID> \
  --type daily \
  --time 08:00 \
  --timezone Asia/Seoul \
  --overlap skip \
  --missed run_once_on_recovery \
  --machine
```

| 옵션 | 값 |
|---|---|
| `--target-type` | `task` 또는 `project` |
| `--target-id` | 등록된 Task ID 또는 Project ID. 없으면 `ROUTINE_INVALID` |
| `--type` | `daily`, `weekly`, `monthly`, `ndays`, `once` |
| `--time` | `HH:MM` 로컬 시각 |
| `--weekday` | `weekly`용. ISO 요일 1(월)~7(일) |
| `--month-day` | `monthly`용. 1~31 |
| `--n-days` | `ndays`용 간격 |
| `--timezone` | IANA 시간대. **반드시 명시한다.** 생략 시 서버 기본값에 의존하게 된다 |
| `--starts-at` / `--ends-at` | 유효 기간 |
| `--version-policy` | `latest`(기본) 또는 `pinned` |
| `--pinned-version` | `pinned`일 때 고정할 버전 번호 |

### 등록 전에 규칙을 확인한다

실제로 언제 도는지 저장 없이 미리 본다.

```sh
relay routine preview --type weekly --weekday 1 --time 09:00 --timezone Asia/Seoul --limit 5 --machine
```

의도한 시각이 아니면 등록하지 않는다.

### 겹침 정책 (`--overlap`)

이전 회차가 아직 돌고 있을 때 새 회차를 어떻게 할지 정한다.

| 값 | 동작 | 쓰는 상황 |
|---|---|---|
| `skip` (기본) | 이번 회차를 버리고 다음 회차로 넘어간다 | 최신 상태만 필요하고 밀린 회차는 의미 없을 때 |
| `queue` | 이번 회차를 **버리지 않고 대기**시킨다. 진행 중인 Run이 끝나면 다음 tick에서 실행하며, 밀린 회차는 **한 tick에 하나씩 순서대로** 처리한다 | 회차를 하나도 빠뜨리면 안 되고 순서가 중요할 때 |
| `cancel_previous` | 진행 중인 Run을 **취소**하고 새 회차를 실행한다 | 항상 최신 회차만 유효하고 오래된 실행은 낭비일 때 |
| `allow_parallel` | 겹쳐서 같이 돈다 | 회차끼리 독립적이고 동시 실행에 문제가 없을 때 |

`queue`는 진행 중인 Run이 끝나지 않으면 계속 대기한다. 무한정 걸릴 수 있는 작업에는 `skip`이나 `cancel_previous`가 안전하다.

`cancel_previous`의 취소는 Task Run이면 Task Run을, Project Run이면 Project Run 전체를 취소한다. 이미 끝난 Run은 그대로 둔다.

### 놓친 회차 정책 (`--missed`)

데몬이 꺼져 있던 동안의 회차를 어떻게 처리할지 정한다.

| 값 | 동작 |
|---|---|
| `skip` | `--missed-grace-seconds`(기본 43200초=12시간)를 넘겨 밀린 회차는 건너뛴다 |
| `run_once_on_recovery` | 밀린 회차가 여러 개여도 **가장 최근 것 하나만** 실행한다 |
| `replay_all` | 밀린 회차를 전부 실행한다 |

매일 최신 상태만 필요한 작업(오늘의 뉴스 등)은 `run_once_on_recovery`가 맞다. 날짜별 기록을 빠짐없이 남겨야 하면 `replay_all`을 쓰되, 데몬이 오래 꺼져 있었다면 한꺼번에 많은 Run이 생긴다는 점을 감안한다.

### 조회와 제어

```sh
relay routine list --machine
relay routine list --name "브리핑" --machine
relay routine show <ROUTINE_ID> --machine
relay routine runs <ROUTINE_ID> --limit 20 --machine
relay routine receipt <ROUTINE_ID> --machine
relay routine run-now <ROUTINE_ID> --machine     # 스케줄과 무관하게 즉시 1회
relay routine update <ROUTINE_ID> --machine
relay routine delete <ROUTINE_ID> --machine
```

`routine delete`는 소프트 삭제다. 과거 Run과 산출물은 남는다.

### 버전 정책

- `latest` — 대상 Task/Project를 수정하면 다음 회차부터 새 버전으로 돈다.
- `pinned` — `--pinned-version`에 고정한다. 대상을 고쳐도 이 Routine은 계속 그 버전으로 돈다.

정기 산출물의 형식을 안정적으로 유지해야 하면 `pinned`를 쓰고, 개선을 즉시 반영하려면 `latest`를 쓴다.

---

## 2. Schedule

완료된 Task Run 하나를 그대로 반복한다.

```sh
relay schedule create --from-task-run <TASK_RUN_ID> \
  --name "주간 리포트" --type weekly --weekday 1 --time 09:00 --machine
```

| 옵션 | 값 |
|---|---|
| `--type` | `daily`, `weekly`, `monthly`, `n_days`, `once` (Routine과 표기가 다르다: `n_days`) |
| `--time` | 반복 가능. 하루에 여러 번 |
| `--weekday` | ISO 1~7. 반복 가능 |
| `--month-day` | 반복 가능 |
| `--missing-month-day` | `skip` 또는 `last_day`. 31일이 없는 달 처리 |
| `--interval-days`, `--anchor-date` | `n_days`용 |
| `--run-at-local` | `once`용 실행 시각 |

원본 Task Run이 재생 가능해야 한다. `relay show <TASK_RUN_ID> --machine`의 `actions.can_schedule`로 확인한다.

```sh
relay schedule preview --machine
relay schedule list --machine
relay schedule show <SCHEDULE_ID> --machine
relay schedule runs <SCHEDULE_ID> --machine
relay schedule pause <SCHEDULE_ID> --machine
relay schedule resume <SCHEDULE_ID> --machine
relay schedule run-now <SCHEDULE_ID> --machine
relay schedule delete <SCHEDULE_ID> --machine
```

Schedule을 지워도 그 Schedule이 만든 과거 Task Run과 산출물은 보존된다.

---

## 3. 운영 확인

```sh
relay operations routines --machine     # Routine 대시보드
relay operations projects --machine     # Project 대시보드
relay attention list --machine          # 조치가 필요한 항목
relay attention list --kind failed_job --machine
```

정기 실행을 설정한 뒤에는 며칠 안에 `operations`와 `attention`으로 실제로 돌았는지, 실패가 쌓이지 않았는지 확인한다. 등록만 하고 끝내지 않는다.

---

## 4. 전제

- **데몬이 떠 있어야 한다.** Routine과 Schedule은 데몬이 회차를 감지해 실행한다.

```sh
relay daemon status
relay daemon start
```

- 데몬이 꺼져 있던 동안의 회차는 `--missed` 정책에 따라 처리된다.
- 반복 작업은 사람이 보지 않는 상태로 돈다. 대상 Task 지시서가 **입력 없이도 완결되는지** 먼저 확인한다. 필수 입력이 있는 Task를 Routine에 걸면 매 회차가 실패한다.

---

## 5. 체크리스트

- [ ] Routine과 Schedule 중 목적에 맞는 것을 골랐는가 (Project면 Routine)
- [ ] `relay routine preview`로 실제 실행 시각을 확인했는가
- [ ] `--timezone`을 명시했는가
- [ ] `--overlap`이 이 작업 성격에 맞는가 (회차를 빠뜨리면 안 되면 `queue`, 최신만 유효하면 `cancel_previous`)
- [ ] `--missed` 정책이 이 작업 성격에 맞는가
- [ ] 대상 Task가 입력 없이 완결되는가
- [ ] 데몬이 상시 실행되는 환경인가
