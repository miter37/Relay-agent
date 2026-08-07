# 등록 Task 레퍼런스

같은 작업을 반복할 때 지시서를 매번 새로 쓰지 않고 Task로 등록해 재사용한다. 일회성 위임은 `SKILL.md` §8의 `relay submit`을 쓴다.

**등록 Task를 쓰는 경우**

- 같은 형태의 작업이 반복된다 (주간 리포트, 정기 조사)
- Project 노드로 쓸 예정이다
- Routine으로 자동 반복할 예정이다

한 번만 할 작업은 등록하지 않는다.

---

## 1. 먼저 기존 Task를 찾는다

```sh
relay catalog tasks --machine
relay task show <TASK_ID> --machine
```

`task_summary`와 `has_input_schema`를 먼저 읽고, 유망한 후보만 전체 정의를 조회한다. 목적이 맞는 Task가 있으면 새로 만들지 말고 실행하거나, 필요하면 `task update`로 고쳐 쓴다.

키워드로 좁히려면:

```sh
relay search "주간 리포트" --kind runs --machine
```

---

## 2. 등록

```sh
relay task create \
  --name "주간 경쟁사 동향 리포트" \
  --task-file instructions.md \
  --profile evidence-research \
  --format json \
  --description "경쟁사 공개 발표를 주간 단위로 정리한다" \
  --summary "경쟁사 주간 동향을 출처와 함께 정리한다" \
  --machine
```

| 옵션 | 의미 |
|---|---|
| `--name` | 필수. 사람이 목록에서 구분할 이름 |
| `--task-file` | **지시서 파일 경로. 한글이 있으면 반드시 이걸 쓴다** (UTF-8로 읽는다) |
| `--instructions` | 짧은 영문 지시서용. 콘솔 인코딩에 따라 한글이 깨질 수 있다 |
| `--profile` | §4 |
| `--format` | `json`(기본) 또는 `txt` |
| `--worker` | 고정할 Worker. 생략하면 `auto` |
| `--fallback` / `--no-fallback` | 기본 Worker 실패 시 다른 Worker로 넘어갈지 |
| `--timeout` | 초 단위 |
| `--description` | 상세 설명 |
| `--summary` | **Catalog에 노출되는 요약. 나중에 이 Task를 고를 근거가 되므로 구체적으로 쓴다** |
| `--input-schema` / `--input-schema-file` | 실행할 때 받을 값의 JSON Schema (§3) |

`--machine`을 붙이면 `{"ok":true,"task":{...}}`로 받고 `task.task_id`를 보존한다.

### 지시서 작성

`SKILL.md` §6의 템플릿을 따르되, 등록 Task는 **입력이 매번 달라진다**는 점을 전제로 쓴다. 특정 날짜나 특정 회사명을 지시서에 박지 말고, 입력으로 받거나 "실행 시점 기준"으로 표현한다.

Project 노드로 쓸 Task라면 산출 파일 개수와 role을 반드시 못박는다 → `references/projects.md` §3.

---

## 3. 입력 스키마

Task가 실행할 때마다 값을 받게 하려면 입력 스키마를 정의한다.

```sh
relay task create --name "경쟁사 동향 리포트" --task-file instructions.md \
  --input-schema-file input-schema.json --machine

relay task update <TASK_ID> --input-schema '{"type":"object","properties":{"company":{"type":"string"}}}' --machine
```

- `--input-schema` — JSON Schema를 인라인 문자열로. 한글 키가 있으면 콘솔 인코딩 문제를 피해 `--input-schema-file`을 쓴다.
- `--input-schema-file` — UTF-8 파일 경로. 둘을 동시에 주면 `INVALID_REQUEST`로 거부된다.
- JSON이 깨졌거나 객체가 아니면 `INPUT_SCHEMA_INVALID`로 거부된다.

`input-schema.json` 예시:

```json
{
  "type": "object",
  "properties": {
    "회사": { "type": "string", "description": "조사 대상" },
    "기간": { "type": "string" }
  },
  "additionalProperties": false,
  "required": ["회사"]
}
```

지원 타입은 `string`, `number`, `boolean`, 그리고 `enum`을 가진 `string`(선택지)이다. 배열은 `{"type":"array","items":{...}}`로 목록 입력이 된다. `additionalProperties: false`면 스키마에 없는 키를 넘길 때 `INPUT_SCHEMA_MISMATCH`로 거부된다.

실행할 때 값을 넣는다.

```sh
relay task run <TASK_ID> --inputs-json '{"회사":"Acme","기간":"최근 7일"}' --machine
```

입력값은 Task Run에 그대로 보존되어 나중에 재현·검색할 수 있다.

---

## 4. Profile 선택

Profile은 Worker에게 주는 작업 규칙이다.

| Profile ID | 쓰는 상황 |
|---|---|
| `evidence-research` | 근거와 출처가 필요한 조사. 확인된 사실과 추정을 분리시킨다 |
| `decision-brief` | 의사결정용 요약. 짧고 결론 중심 |
| `data-validation` | 데이터 검증·정합성 확인 |
| `analysis-only` | 입력 파일을 수정하지 않고 분석만 |
| `artifact-production` | 파일·문서·코드 등 산출물 생성 |
| `code-review` | 코드 리뷰 |

```sh
relay config show --machine   # 사용자 정의 Profile 포함 현재 목록 확인
```

레거시 ID(`web-research`, `report`, `analysis`, `general-artifact`, `code`)도 아직 받아들여지며 각각 위 ID로 매핑된다. **새로 만들 때는 위 표의 ID를 쓴다.**

사용자 정의 Profile이 있으면 그 `instructions`가 기본 규칙을 대체한다.

---

## 5. 실행

```sh
relay task run <TASK_ID> --machine
relay task run <TASK_ID> --inputs-json '{"회사":"Acme"}' --machine
relay task run <TASK_ID> --worker codex --model gpt-5.6 --machine
relay task run <TASK_ID> --attach spec.pdf --machine
relay task run <TASK_ID> --input-artifact <UID>=A1 --machine
relay task run <TASK_ID> --target "D:/work/report" --machine
```

| 옵션 | 의미 |
|---|---|
| `--inputs-json` | 입력 스키마에 정의된 값 |
| `--attach` | 로컬 파일 첨부 (반복 가능). `input/`에 복사된다 |
| `--input-artifact UID[=ALIAS]` | 과거 Run의 Artifact를 입력으로 재사용. 별칭은 `A1` 형식 |
| `--target` | 실제로 파일을 만들거나 고칠 폴더. 격리 사본에서 작업 후 검증되면 반영된다 |
| `--request-id` | 중복 제출 방지 키. 같은 ID면 기존 Run을 돌려준다 |
| `--force-new` | 중복 판정을 무시하고 새로 실행 |
| `--model` | 모델 지정. 먼저 `relay models --machine`으로 확인 |

실행은 비동기다. `task_run_id`를 보존하고 `SKILL.md` §8 Step 4~6대로 상태 추적·결과 회수한다.

동기 실행이 필요하면 `relay run`을 쓰되, 오래 걸리는 작업에는 쓰지 않는다 (`SKILL.md` §14).

---

## 6. 수정과 버전

```sh
relay task update <TASK_ID> --task-file new_instructions.md --machine
relay task update <TASK_ID> --summary "..." --machine
```

수정하면 `version`이 올라간다. 진행 중인 Run은 제출 시점 스냅샷으로 계속 실행되므로 영향받지 않는다.

Routine이 `version_policy=pinned`로 특정 버전을 고정하고 있으면 수정해도 그 Routine은 옛 버전을 계속 쓴다 → `references/automation.md`.

```sh
relay task delete <TASK_ID> --machine
```

삭제해도 과거 Task Run과 산출물은 남는다. 그 Task를 참조하는 Project가 있으면 그 Project는 실행 시 `PROJECT_TASK_MISSING`으로 실패하므로, 삭제 전에 참조를 확인한다.

---

## 7. 이력 조회

```sh
relay task runs <TASK_ID> --limit 20 --machine    # 이 Task의 실행 이력
relay catalog task-runs --machine                 # 전체 Task Run 카탈로그
relay history --machine                           # 최근 Run 목록
```

성공한 일회성 Run을 나중에 Task로 승격할 수 있다.

```sh
relay task save-as-task <TASK_RUN_ID> --name "..." --description "..." --machine
```

---

## 8. 체크리스트

- [ ] 기존 Task로 해결되지 않는가 (`relay catalog tasks`)
- [ ] 한글 지시서를 `--task-file`로 넘겼는가
- [ ] `--summary`가 나중에 이 Task를 고를 만큼 구체적인가
- [ ] 지시서가 특정 날짜·대상을 하드코딩하지 않았는가
- [ ] Profile이 현재 ID인가 (레거시 ID를 새로 쓰지 않았는가)
- [ ] Project 노드로 쓸 거라면 산출 파일 개수와 role을 못박았는가
