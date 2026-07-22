# Hermes Skill: Relay Delegation

## 목적

Relay를 사용해 Claude Code, Codex CLI, Antigravity CLI에 독립적인 일회성 작업을 위임하고 JSON/TXT 결과 파일을 회수한다.

## Relay를 사용하는 조건

다음 조건을 모두 만족할 때만 사용한다.

1. 외부 AI CLI에 맡길 수 있는 독립적인 작업이다.
2. 최종 결과를 JSON 또는 TXT 파일로 받을 수 있다.
3. 작업 도중 사용자와 계속 대화할 필요가 없다.
4. OTP, CAPTCHA, 추가 로그인 입력이 필요하지 않다.
5. 결제, 주문, 이메일 발송 같은 외부 부작용이 중심이 아니다.

Relay는 작업 실행과 결과 파일 전달을 검증한다. 결과 내용의 사실성은 보증하지 않는다.

## 사전 조건

Hermes가 Relay를 사용하기 전에 운영자가 전용 저권한 Windows 계정과 ACL 격리를 구성하고, 해당 계정의 Relay 설정에서 다음을 실행해야 한다.

```powershell
relay config set service_isolation_acknowledged true
```

이 값이 false이면 Hermes caller 작업은 `PERMISSION_BLOCKED`로 거부된다.

## 기본 절차

### 1. 경로 결정

항상 다음을 먼저 정한다.

- task file 경로
- result path
- artifact directory
- request ID

Telegram 요청이라면 request ID는 가능한 경우 다음처럼 만든다.

```text
telegram-<chat_id>-<message_id>
```

### 2. Task file 작성

UTF-8 Markdown 파일을 만든다. 사용자 요청을 충실히 적고 다음을 명시한다.

- 기준 날짜
- 조사 범위
- 결과에 필요한 내용
- 필요한 출처
- 불확실성을 숨기지 말 것

CLI별 플래그나 사용법은 task file에 적지 않는다. Relay가 처리한다.

### 3. Submit

Hermes에서는 동기 run이 아니라 submit을 기본으로 사용한다.

```powershell
relay submit `
  --task-file "<TASK_FILE>" `
  --format json `
  --out "<RESULT_PATH>" `
  --artifacts "<ARTIFACT_DIR>" `
  --request-id "<REQUEST_ID>" `
  --caller hermes `
  --machine
```

특정 worker가 필요할 때만 `--worker claude|codex|antigravity`를 넣는다.

작업자를 명시하지 않으면 Relay 설정의 default worker와 fallback 순서를 사용한다.

### 4. 상태 확인

Submit 결과의 `job_id`를 저장한다.

```powershell
relay status <JOB_ID> --machine
```

또는:

```powershell
relay wait <JOB_ID> --timeout 1800 --machine
```

### 5. 결과 회수

```powershell
relay result <JOB_ID> --machine
```

receipt에서 다음을 확인한다.

- `ok`
- `status`
- `result_path`
- `artifact_path`
- `result_status`
- `uncertainties_count`
- `missing_items_count`

`completed` 또는 `partial`일 때만 result file을 읽는다.

### 6. 사용자 답변

JSON 결과의 다음 필드를 확인한다.

- `answer`
- `sources`
- `uncertainties`
- `missing_items`
- `artifacts`

`partial`, uncertainties, missing_items를 숨기지 않는다.

Relay 결과를 "검증된 사실"이라고 표현하지 않는다. 외부 AI CLI의 조사 결과로 취급하고, 중요한 사실은 필요하면 Hermes가 별도 검증한다.

## 사용 금지

다음 행동을 하지 않는다.

- `claude`, `codex`, `agy` 명령을 직접 조립한다.
- 긴 사용자 요청을 shell 인자에 직접 넣는다.
- CLI stdout 원문만 보고 성공 처리한다.
- exit code 0만 보고 완료라고 말한다.
- Relay 결과가 생성되기 전에 사용자에게 완료라고 한다.
- uncertainties와 missing_items를 삭제한다.
- Relay DB나 adapter spec을 임의로 수정한다.
- deep doctor를 통과하지 않은 worker를 강제로 활성화한다.

## 실패 처리

Relay가 `failed`를 반환하면 `error_code`와 `attempts`를 읽는다.

기술적 실패이고 Relay가 모든 fallback을 소진했다면 사용자에게 작업이 완료되지 않았다고 명시한다.

인증 오류는 다음을 안내한다.

```text
AUTH_REQUIRED
```

해당 Windows RelayWorker 계정에서 worker CLI 로그인을 갱신해야 한다.

## 예시

사용자 요청:

```text
오늘 트럼프 발언을 공식 원문과 주요 언론을 기준으로 조사해.
```

Task file:

```markdown
2026-07-14 기준 도널드 트럼프의 오늘 주요 공개 발언을 조사한다.
공식 연설문, 백악관/공식 계정, 주요 신뢰 언론을 우선한다.
발언 내용, 발언 장소와 시각, 원문 인용, 정책 및 시장 시사점을 정리한다.
사실과 해석을 구분한다.
확인되지 않은 내용은 uncertainties에 넣는다.
```

실행:

```powershell
relay submit `
  --task-file "D:\Hermes\relay-input\trump-8821.md" `
  --out "D:\Hermes\relay-results\trump-8821.json" `
  --artifacts "D:\Hermes\relay-artifacts\trump-8821" `
  --request-id "telegram-123-8821" `
  --caller hermes `
  --machine
```

## 자동 작업공간 정리

Relay daemon은 만료된 내부 workspace와 staging을 자동 정리한다. Hermes는 별도의 cleanup 명령을 실행할 필요가 없다. 최종 result_path, artifact_path, SQLite 이력은 자동 정리 대상이 아니다. 오래된 원시 stdout/stderr가 필요하다면 해당 작업의 보존 기간이 지나기 전에 확인한다.
