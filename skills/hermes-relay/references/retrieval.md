# 과거 작업물 조회 레퍼런스

이미 한 일을 다시 하지 않기 위한 명령들. **새 작업을 제출하기 전에 여기부터 본다.**

| 알고 싶은 것 | 명령 |
|---|---|
| 비슷한 작업을 전에 했나 | `relay search` / `relay search-semantic` |
| 등록된 Task/Project 목록 | `relay catalog tasks` / `relay catalog projects` |
| 최근 실행 이력 | `relay history` |
| 특정 Run의 결과 파일 | `relay result` / `relay artifact read` |
| 이 산출물이 무엇에서 나왔나 | `relay run-lineage` / `relay artifact lineage` |
| 두 실행의 차이 | `relay compare runs` |
| 결과 품질이 괜찮은가 | `relay quality run` |
| 지금 조치가 필요한 것 | `relay attention list` |

---

## 1. 검색

### 키워드 검색

```sh
relay search "경쟁사 동향" --kind runs --machine
relay search "portrait" --kind artifacts --machine
```

| 옵션 | 의미 |
|---|---|
| `--kind` | `runs`(기본) 또는 `artifacts` |
| `--status` | Run 상태로 좁힌다 |
| `--worker` | 실행한 Worker |
| `--source` | 제출 경로 |
| `--trigger-type` | `manual`, `routine`, `schedule`, `project` 등 |
| `--role` | Artifact role (`--kind artifacts`) |
| `--mime-type` | MIME 타입 (`--kind artifacts`) |
| `--from` / `--to` | 날짜 범위 |
| `--limit` / `--offset` | 페이지네이션 |

`--kind runs` 응답의 각 항목:

```
run_id, task_run_id, job_id, title, status, result_status,
executed_at, worker, trigger_type, summary,
artifact_count, artifact_roles, artifacts_available, relevance
```

`artifact_roles`로 그 Run이 어떤 role의 파일을 남겼는지 바로 알 수 있다. 재사용할 Artifact를 고를 때 유용하다.

응답에 `next_cursor`와 `has_more`가 있으면 필요한 만큼 이어서 읽는다.

### 의미 검색

```sh
relay search-semantic "이미지가 포함된 인물 리포트" --kind runs --limit 5 --machine
```

키워드가 정확히 겹치지 않아도 찾는다. 단어를 모를 때 먼저 쓰고, 정확한 필터가 필요하면 `relay search`로 좁힌다.

> 현재 설치에 임베딩 백엔드가 없으면 의미 검색은 내부적으로 키워드 검색(FTS5)으로 대체된다. 결과가 기대보다 단순하면 이 때문일 수 있다.

---

## 2. 결과와 산출물 회수

```sh
relay show <TASK_RUN_ID> --machine       # 상태, actions, 산출물 경로
relay result <TASK_RUN_ID> --machine     # 최종 receipt
relay logs <TASK_RUN_ID> --machine
```

Artifact는 UID로 다룬다.

```sh
relay artifact show <ARTIFACT_UID> --machine              # 메타데이터
relay artifact read <ARTIFACT_UID> --max-bytes 100000 --machine   # 내용
```

`--max-bytes`로 상한을 두고 읽는다. 큰 바이너리를 통째로 읽어 컨텍스트를 낭비하지 않는다. 이미지·PDF 같은 바이너리는 내용을 읽지 말고 경로만 사용자에게 전달한다.

### 재사용

과거 Artifact를 새 작업의 입력으로 그대로 넣을 수 있다.

```sh
relay task run <TASK_ID> --input-artifact <UID>=A1 --machine
relay submit "이 자료를 요약해줘" --input-artifact <UID>=A1 --machine
```

같은 파일을 다시 만들지 말고 이 방법을 쓴다.

---

## 3. 계보 추적

```sh
relay run-lineage <RUN_ID> --machine         # 이 Run이 소비하고 생산한 Artifact
relay artifact lineage <ARTIFACT_UID> --machine   # 이 Artifact의 출처와 소비처
```

"이 보고서 숫자가 어디서 나왔나"를 답할 때 쓴다. Project Run에서 중간 단계 결과를 추적할 때 특히 유용하다.

---

## 4. 비교

```sh
relay compare runs <RUN_A> <RUN_B> --machine
relay compare artifacts <UID_A> <UID_B> --machine
```

같은 Task를 다시 돌렸을 때 무엇이 달라졌는지, 어느 Worker가 나은 결과를 냈는지 판단할 때 쓴다.

---

## 5. 품질과 주의 항목

```sh
relay quality run <RUN_ID> --machine
relay quality attention --status low --machine
relay attention list --machine
relay attention list --kind failed_job --limit 20 --machine
relay attention list --kind approval --machine
relay attention list --kind low_quality --machine
```

`attention list`는 사람이 손대야 하는 것을 모아 보여준다: 실패한 Run, 대기 중인 승인, 품질이 낮은 결과.

정기 실행을 걸어둔 뒤에는 이 명령으로 주기적으로 확인한다. **품질 점수는 참고값이지 사실성 보증이 아니다.** 최종 판단은 결과를 직접 읽고 한다.

---

## 6. 내보내기와 가져오기

```sh
relay export --out relay-backup.zip --machine
relay export --include-runs --out relay-full.zip --machine
relay import relay-backup.zip --conflict skip --machine
relay import relay-backup.zip --conflict rename --include-runs --machine
```

`--conflict`는 `skip`(기본), `overwrite`, `rename` 중 하나다.

**`overwrite`는 기존 정의를 덮어쓴다.** 사용자가 명시적으로 요청하지 않았으면 쓰지 않는다. 기본은 `skip`이다.

---

## 7. 조회 순서 원칙

새 작업을 제출하기 전 이 순서로 확인한다.

1. `relay catalog tasks` / `relay catalog projects` — 재사용할 정의가 있는가
2. `relay search` 또는 `relay search-semantic` — 같은 작업 결과가 이미 있는가
3. 있으면 `relay artifact read` 또는 `--input-artifact`로 재사용
4. 없을 때만 새로 제출

이미 있는 결과를 다시 만드는 것은 시간과 비용을 버리는 것이고, 사용자에게 서로 다른 두 답을 주게 된다.
