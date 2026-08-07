# Project 작성·실행 레퍼런스

여러 Task를 Artifact로 연결해 하나의 DAG로 실행하는 방법. `SKILL.md` §8의 단일 Task 위임으로 끝나지 않는 요청에만 쓴다.

**언제 Project를 쓰는가**

- 단계마다 다른 Profile이나 Worker가 필요할 때 (조사 → 산출물 생성)
- 앞 단계의 파일을 뒷 단계가 실제로 읽어야 할 때
- 같은 파이프라인을 Routine으로 매일 반복 실행할 때

단계 사이에 파일을 넘길 필요가 없으면 Project를 만들지 말고 Task 하나로 처리한다.

---

## 1. 먼저 읽어야 하는 것

새 Project를 설계하기 전에 반드시 기존 것을 먼저 찾는다.

```sh
relay catalog projects --machine
relay project show <PROJECT_ID> --machine
```

목적과 입출력 계약이 맞는 Project가 있으면 새로 만들지 않고 재사용한다.

---

## 2. 정의 스키마

`relay project create --file <path>`에 넘길 UTF-8 JSON.

기계가 읽을 수 있는 정본은 CLI에서 직접 받을 수 있다 (데몬 없이도 동작한다).

```sh
relay project schema --machine
```

`schema`에는 JSON Schema가, `rules`에는 등록 시점 검증 항목과 **실행 시점에만 드러나는 제약**(§3)이 들어 있다. 아래는 그 요약이다.

```json
{
  "name": "오늘의 화제 인물 브리핑",
  "description": "무엇을 하는 Project인지 한두 문장",
  "project_summary": "Catalog에 노출되는 500자 이내 요약",
  "failure_policy": "stop",
  "nodes": [
    { "node_id": "pick",  "task_id": "01K..." },
    { "node_id": "image", "task_id": "01K..." },
    { "node_id": "page",  "task_id": "01K..." }
  ],
  "connections": [
    { "from_node": "pick",  "from_role": "result", "to_node": "image", "to_alias": "A1" },
    { "from_node": "image", "from_role": "output", "to_node": "page",  "to_alias": "A1" },
    { "from_node": "pick",  "from_role": "result", "to_node": "page",  "to_alias": "A2" }
  ],
  "output_selection": [
    { "node_id": "page", "role": "output" }
  ]
}
```

| 필드 | 규칙 |
|---|---|
| `nodes[].node_id` | 비어 있지 않고 Project 안에서 유일. 사람이 읽을 수 있는 짧은 식별자 |
| `nodes[].task_id` | 이미 등록된 Task ID. 없으면 `PROJECT_TASK_MISSING` |
| `nodes[].checkpoint` | 선택. 사람 승인이 필요한 노드에만 (§6) |
| `connections[].from_role` | 상위 노드가 만든 Artifact의 role (§3) |
| `connections[].to_alias` | **`A1`, `A2`, `A3` … 형식만 허용**. 다른 문자열은 `PROJECT_INVALID` |
| `output_selection` | 이 Project의 최종 산출물. `(node_id, role)` 목록 |
| `failure_policy` | 현재 `"stop"`만 유효 |

**검증되는 것** — 등록 시점에 서버가 막는다.

- 노드 0개 → `PROJECT_INVALID`
- `task_id` 미존재 → `PROJECT_TASK_MISSING`
- 자기 자신으로의 연결, 순환 → `PROJECT_CYCLE`
- 같은 `(to_node, to_alias)`에 두 입력 → `PROJECT_INPUT_CONFLICT`
- `output_selection`이 없는 노드를 가리킴 → `PROJECT_INVALID`

**검증되지 않는 것** — 실행할 때 터진다. §3이 이걸 다룬다.

---

## 3. 가장 중요한 규칙: role은 노드마다 정확히 하나여야 한다

연결과 최종 산출물 선택은 모두 `(노드, role)`로 해석되고, **결과가 정확히 1개가 아니면 Project Run이 실패한다.**

- 0개 → `PROJECT_ARTIFACT_MISSING`
- 2개 이상 → `PROJECT_ARTIFACT_AMBIGUOUS`

### 실제로 존재하는 role

| role | 누가 붙이나 | 개수 |
|---|---|---|
| `result` | Relay가 결과 파일(result.json/txt)에 자동으로 붙인다. **예약어라 Worker가 선언할 수 없다** | 성공한 Run마다 항상 정확히 1개 |
| Worker가 선언한 role | 결과 JSON의 `artifacts[].role`. 소문자, `^[a-z][a-z0-9_-]{0,31}$` | 선언한 만큼 |
| `output` | role을 선언하지 않은 모든 파일의 기본값 | 남은 파일 수만큼 |

### 이것이 설계에 미치는 영향

**구조화된 데이터를 넘길 때는 `from_role: "result"`를 쓴다.** 항상 정확히 1개라 절대 모호해지지 않는다. 상위 Task는 파일을 만들 필요 없이 `answer`에만 내용을 담으면 된다.

**파일 자체를 넘길 때는 role을 명시적으로 나눈다.** 한 노드가 파일을 2개 이상 만들고 그것들을 뒷 단계가 따로 소비한다면, Task 지시서에서 각 파일에 **서로 다른 role을 선언**하게 해야 한다.

```jsonc
// Task 지시서가 Worker에게 요구할 결과 형식
"artifacts": [
  { "relative_path": "portrait.jpg", "role": "image",    "encoding": "base64", "content": "...", "description": "..." },
  { "relative_path": "source.json",  "role": "metadata", "encoding": "utf-8",  "content": "...", "description": "..." }
]
```

이러면 `from_role: "image"`와 `from_role: "metadata"`로 각각 정확히 1개씩 잡힌다.

role을 나누지 않으면 두 파일 모두 `output`이 되어 `from_role: "output"` 연결이 `PROJECT_ARTIFACT_AMBIGUOUS`로 죽는다.

**대안: 파일을 하나로 합친다.** 예를 들어 이미지를 별도 파일로 두지 말고 HTML 안에 data URI로 넣으면 최종 노드는 `index.html` 하나만 만들게 되어 `output_selection`이 단순해진다.

### Task 지시서에 반드시 적을 것

Project 노드로 쓰일 Task의 지시서에는 산출 파일 개수를 못박는다. Worker는 지시가 없으면 설명 파일이나 메타데이터 파일을 임의로 추가한다.

```md
## 중요 제약
- artifacts 배열에는 이미지 파일 하나만 넣는다. 메타데이터 파일을 추가로 만들지 않는다.
  파일이 2개 이상이면 이 Project는 실패한다. 출처와 라이선스는 answer에만 적는다.
```

또는 role을 쓰는 경우:

```md
## 중요 제약
- 파일은 정확히 두 개만 만들고 role을 각각 지정한다.
  - portrait.<확장자> → role: "image"
  - source.json → role: "metadata"
- 같은 role을 두 파일에 쓰지 않는다.
```

---

## 4. 하위 노드가 입력을 받는 방식

연결된 Artifact는 하위 Task Run의 워크스페이스 `input/` 아래로 복사되고, 요청서의 **Artifact Inputs** 절에 별칭과 함께 나열된다.

```
- `A1` at `input/image__A1__portrait.jpg` (source 01K.../portrait.jpg, sha256=...)
```

실제 파일명은 `{node_id}__{alias}__{원래경로}`다. **파일명이 `A1`이 아니다.** Task 지시서에는 이렇게 쓴다.

```md
## 입력
- 요청서의 Artifact Inputs 항목에 별칭 `A1`로 표시된 파일이 앞 단계 결과다.
  `input/` 아래에 있으며 파일명은 요청서에 적힌 실제 이름이다.
```

Relay는 복사 전후로 크기와 SHA-256을 검증한다. 원본이 바뀌었으면 `ARTIFACT_CHANGED`로 실패한다.

---

## 5. 등록과 실행

```sh
# 등록 (한글 포함 시 --file 사용. 콘솔 인코딩 문제를 피한다)
relay project create --file project.json --machine

# 실행
relay project run <PROJECT_ID> --machine

# 외부 Artifact를 시작 노드에 주입하며 실행
relay project run <PROJECT_ID> --input pick:A1=<ARTIFACT_UID> --machine
```

`project run`은 즉시 `project_run_id`를 돌려주고 백그라운드로 진행한다. 각 노드는 개별 Task Run으로 실행되므로 Run 목록에도 노드 수만큼 나타난다.

### 진행 추적

```sh
relay project-run show <PROJECT_RUN_ID> --machine    # 전체 상태, failure_reason
relay project-run steps <PROJECT_RUN_ID> --machine   # 노드별 상태와 task_run_id
relay project-run receipt <PROJECT_RUN_ID> --machine # 최종 산출물 Artifact UID
```

단계 상태: `pending` → `ready` → `queued` → `running` → `completed`. 실패 시 `failed`이며, 하위 노드는 `blocked`가 된다.

개별 노드가 왜 실패했는지는 `steps`의 `task_run_id`로 일반 Task Run 진단을 그대로 쓴다 (`SKILL.md` §13).

### 복구

```sh
relay project-run retry <PROJECT_RUN_ID> --machine                      # 실패 지점 재시도
relay project-run retry <PROJECT_RUN_ID> --from-node <NODE> --worker codex --machine
relay project-run reexecute <PROJECT_RUN_ID> --from-node <NODE> --machine  # 성공한 노드부터 다시
relay project-run cancel <PROJECT_RUN_ID> --machine
```

`reexecute`는 지정 노드와 그 하위를 다시 돌린다. 앞 단계 결과는 그대로 재사용하므로 마지막 조립 단계만 고칠 때 유용하다.

---

## 6. 체크포인트(사람 승인)

노드에 checkpoint를 걸면 그 단계 완료 후 `awaiting_approval`로 멈춘다.

```json
{ "node_id": "publish", "task_id": "01K...", "checkpoint": { "enabled": true } }
```

```sh
relay approval list --project-run <PROJECT_RUN_ID> --machine
relay approval show <TOKEN> --machine
relay approval approve <TOKEN> --machine
relay approval reject <TOKEN> --machine
relay approval edit <TOKEN> --file <수정한 파일> --machine
```

`approval edit`으로 넣은 사람 수정본은 하위 노드의 role 해석에서 원본보다 우선한다.

**에이전트는 사람 승인을 대신하지 않는다.** 승인이 필요한 Project를 자동으로 승인하며 진행하지 않는다.

### 폴더 배달

checkpoint에 `deliver_to`를 넣으면 결과를 실제 폴더로 배달한다. `kind`는 `folder`만 지원하고, 경로는 설정된 `allowed_delivery_roots` 안이어야 한다. 아니면 등록 시점에 `DELIVERY_PATH_NOT_ALLOWED`로 거부된다.

---

## 7. 실패 원인 대조표

| 오류 | 원인 | 대응 |
|---|---|---|
| `PROJECT_TASK_MISSING` | `task_id`가 없거나 삭제됨 | `relay catalog tasks`로 확인 후 정의 수정 |
| `PROJECT_INVALID` | 별칭이 `A1` 형식이 아님, 노드 0개, output_selection이 없는 노드 참조 | 정의 수정 |
| `PROJECT_CYCLE` | 연결에 순환 | DAG로 재설계 |
| `PROJECT_INPUT_CONFLICT` | 같은 `(to_node, to_alias)`에 두 연결 | 별칭 분리 |
| `PROJECT_ARTIFACT_MISSING` | 그 role의 파일을 상위 노드가 안 만듦 | Task 지시서에 산출물 요구를 명시 |
| `PROJECT_ARTIFACT_AMBIGUOUS` | 같은 role 파일이 2개 이상 | §3대로 role을 나누거나 파일을 합침 |
| `ARTIFACT_CHANGED` | 원본 Artifact가 변경됨 | 상위 노드부터 재실행 |
| `SCHEMA_MISMATCH` (role 관련) | Worker가 `result` 같은 예약 role을 선언 | Task 지시서에서 role 이름을 바꾸게 수정 |

---

## 8. 설계 체크리스트

Project를 등록하기 전에 확인한다.

- [ ] 기존 Project로 해결되지 않는가 (`relay catalog projects`)
- [ ] 모든 `task_id`가 실재하는가
- [ ] 모든 `to_alias`가 `A1`/`A2` 형식인가
- [ ] 연결 그래프에 순환이 없는가
- [ ] **연결에 쓰이는 모든 `(노드, role)`이 정확히 파일 1개로 해석되는가**
- [ ] 각 노드의 Task 지시서가 산출 파일 개수와 role을 못박고 있는가
- [ ] `output_selection`의 각 항목도 정확히 1개로 해석되는가
- [ ] 하위 노드 지시서가 `input/`의 실제 파일명 규칙을 설명하는가
- [ ] `project_summary`가 나중에 이 Project를 고를 수 있을 만큼 구체적인가
