# Relay 개발 계획서 v1.0

> Antigravity CLI·Claude Code·Codex CLI를 신뢰 가능한 비대화형 작업자로 사용하는 Windows 우선 로컬 작업 브로커

- 문서 상태: 개발 기준안
- 작성일: 2026-07-14
- 주 실행 환경: Windows 11
- 주 연동 대상: Hermes AI Agent Telegram Gateway 및 사람의 직접 CLI 사용
- 구현 원칙: **CLI subprocess 전용**
- 제외 원칙: **SDK·MCP 서버·벤더 API로의 전환 계획을 포함하지 않는다.**

---

## 1. 문서 목적

이 문서는 `Relay`의 제품 범위, 사용자 인터페이스, 시스템 구조, 보안 경계, 작업자별 어댑터, 결과물 전달 계약, 데이터베이스, 오류 처리, Windows 구현 세부사항, Hermes 연동 절차, 시험 계획과 출시 기준을 정의한다.

Relay는 단순히 AI CLI 명령을 대신 실행하는 셸 래퍼가 아니다. 다음 문제를 해결하는 신뢰 실행 계층이다.

1. AI CLI별 명령·옵션·출력 형식 차이를 호출자가 알 필요가 없게 한다.
2. 작업자가 승인 질문 없이 끝까지 실행 가능한 상태인지 실제 시험한다.
3. 종료 코드가 아니라 지정된 결과 파일과 아티팩트의 생성·형식·위치를 검증한다.
4. 작업자가 사용자 최종 경로를 직접 건드리지 못하게 하고, staging 검증 후 결과를 배포한다.
5. 장시간 작업을 Hermes의 터미널 호출 수명과 분리한다.
6. 모든 요청·시도·결과·오류·파일을 SQLite에 기록한다.
7. 중복 요청, 프로세스 잔존, 빈 출력, 대화형 정지, 잘못된 경로 쓰기 등 자동화 실패를 통제한다.

---

## 2. 프로젝트 정의

### 2.1 한 문장 정의

> Relay는 사람이나 Hermes가 한 줄로 외부 AI CLI에 일회성 작업을 위임하고, 중간 상호작용 없이, 요청한 경로에서 JSON 또는 TXT 결과와 부속 아티팩트를 확실하게 회수하도록 하는 로컬 작업 브로커다.

### 2.2 Relay가 보장하는 것

- 설치·로그인된 작업자 CLI의 실제 실행
- 공식 또는 로컬 capability audit로 확인된 비대화형 실행
- 결과 경로와 아티팩트 경로의 결정
- 임시 작업 공간에서의 실행
- 결과 파일 존재·인코딩·형식 검증
- 아티팩트 manifest와 실제 파일 일치 검증
- 검증 완료 후 최종 경로 배포
- 작업 이력과 원시 로그 저장
- timeout·stall·취소 시 전체 프로세스 트리 정리
- 명확한 상태 및 오류 코드 반환
- Hermes가 파싱할 수 있는 단일 JSON 영수증

### 2.3 Relay가 보장하지 않는 것

- 조사 내용의 사실성
- 출처가 개별 주장과 정확히 대응하는지 여부
- 분석 논리의 타당성
- CLI가 선택한 자료의 완전성
- 사용자 의도의 올바른 해석
- 외부 서비스 자체의 가용성

Relay의 `completed`는 **결과 전달 계약이 충족되었다는 뜻**이다. 결과 내용이 객관적으로 검증되었다는 뜻이 아니다.

---

## 3. 고정 설계 결정

다음 항목은 v1 계열의 고정 원칙으로 취급한다.

### 3.1 CLI 전용

Relay는 다음 실행 파일을 subprocess로 호출한다.

- `agy`
- `claude`
- `codex`

SDK, 직접 API, MCP 서버, 앱 서버로 어댑터를 대체하지 않는다. 각 CLI가 업데이트되면 capability audit와 어댑터 사양을 갱신해 대응한다.

### 3.2 Windows 우선

v1의 기준 플랫폼은 Windows 11이다. Linux 지원은 코드 구조상 가능하게 만들되, Windows에서 먼저 다음을 완성한다.

- Windows Job Object 기반 프로세스 트리 관리
- NTFS 경로 및 ACL 검사
- 전용 저권한 사용자 실행
- UTF-8 작업 파일
- PowerShell·CMD를 거치지 않는 직접 프로세스 실행
- 긴 경로와 드라이브 간 결과 배포

### 3.3 새 언어를 만들지 않는다

Relay의 요청 형식은 CLI 인자와 UTF-8 Markdown task file이다. 결과는 JSON 또는 TXT다.

### 3.4 자동 `Y` 입력 금지

대화형 질문이 감지되었을 때 키보드 입력을 흉내 내지 않는다.

- 공식 무인 실행 옵션을 사용한다.
- 입력 스트림은 작업 전달 후 닫는다.
- 질문·정지 발생은 작업자 이상으로 처리한다.

### 3.5 종료 코드만으로 성공 판정 금지

프로세스가 `0`으로 끝나도 결과가 없거나 잘못되었으면 실패다.

### 3.6 결과물은 staging을 거친다

AI CLI가 최종 사용자 경로에 직접 기록하지 못하게 한다.

### 3.7 Hermes에서는 비동기 작업이 기본

사람의 짧은 실행은 동기 `run`, Hermes는 `submit → status → result`를 기본 패턴으로 사용한다.

---

## 4. 지원 작업자

| 작업자 | 실행 파일 | v1 포함 | 기본 활성화 | 기본 작업자 후보 |
|---|---|---:|---:|---:|
| Claude Code | `claude` | 예 | 예 | 1순위 |
| Codex CLI | `codex` | 예 | 예 | 2순위 |
| Antigravity CLI | `agy` | 예 | 아니오 | deep doctor 통과 후 가능 |

### 4.1 초기 기본 정책

```toml
default_worker = "claude"
fallback_order = ["codex"]

[workers.antigravity]
enabled = false
require_deep_doctor = true
```

Antigravity는 프로그램에 포함하되, 로컬 버전의 비대화형 실행·승인 우회·결과 쓰기·출력 회수·stall 테스트·경로 안전성 시험을 통과해야만 활성화할 수 있다.

### 4.2 명시 작업자 정책

- `--worker`를 생략하면 설정된 기본 작업자를 사용한다.
- `--worker claude`처럼 명시하면 기본적으로 Claude만 실행한다.
- 명시 작업자에서도 폴백을 원하면 `--fallback`을 함께 사용한다.
- 작업자 이름은 `auto`, `claude`, `codex`, `antigravity`만 허용한다.

---

## 5. 대상 작업과 사용 제한

### 5.1 허용 대상

최종 산출물을 독립된 JSON 또는 TXT 파일로 정의할 수 있는 일회성 작업이다.

- 웹 조사
- 뉴스·발언 조사
- 기업·산업 자료 조사
- 문서 요약
- 코드·파일 분석
- 보고서 초안 작성
- 비교표·목록 작성
- 입력 파일에서 정보 추출
- JSON 데이터 정리
- 부속 CSV·Markdown·이미지 등 아티팩트 생성

### 5.2 비대상

- 지속적인 대화형 협업
- 사용자 선택 없이는 진행할 수 없는 작업
- OTP·CAPTCHA·추가 로그인 입력이 필요한 작업
- 송금·주문·결제·이메일 발송 등 외부 부작용 수행
- 결과 경로와 결과 형식을 정의할 수 없는 작업
- 실시간 GUI 공동 조작
- 장기 상주형 에이전트 작업

### 5.3 호출 전 필수 정보

Relay는 호출자가 생략하더라도 다음 값을 항상 결정한다.

- 작업문
- 작업자 또는 `auto`
- 결과 형식 `json|txt`
- 결과 경로
- 아티팩트 경로
- 실행 프로필
- timeout
- caller
- request ID 또는 내부 job ID

---

## 6. 사용자 경험과 명령 규격

프로그램 이름은 `relay.exe`, 명령 예시는 `relay`로 표기한다.

### 6.1 가장 단순한 사람용 호출

```powershell
relay "트럼프의 오늘 주요 발언을 조사해"
```

기본값 예시:

- 작업자: `claude`
- 결과 형식: `json`
- 결과 경로: `D:\Relay\results\YYYY-MM-DD\<job_id>\result.json`
- 아티팩트 경로: `D:\Relay\artifacts\<job_id>`
- 프로필: `web-research`
- 실행 방식: 동기
- 폴백: 설정값

### 6.2 작업자 지정

```powershell
relay "트럼프의 오늘 주요 발언을 조사해" --worker codex
```

### 6.3 명시 작업자 + 폴백 허용

```powershell
relay "트럼프의 오늘 주요 발언을 조사해" --worker claude --fallback
```

### 6.4 경로와 형식 지정

```powershell
relay "트럼프의 오늘 주요 발언을 조사해" `
  --worker claude `
  --format json `
  --out "D:\Research\trump.json" `
  --artifacts "D:\Research\trump_artifacts"
```

### 6.5 TXT 결과

```powershell
relay "첨부 문서를 간단히 요약해" `
  --format txt `
  --out "D:\Results\summary.txt" `
  --attach "D:\Input\document.pdf"
```

### 6.6 task file 방식

자동화 호출자는 긴 작업문을 인자로 전달하지 않는다.

```powershell
relay run `
  --task-file "D:\Relay\requests\request.md" `
  --out "D:\Relay\results\result.json" `
  --artifacts "D:\Relay\artifacts\request-001" `
  --machine
```

### 6.7 Hermes용 제출

```powershell
relay submit `
  --task-file "D:\Hermes\relay-input\telegram-8821.md" `
  --format json `
  --out "D:\Hermes\relay-results\telegram-8821.json" `
  --artifacts "D:\Hermes\relay-artifacts\telegram-8821" `
  --request-id "telegram-chat123-message8821" `
  --caller hermes `
  --machine
```

### 6.8 상태 및 결과 조회

```powershell
relay status <job_id> --machine
relay result <job_id> --machine
relay logs <job_id>
relay cancel <job_id> --machine
relay show <job_id> --machine
relay history --status failed
```

### 6.9 진단

```powershell
relay doctor
relay doctor --deep
relay doctor --worker claude --deep
```

### 6.10 설정

```powershell
relay config show
relay config set default_worker claude
relay config set fallback_order codex,antigravity
relay config enable-worker antigravity
relay config disable-worker antigravity
```

---

## 7. CLI 출력 계약

### 7.1 사람 모드

사람 모드에서는 읽기 쉬운 진행 표시를 제공할 수 있다.

```text
[relay] job created: 01J...
[relay] worker: claude
[relay] running...
[relay] completed
Result: D:\Relay\results\...\result.json
Artifacts: D:\Relay\artifacts\...
```

### 7.2 Machine 모드

`--machine`에서는 stdout에 JSON 객체 **한 건만** 출력한다.

- 진행 메시지 없음
- ANSI 색상 없음
- 여러 JSON line 없음
- debug 정보 없음
- stderr 출력은 가능하지만 Hermes가 합쳐 받을 수 있으므로 기본 비활성화
- 자세한 진행 정보는 DB와 로그 파일에 기록

성공 예시:

```json
{
  "ok": true,
  "status": "completed",
  "job_id": "01J2X9R8ABCD",
  "worker": "claude",
  "result_path": "D:/Hermes/relay-results/telegram-8821.json",
  "artifact_path": "D:/Hermes/relay-artifacts/telegram-8821",
  "result_status": "partial",
  "uncertainties_count": 2
}
```

제출 예시:

```json
{
  "ok": true,
  "status": "queued",
  "job_id": "01J2X9R8ABCD"
}
```

실패 예시:

```json
{
  "ok": false,
  "status": "failed",
  "job_id": "01J2X9R8ABCD",
  "error_code": "ALL_WORKERS_FAILED",
  "attempted_workers": ["claude", "codex"],
  "logs_path": "D:/Relay/logs/01J2X9R8ABCD"
}
```

---

## 8. 전체 아키텍처

```text
사람 / Hermes
      │
      ▼
relay.exe
- 입력 파싱
- 경로·정책 검증
- 동기 명령 또는 daemon RPC
- machine receipt 출력
      │
      ▼
relayd.exe
- 작업 큐
- SQLite 상태
- concurrency 제한
- worker 선택
- 프로세스 감독
- 검증·배포
      │
      ├── Claude Adapter
      ├── Codex Adapter
      └── Antigravity Adapter
              │
              ▼
Windows 저권한 작업 환경
              │
              ▼
workspace / staging / artifacts
              │
              ▼
검증 후 최종 결과 경로 배포
```

### 8.1 프로세스 구성

- `relay.exe`: 사용자 CLI와 daemon client
- `relayd.exe`: Windows 백그라운드 서비스 또는 사용자 세션 daemon
- `relay-worker.exe`: 선택 사항. 개별 job supervisor를 별도 프로세스로 분리할 때 사용

초기 구현에서는 `relay.exe`와 `relayd.exe` 두 개로 충분하다.

### 8.2 통신

Windows named pipe를 기본으로 사용한다.

예:

```text
\\.\pipe\relay-v1
```

요청·응답은 길이 프레이밍된 JSON으로 전달한다. 로컬 사용자 ACL을 설정해 허용된 사용자와 Hermes 서비스 계정만 접속할 수 있게 한다.

---

## 9. 작업 상태 머신

### 9.1 상태

```text
CREATED
QUEUED
PREPARING
RUNNING
VALIDATING
DELIVERING
COMPLETED
PARTIAL
FAILED
CANCEL_REQUESTED
CANCELLED
```

### 9.2 전이

```text
CREATED → QUEUED
QUEUED → PREPARING
PREPARING → RUNNING
RUNNING → VALIDATING
VALIDATING → DELIVERING
DELIVERING → COMPLETED | PARTIAL

PREPARING | RUNNING | VALIDATING | DELIVERING → FAILED
QUEUED | PREPARING | RUNNING → CANCEL_REQUESTED → CANCELLED
```

### 9.3 작업자 시도 상태

각 job에는 하나 이상의 attempt가 존재할 수 있다.

```text
ATTEMPT_CREATED
STARTING
ACTIVE
STALLED
PROCESS_EXITED
OUTPUT_INVALID
SUCCEEDED
FAILED
TERMINATED
```

폴백 시 기존 attempt는 수정하지 않고 새 attempt를 생성한다.

---

## 10. 디렉터리 구조

```text
D:\Relay\
├── bin\
├── config\
│   ├── relay.toml
│   ├── schemas\
│   └── profiles\
├── requests\
├── workspace\
│   ├── claude\
│   ├── codex\
│   └── antigravity\
├── staging\
├── results\
├── artifacts\
├── logs\
├── adapter-specs\
├── runtime\
│   ├── relay.pid
│   └── relay.sock-info.json
└── relay.db
```

작업별 구조:

```text
D:\Relay\workspace\claude\<job_id>\
├── request.md
├── relay-context.json
├── input\
├── output\
│   ├── result.json.partial
│   └── receipt.partial.json
├── artifacts\
└── runtime\
    ├── command.json
    ├── stdout.log
    ├── stderr.log
    ├── events.jsonl
    ├── process.json
    └── manifest.json
```

---

## 11. 요청 파일 계약

`request.md`는 UTF-8 without BOM을 기본으로 한다.

예시:

```markdown
# Relay Task

## User Task
트럼프의 오늘 주요 발언을 조사한다.

## Output Contract
- 결과 형식: JSON
- 결과 임시 경로: D:\Relay\workspace\claude\<job_id>\output\result.json.partial
- 아티팩트 임시 경로: D:\Relay\workspace\claude\<job_id>\artifacts
- 사용자에게 질문하지 않는다.
- 완료되지 않은 항목은 숨기지 않는다.
- 지정된 작업 공간 밖의 파일을 수정하지 않는다.

## Research Requirements
- 기준 날짜를 명시한다.
- 중요 주장에 출처 URL을 포함한다.
- 사실과 추정을 구분한다.
- 확인하지 못한 내용은 uncertainties에 넣는다.
```

Relay는 호출자의 원문을 그대로 넣되, 실행 계약 부분은 시스템이 생성한다.

---

## 12. 실행 프로필

### 12.1 `web-research`

목적:

- 최신 웹 자료 조사
- 결과 JSON/TXT 작성
- 출처·기준일·불확실성 기록

원칙:

- shell·임의 코드 실행은 작업자 capability가 허용하더라도 가급적 제한
- 사용 가능한 경우 웹 검색·웹 읽기·파일 읽기·파일 쓰기만 허용
- 결과와 아티팩트는 지정 workspace 안에 작성

### 12.2 `general-artifact`

목적:

- 문서·코드·입력 파일을 읽고 결과물 생성

### 12.3 `analysis-only`

목적:

- 파일 수정 없이 분석 결과만 작성

### 12.4 프로필별 정책

프로필은 다음을 정의한다.

- 허용 작업자
- 모델 기본값
- timeout
- stall threshold
- 작업자 도구 제한
- 결과 schema
- 아티팩트 최대 용량
- 입력 root와 출력 root
- fallback 허용 여부

---

## 13. 결과 계약

### 13.1 JSON 결과 스키마

```json
{
  "schema_version": "1.0",
  "status": "complete",
  "answer": "조사 결과 본문",
  "sources": [
    {
      "title": "출처 제목",
      "url": "https://example.com/source",
      "publisher": "발행자",
      "published_at": "2026-07-14",
      "accessed_at": "2026-07-14T18:00:00+09:00"
    }
  ],
  "uncertainties": [
    "전체 연설문이 아니라 공개된 영상·보도자료를 기준으로 정리함"
  ],
  "missing_items": [],
  "artifacts": [
    {
      "name": "source_table.csv",
      "relative_path": "source_table.csv",
      "description": "출처별 발언 정리표"
    }
  ]
}
```

### 13.2 허용 `status`

- `complete`: 요청된 결과를 완료했다고 작업자가 표시
- `partial`: 일부 항목이 누락되거나 제한이 있음
- `failed`: 결과 파일은 작성했으나 작업을 수행하지 못함

Relay는 `complete`를 사실 검증으로 간주하지 않는다.

### 13.3 TXT 결과

TXT에는 최종 본문만 UTF-8로 저장한다. 다만 내부 `receipt.json`에는 다음을 기록한다.

- job ID
- 작업자
- 실행 결과
- 결과 파일 해시
- 아티팩트 수
- warnings
- uncertainties 탐지 여부

### 13.4 사용자 정의 JSON Schema

v1.0 기본 결과는 Relay 표준 schema를 사용한다. 사용자 schema 지원은 v1.1 후보이나, CLI capability audit가 확인된 작업자에서만 활성화한다.

---

## 14. 아티팩트 계약

### 14.1 기본 규칙

- 아티팩트는 지정된 작업별 artifact staging 폴더 안에만 생성한다.
- 상대 경로만 manifest에 기록한다.
- `..`, 심볼릭 링크, junction, reparse point를 통한 탈출을 차단한다.
- Relay가 최종 경로로 복사하거나 이동한다.
- 사용자 지정 경로가 없으면 job별 기본 폴더를 사용한다.

### 14.2 manifest

```json
{
  "job_id": "01J2X9R8ABCD",
  "artifacts": [
    {
      "relative_path": "sources.csv",
      "mime_type": "text/csv",
      "size": 18422,
      "sha256": "..."
    }
  ]
}
```

### 14.3 제한

기본값 예시:

```toml
max_artifact_count = 50
max_single_artifact_bytes = 104857600
max_total_artifact_bytes = 524288000
```

---

## 15. Staging·검증·원자적 배포

### 15.1 실행 흐름

```text
1. 작업 workspace 생성
2. task file과 입력 복사
3. AI CLI 실행
4. 결과를 .partial 경로에 생성
5. 프로세스 종료 및 orphan 검사
6. 결과 형식·아티팩트 검증
7. 해시 계산
8. 최종 경로에 배포
9. DB를 완료 상태로 갱신
10. machine receipt 반환
```

### 15.2 같은 볼륨

NTFS rename을 이용한다.

```text
result.json.partial → final\result.json
```

### 15.3 다른 볼륨

1. 최종 경로에 `.partial` 이름으로 복사
2. 파일 크기와 SHA-256 비교
3. fsync 가능한 범위에서 flush
4. 최종 이름으로 rename
5. staging 원본 삭제

### 15.4 덮어쓰기

기본값은 금지다.

- 기존 파일이 있으면 충돌 오류 또는 자동 suffix
- `--overwrite`를 명시했을 때만 교체
- 덮어쓰기 시에도 기존 파일을 곧바로 지우지 않고 임시 backup 후 교체 가능

---

## 16. 성공 판정

### 16.1 공통

- 프로세스가 종료됨
- 프로세스 트리 잔존 없음
- timeout·cancel이 아님
- staging 결과 파일 존재
- 파일이 허용 경로 안에 있음
- 크기 제한을 통과
- 결과 검증 통과
- 아티팩트 검증 통과

### 16.2 JSON

- UTF-8 디코딩 성공
- JSON parse 성공
- Relay schema validation 통과
- 필수 필드 존재
- `status` 값 유효
- manifest와 파일 일치

### 16.3 TXT

- 파일 존재
- 빈 파일이 아님
- UTF-8 디코딩 성공
- 명백한 CLI 오류문만 들어 있지 않음
- 최대 결과 크기 이하

### 16.4 대표 실패

```text
exit 0 + 파일 없음       → OUTPUT_NOT_CREATED
exit 0 + 빈 파일         → EMPTY_OUTPUT
JSON parse 실패          → INVALID_JSON
schema 불일치            → SCHEMA_MISMATCH
결과 경로 밖 파일 생성   → ARTIFACT_PATH_VIOLATION
```

---

## 17. Verified Unattended Execution

### 17.1 정의

작업자는 다음 조건을 실제 시험으로 통과해야 자동 작업 배정 대상이 된다.

1. TTY가 필요하지 않다.
2. 로그인 입력이 발생하지 않는다.
3. 폴더 신뢰 질문이 발생하지 않는다.
4. 도구 승인 질문이 발생하지 않는다.
5. 사용자 후속 질문이 발생하지 않는다.
6. 지정 workspace에 결과 파일을 쓸 수 있다.
7. 제한시간 안에 종료한다.
8. stdout 또는 결과 파일을 회수할 수 있다.
9. 자식 프로세스가 남지 않는다.

### 17.2 Capability audit 우선 원칙

설계 문서에 적힌 플래그를 무조건 하드코딩하지 않는다.

개발 및 실행 순서:

```text
설치 실행 파일 탐색
→ 버전 수집
→ help 및 설정 정보 캡처
→ 후보 옵션 조합 시험
→ 실제 파일 생성 probe
→ 출력·종료·권한·경로 검증
→ adapter spec 저장
```

버전이 변경되면 기존 deep audit는 무효다.

### 17.3 Adapter spec

```json
{
  "worker": "claude",
  "executable": "C:/.../claude.exe",
  "version": "2.1.xxx",
  "audited_at": "2026-07-14T18:00:00+09:00",
  "capabilities": {
    "noninteractive": true,
    "structured_output": true,
    "result_file": false,
    "permission_bypass": true,
    "tool_restriction": true
  },
  "command_template_id": "claude-v2.1-print-json-v1",
  "deep_doctor_passed": true,
  "spec_hash": "..."
}
```

---

## 18. Claude Code 어댑터

### 18.1 확인된 기능 후보

설치 버전에서 재검증할 대상:

- 비대화형 `-p` / `--print`
- `--output-format text|json|stream-json`
- `--json-schema`
- `--permission-mode bypassPermissions`
- `--dangerously-skip-permissions`
- `--no-session-persistence`
- `--max-turns`
- `--tools`
- `--disallowedTools`
- `--bare`

### 18.2 기본 정책

- Hermes 자동 실행에서는 `bypassPermissions`가 필요하다.
- 동시에 사용할 수 있는 도구 목록을 프로필별로 제한한다.
- 외부 MCP 도구는 기본 비활성화한다.
- `web-research`에서는 Bash를 기본 제외하는 방안을 우선 시험한다.
- 출력은 JSON wrapper로 수신하고 `result` 또는 `structured_output`을 추출한다.
- 세션은 저장하지 않는다.

### 18.3 후보 명령 템플릿

아래는 capability audit 전의 설계 템플릿이며, 실제 옵션 순서와 조합은 audit 결과로 고정한다.

```text
claude
  --bare
  -p
  --permission-mode bypassPermissions
  --output-format json
  --no-session-persistence
  --max-turns <N>
  --tools <audited-tools>
  --disallowedTools mcp__*
  <fixed prompt referencing request.md>
```

### 18.4 결과 처리

- Claude wrapper JSON을 별도 raw output으로 저장
- `result`와 `structured_output` 구분
- 비용·usage 메타데이터가 있으면 attempts에 저장
- schema가 유효하지 않거나 structured output이 누락되면 실패

---

## 19. Codex CLI 어댑터

### 19.1 확인된 기능 후보

- `codex exec`
- `--ask-for-approval never`
- `--sandbox workspace-write`
- `--json`
- `--output-last-message`
- `--output-schema`
- `--ephemeral`
- `--skip-git-repo-check`
- `-C` / `--cd`
- prompt `-`를 통한 stdin 입력
- live search 관련 옵션

### 19.2 YOLO 정책

Codex의 raw `--yolo` 또는 `--dangerously-bypass-approvals-and-sandbox`는 Relay 기본값으로 사용하지 않는다.

기본:

```text
approval = never
sandbox = workspace-write
```

raw YOLO는 v1에서 설정 항목 자체를 제공하지 않는 것을 원칙으로 한다. Relay의 전용 OS 계정이 있더라도 CLI 자체 sandbox를 유지한다.

### 19.3 후보 명령 템플릿

```text
codex exec
  --ephemeral
  --ask-for-approval never
  --sandbox workspace-write
  --skip-git-repo-check
  --json
  -C <workspace>
  --output-schema <schema-path>
  --output-last-message <raw-final-path>
  -
```

Relay는 stdin으로 짧은 고정 프롬프트만 전달하며, 긴 작업문은 `request.md`에서 읽게 한다.

### 19.4 결과 처리

- JSONL 진행 이벤트는 `events.jsonl`에 저장
- 최종 메시지 파일과 schema 결과를 분리해 검증
- Codex가 결과 파일을 직접 생성하지 못한 경우, 최종 메시지를 Relay 결과 스키마에 래핑하는 fallback은 프로필별로 결정

---

## 20. Antigravity CLI 어댑터

### 20.1 확인된 기능 후보

- `agy -p` 비대화형 실행
- `--model`
- `--dangerously-skip-permissions`
- `agy models`
- 폴더 신뢰 설정
- permission mode 설정

### 20.2 초기 상태

- adapter 코드는 포함
- 기본 `enabled = false`
- deep doctor 통과 전 `auto` 선택 대상 제외
- 사용자가 명시해도 검증되지 않은 버전이면 실행 거부

### 20.3 후보 명령 템플릿

```text
agy
  --dangerously-skip-permissions
  --model <configured model>
  -p <fixed prompt referencing request.md>
```

### 20.4 필수 추가 시험

- 비TTY stdout 회수
- exit 0 + 빈 출력 여부
- 신뢰 폴더 질문 재발 여부
- 지정 경로 파일 쓰기
- 호스트 전체 접근 범위
- 실행 후 helper process 잔존
- 자동화 환경에서의 stall 빈도

---

## 21. Deep Doctor

### 21.1 목적

`doctor --deep`은 실행 파일 존재 검사가 아니라 실제 무인 작업 수행 시험이다.

### 21.2 probe 작업

```text
1. request.md를 읽는다.
2. output/result.json.partial을 생성한다.
3. artifacts/probe.txt를 생성한다.
4. result JSON에는 지정된 고정 값을 넣는다.
5. 질문이나 승인 요청 없이 종료한다.
6. workspace 밖 파일을 수정하지 않는다.
```

### 21.3 검사 항목

- 실행 파일·버전
- 로그인 상태
- 비대화형 시작
- 승인 우회
- 지정 결과 쓰기
- stdout/stderr 회수
- JSON parse
- 종료 코드
- 전체 프로세스 트리 종료
- workspace 밖 변경 감시
- stall
- 실제 소요시간

### 21.4 결과

```text
Worker         Version   Login  Unattended  Output  File write  Isolation  Status
claude         2.1.xxx   yes    pass        pass    pass        pass       healthy
codex          x.y.z     yes    pass        pass    pass        pass       healthy
antigravity    1.x       yes    fail        fail    pass        warning    disabled
```

### 21.5 재검증 조건

- CLI 버전 변경
- 실행 파일 경로 변경
- command template 변경
- Relay 버전에서 어댑터 변경
- profile의 도구·권한 정책 변경
- 마지막 deep audit 이후 설정된 기간 경과

---

## 22. 프로세스 감독과 Stall 감지

### 22.1 문자열 매칭은 보조 신호

다음 문구를 감지할 수 있지만 주 판정 수단으로 쓰지 않는다.

- proceed
- allow
- trust this folder
- press enter
- 승인
- 계속하시겠습니까

로케일과 버전이 바뀔 수 있기 때문이다.

### 22.2 활동 신호

- 마지막 stdout 시각
- 마지막 stderr 시각
- CPU time 변화
- 디스크 I/O 변화
- workspace 파일 변화
- child process 변화
- 네트워크 활동은 OS 격리 범위에서 가능할 때만 참고

### 22.3 기본 정책

```toml
soft_stall_seconds = 120
hard_stall_seconds = 300
absolute_timeout_seconds = 1200
```

- 120초 무출력이라도 CPU·I/O·파일 변화가 있으면 실행 유지
- 모든 활동이 멈추면 soft stall 이벤트 기록
- hard threshold를 넘으면 프로세스 트리 종료
- 작업자 실패로 기록하고 정책에 따라 폴백

### 22.4 stdin

프로세스 시작 후 필요한 입력을 전달하고 stdin을 닫는다. 대화형 질문에 답을 전송하지 않는다.

---

## 23. Windows 프로세스 관리

### 23.1 직접 실행

PowerShell이나 `cmd.exe /c`를 중간에 두지 않고 CreateProcess 계열로 직접 argv를 전달한다.

장점:

- 따옴표 오류 감소
- 한글·특수문자 문제 감소
- injection 표면 감소
- 정확한 자식 프로세스 관리

### 23.2 Job Object

- provider 프로세스를 작업별 Job Object에 할당
- `KILL_ON_JOB_CLOSE` 적용
- cancel·timeout·daemon 종료 시 전체 트리 종료
- 작업 후 orphan scan

Job Object 할당 실패 시 `taskkill /T /F`는 최후 수단으로 사용한다.

### 23.3 인코딩

- request file: UTF-8
- config: UTF-8
- DB text: UTF-8
- subprocess stdout/stderr: raw bytes 저장 후 adapter별 디코딩
- 콘솔 코드페이지에 의존하지 않음

### 23.4 경로

- 내부 경로를 absolute canonical path로 변환
- `\\?\` long path 지원
- case-insensitive 경로 비교
- junction·symlink·reparse point 검사
- 허용 root 하위 여부를 파일 생성 직전과 배포 직전에 재확인

---

## 24. 보안 모델

### 24.1 핵심 위협

```text
Telegram 입력
→ Hermes가 task 생성
→ AI CLI가 웹 자료를 읽음
→ 웹 프롬프트 인젝션 가능
→ 승인 우회 모드로 도구 실행
```

따라서 Hermes 자동 실행은 일반 사용자 계정에서 수행하면 안 된다.

### 24.2 실행 등급

#### Manual mode

- 사람이 직접 사용
- 현재 사용자 계정 실행 가능
- 위험 경고
- output root 정책은 동일하게 적용

#### Hermes service mode

- 전용 Windows 로컬 계정 필수
- 예: `RelayWorker`
- 관리자 권한 없음
- 다른 사용자 프로필 접근 금지
- Relay input/output/workspace root만 ACL 허용
- 시스템 디렉터리·사용자 문서·SSH 키·브라우저 프로필 접근 금지
- CLI 로그인은 전용 계정에서 수행

### 24.3 허용 경로

```toml
allowed_input_roots = [
  "D:/Relay/input",
  "D:/Hermes/relay-input"
]

allowed_output_roots = [
  "D:/Relay/results",
  "D:/Hermes/relay-results"
]

allowed_artifact_roots = [
  "D:/Relay/artifacts",
  "D:/Hermes/relay-artifacts"
]
```

경로 정책에 맞지 않으면 작업 시작 전에 거부한다.

### 24.4 프로필별 도구 축소

- Claude: `--tools`와 deny 규칙을 capability audit 후 활용
- Codex: `workspace-write` sandbox 유지
- Antigravity: 경로·도구 통제가 충분히 확인되지 않으면 Hermes 자동 실행 금지

### 24.5 비밀정보

DB와 로그에 다음을 저장하지 않는다.

- 액세스 토큰
- 쿠키
- 로그인 파일 내용
- 환경 변수의 비밀값

환경 변수 snapshot은 이름과 해시 또는 allowlist된 비민감 값만 저장한다.

---

## 25. 중복 방지

### 25.1 강한 중복 방지

Hermes는 Telegram chat ID와 message ID를 기반으로 `request-id`를 생성한다.

```text
telegram-<chat_id>-<message_id>
```

DB에 unique index를 둔다.

동일 ID 요청:

- 실행 중이면 기존 job 반환
- 완료면 기존 결과 반환
- 실패면 설정에 따라 기존 실패 반환 또는 `--force-rerun` 요구

### 25.2 소프트 중복 방지

`request-id` 누락에 대비해 다음 hash를 계산한다.

```text
normalized task
+ attachment hashes
+ profile
+ requested worker
+ result format
```

최근 N분 내 동일 hash:

- Hermes: 기본 재사용
- 사람: 경고 후 새 실행 가능

```toml
soft_dedup_window_minutes = 30
soft_dedup_hermes_action = "reuse"
soft_dedup_human_action = "warn"
```

---

## 26. 폴백

### 26.1 기본 체인

```toml
default_worker = "claude"
fallback_order = ["codex"]
```

Antigravity가 활성화되고 안정성이 확인되면 사용자가 순서를 변경할 수 있다.

### 26.2 기술적 실패만 폴백

허용:

- WORKER_NOT_INSTALLED
- WORKER_UNHEALTHY
- AUTH_REQUIRED
- RATE_LIMITED
- QUOTA_EXCEEDED
- PROCESS_CRASHED
- TIMEOUT
- STALLED
- INTERACTIVE_PROMPT_DETECTED
- EMPTY_OUTPUT
- OUTPUT_NOT_CREATED
- INVALID_JSON
- SCHEMA_MISMATCH
- ARTIFACT_PATH_VIOLATION

자동 폴백 금지:

- 모델의 정책상 거부
- `partial` 결과
- 결과 내용의 품질 불만
- 사실성 의심
- 정상 완료 후 다른 스타일 선호

### 26.3 폴백 이력

모든 attempt를 보존하고 최종 receipt에 실제 작업자와 시도 순서를 기록한다.

---

## 27. SQLite 데이터 모델

SQLite WAL mode를 사용한다.

### 27.1 `jobs`

```sql
CREATE TABLE jobs (
    job_id TEXT PRIMARY KEY,
    request_id TEXT UNIQUE,
    caller TEXT NOT NULL,
    task_hash TEXT NOT NULL,
    task_text TEXT,
    requested_worker TEXT NOT NULL,
    actual_worker TEXT,
    profile TEXT NOT NULL,
    result_format TEXT NOT NULL,
    output_path TEXT NOT NULL,
    artifact_path TEXT NOT NULL,
    status TEXT NOT NULL,
    result_status TEXT,
    error_code TEXT,
    created_at TEXT NOT NULL,
    started_at TEXT,
    completed_at TEXT,
    config_snapshot_hash TEXT NOT NULL
);
```

### 27.2 `attempts`

```sql
CREATE TABLE attempts (
    attempt_id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL,
    ordinal INTEGER NOT NULL,
    worker TEXT NOT NULL,
    worker_version TEXT,
    executable_path TEXT,
    adapter_spec_hash TEXT,
    command_template_id TEXT,
    permission_mode TEXT,
    sandbox_mode TEXT,
    unattended_verified INTEGER NOT NULL DEFAULT 0,
    started_at TEXT,
    completed_at TEXT,
    exit_code INTEGER,
    failure_code TEXT,
    stdout_path TEXT,
    stderr_path TEXT,
    raw_event_path TEXT,
    FOREIGN KEY(job_id) REFERENCES jobs(job_id)
);
```

### 27.3 `artifacts`

```sql
CREATE TABLE artifacts (
    artifact_id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL,
    relative_path TEXT NOT NULL,
    final_path TEXT NOT NULL,
    mime_type TEXT,
    size_bytes INTEGER NOT NULL,
    sha256 TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY(job_id) REFERENCES jobs(job_id)
);
```

### 27.4 `events`

```sql
CREATE TABLE events (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT NOT NULL,
    attempt_id TEXT,
    timestamp TEXT NOT NULL,
    event_type TEXT NOT NULL,
    payload_json TEXT,
    FOREIGN KEY(job_id) REFERENCES jobs(job_id)
);
```

### 27.5 `capability_audits`

```sql
CREATE TABLE capability_audits (
    audit_id TEXT PRIMARY KEY,
    worker TEXT NOT NULL,
    executable_path TEXT NOT NULL,
    version TEXT NOT NULL,
    audited_at TEXT NOT NULL,
    status TEXT NOT NULL,
    test_results_json TEXT NOT NULL,
    adapter_spec_hash TEXT NOT NULL
);
```

### 27.6 개인정보 모드

```toml
history_mode = "metadata"
```

- `full`: 작업문 저장
- `metadata`: task hash와 메타데이터만 저장
- `off`: 작업 운영에 필수인 최소 상태만 저장

---

## 28. 오류 코드

### 입력·설정

```text
INVALID_ARGUMENT
TASK_REQUIRED
TASK_FILE_NOT_FOUND
INVALID_FORMAT
OUTPUT_PATH_NOT_ALLOWED
ARTIFACT_PATH_NOT_ALLOWED
OUTPUT_ALREADY_EXISTS
CONFIG_INVALID
```

### 작업자

```text
WORKER_NOT_INSTALLED
WORKER_DISABLED
WORKER_UNVERIFIED
WORKER_UNHEALTHY
AUTH_REQUIRED
AUTH_EXPIRED
RATE_LIMITED
QUOTA_EXCEEDED
PERMISSION_BLOCKED
```

### 실행

```text
PROCESS_START_FAILED
PROCESS_CRASHED
TIMEOUT
STALLED
INTERACTIVE_PROMPT_DETECTED
CANCELLED
ORPHAN_PROCESS_DETECTED
```

### 결과

```text
OUTPUT_NOT_CREATED
EMPTY_OUTPUT
INVALID_TEXT_ENCODING
INVALID_JSON
SCHEMA_MISMATCH
RESULT_TOO_LARGE
ARTIFACT_MANIFEST_MISSING
ARTIFACT_MISSING
ARTIFACT_PATH_VIOLATION
DELIVERY_FAILED
CHECKSUM_MISMATCH
```

### 종합

```text
ALL_WORKERS_FAILED
POSSIBLE_DUPLICATE
INTERNAL_ERROR
```

---

## 29. 설정 파일

```toml
schema_version = 1

default_worker = "claude"
fallback_order = ["codex"]
fallback_enabled = true

default_format = "json"
default_profile = "web-research"

result_root = "D:/Relay/results"
artifact_root = "D:/Relay/artifacts"
workspace_root = "D:/Relay/workspace"
staging_root = "D:/Relay/staging"
log_root = "D:/Relay/logs"
database_path = "D:/Relay/relay.db"

timeout_seconds = 1200
soft_stall_seconds = 120
hard_stall_seconds = 300

max_concurrent_jobs = 2
max_concurrent_per_worker = 1

history_mode = "metadata"
soft_dedup_window_minutes = 30
soft_dedup_hermes_action = "reuse"
soft_dedup_human_action = "warn"

overwrite = false
machine_stdout_single_json = true

[workers.claude]
enabled = true
require_deep_doctor = true
default_model = "sonnet"
max_turns = 30
max_budget_usd = 5.0

[workers.codex]
enabled = true
require_deep_doctor = true
sandbox = "workspace-write"
approval = "never"
use_live_search = true
raw_yolo = false

[workers.antigravity]
enabled = false
require_deep_doctor = true
default_model = "Gemini 3.5 Flash (High)"

[hermes]
service_mode_required = true
require_request_id = true
poll_interval_seconds = 10
```

---

## 30. Hermes 스킬 규격

### 30.1 Relay 사용 조건

Hermes는 다음 조건을 모두 만족할 때 Relay를 사용한다.

1. 작업이 일회성이다.
2. 결과를 JSON 또는 TXT 파일로 받을 수 있다.
3. 중간 사용자 입력이 필요하지 않다.
4. 결과 경로와 아티팩트 경로를 결정할 수 있다.
5. 외부 CLI의 조사·분석 능력을 위임할 실익이 있다.

### 30.2 실행 절차

```text
1. Relay 대상 작업인지 판단한다.
2. 결과 형식 JSON/TXT를 정한다.
3. Hermes 허용 root 아래 결과 경로와 artifact 경로를 정한다.
4. UTF-8 request.md를 작성한다.
5. Telegram chat ID + message ID로 request-id를 만든다.
6. relay submit --machine을 실행한다.
7. 반환된 job_id를 저장한다.
8. relay status --machine으로 폴링한다.
9. completed 또는 partial이면 relay result를 읽는다.
10. 실제 result_path 파일을 읽는다.
11. status, uncertainties, missing_items를 확인한다.
12. partial·불확실성을 사용자에게 숨기지 않는다.
```

### 30.3 Hermes 금지 행동

- Claude·Codex·Antigravity 명령을 직접 생성
- 작업문 전체를 shell quoted string으로 전달
- exit code 0만 보고 성공 판정
- stdout 원문을 결과 파일 대신 사용
- `uncertainties` 삭제
- 결과 파일 생성 전 완료 보고
- Relay 결과를 검증된 사실로 단정

### 30.4 Hermes 사용자 응답 원칙

Relay 결과가 `partial`이면 명확히 표시한다.

예:

```text
외부 조사 에이전트의 결과를 받았습니다. 일부 항목은 확인하지 못해 부분 완료로 표시됐습니다.
```

`uncertainties`가 있으면 핵심 항목을 답변에 반영한다.

---

## 31. 관찰 가능성과 로그

### 31.1 로그 분리

- relay daemon log
- job event log
- attempt stdout
- attempt stderr
- provider raw JSON/JSONL
- validation report
- delivery report

### 31.2 이벤트 예시

```text
JOB_CREATED
JOB_DEDUP_REUSED
WORKER_SELECTED
ATTEMPT_STARTED
SOFT_STALL_DETECTED
HARD_STALL_TERMINATED
OUTPUT_VALIDATION_STARTED
OUTPUT_VALIDATION_FAILED
FALLBACK_STARTED
DELIVERY_COMPLETED
JOB_COMPLETED
```

### 31.3 보존 정책

```toml
job_metadata_days = 365
raw_logs_days = 30
workspace_days = 7
failed_workspace_days = 30
results_cleanup = false
```

결과와 아티팩트는 사용자 소유이므로 자동 삭제하지 않는 것이 기본이다.

---

## 32. 구현 기술

### 32.1 권장 언어: Go

이유:

- 단일 실행 파일 배포
- Windows 서비스와 프로세스 관리 구현 용이
- concurrency와 queue 구현 단순
- SQLite 라이브러리 성숙
- subprocess stdout/stderr streaming 용이
- 의존성 및 런타임 설치 부담이 작음

### 32.2 주요 구성

- CLI: Cobra 또는 표준 `flag` 기반
- Config: TOML
- DB: SQLite + WAL
- Schema validation: JSON Schema Draft 2020-12 호환 라이브러리
- Windows: `golang.org/x/sys/windows`
- ID: ULID
- Hash: SHA-256
- IPC: Windows named pipe

### 32.3 패키지 구조 예시

```text
cmd/
  relay/
  relayd/
internal/
  adapter/
    claude/
    codex/
    antigravity/
  audit/
  config/
  db/
  delivery/
  doctor/
  job/
  policy/
  process/
  receipt/
  schema/
  security/
  supervisor/
  validator/
  windowsjob/
profiles/
schemas/
tests/
```

---

## 33. 시험 전략

### 33.1 단위 시험

- 명령 인자 생성
- 경로 canonicalization
- 허용 root 판정
- JSON/TXT validator
- manifest validator
- 상태 전이
- dedup hash
- 오류 매핑
- config merge

### 33.2 통합 시험

각 작업자별 fake CLI를 만들어 다음 상황을 재현한다.

- 정상 결과
- exit 0 + 파일 없음
- exit 0 + 빈 파일
- invalid JSON
- stderr만 출력
- 5분 stall
- 자식 프로세스 잔존
- 결과 경로 밖 파일 생성
- partial 결과
- rate limit 오류

### 33.3 실제 CLI E2E

- Claude deep doctor
- Codex deep doctor
- Antigravity deep doctor
- 웹 조사 결과 JSON 생성
- TXT 요약 생성
- 아티팩트 2개 생성
- 다른 드라이브로 결과 배포
- 한글 요청·경로

### 33.4 Fault injection

- daemon 강제 종료
- 네트워크 단절
- 디스크 공간 부족
- 결과 경로 권한 제거
- SQLite lock
- CLI 업데이트 후 옵션 변경
- workspace 파일 잠금
- cancel race

### 33.5 보안 시험

- `..` 경로
- symlink/junction 탈출
- absolute artifact path
- output root 외부 경로
- 악의적 파일명
- task에 명령행 특수문자
- 웹 프롬프트 인젝션으로 외부 파일 접근 유도
- 다른 Windows 사용자 프로필 접근 시도

---

## 34. 개발 단계

### Phase 0 — 로컬 Capability Audit

산출물:

- 세 CLI 실제 경로와 버전
- help 및 실행 출력 캡처
- 후보 명령 조합
- 비대화형 probe 결과
- 초기 adapter specs

통과 조건:

- Claude와 Codex 중 최소 2개가 결과 파일 probe 성공
- Windows에서 프로세스 트리 종료 확인

### Phase 1 — 동기 핵심 MVP

범위:

- `relay run`
- Claude·Codex adapter
- task file
- 기본 경로
- staging
- JSON/TXT 검증
- 원자적 배포
- SQLite 이력
- `doctor --deep`
- Windows Job Object
- 사람용 출력과 `--machine`

### Phase 2 — Hermes 비동기 연동

범위:

- `relayd.exe`
- `submit/status/result/cancel`
- named pipe
- request ID
- soft dedup
- 전용 Windows 계정
- Hermes 스킬 문서

### Phase 3 — 폴백과 운영 강화

범위:

- 기술적 폴백
- queue·동시성
- retention·cleanup
- history 조회
- rerun
- richer error mapping
- failure analytics

### Phase 4 — Antigravity 활성화

범위:

- Antigravity adapter deep audit
- 신뢰 폴더 및 비TTY 검증
- unattended 결과 파일 생성
- 보안 경계 확인
- 통과한 버전에서만 활성화

### Phase 5 — 안정화

범위:

- 100건 이상 실전 작업
- 버전 업데이트 회귀 시험
- 실패 코드 보완
- 설치 프로그램
- Windows 서비스 등록
- 운영 문서

**SDK 전환 또는 SDK 어댑터 개발 단계는 존재하지 않는다.**

---

## 35. 출시 통과 기준

| 항목 | 기준 |
|---|---:|
| 정상 작업의 결과 파일 수령률 | 98% 이상 |
| exit 0인데 결과 없음의 성공 오판 | 0건 |
| JSON schema 위반의 성공 오판 | 0건 |
| 허용 root 밖 최종 배포 | 0건 |
| 허용 artifact root 탈출 | 0건 |
| timeout 후 orphan process | 0건 |
| exact request ID 중복 유료 실행 | 0건 |
| CLI 버전 변경 후 미검증 자동 실행 | 0건 |
| machine receipt JSON parse | 100% |
| 실패 원인 표준 코드 분류 | 95% 이상 |
| Hermes partial/uncertainties 전달 | 100% |

### 35.1 Antigravity 별도 활성화 기준

- deep doctor 연속 10회 성공
- 비TTY 빈 출력 0회
- 승인 질문 0회
- 지정 workspace 파일 생성 성공 100%
- orphan process 0회
- 허용 경로 밖 변경 0회

---

## 36. v1 범위에서 만들지 않는 것

- 자체 LLM 호출 API
- SDK 어댑터
- MCP 서버 기반 작업자 연결
- 멀티에이전트 토론
- 답변 비교·채점
- 사실 검증 엔진
- 자체 웹검색 엔진
- 브라우저 자동화 엔진
- 작업자 학습·메모리
- GUI 대시보드
- 원격 SaaS
- 모바일 앱
- 이메일·결제 등 외부 행동 도구

---

## 37. 운영 체크리스트

### 설치

- [ ] 전용 `RelayWorker` 로컬 사용자 생성
- [ ] Relay root ACL 설정
- [ ] 세 AI CLI 설치
- [ ] RelayWorker 계정으로 각 CLI 로그인
- [ ] `relay doctor --deep` 실행
- [ ] Claude·Codex healthy 확인
- [ ] Antigravity 기본 disabled 확인
- [ ] daemon Windows 서비스 등록
- [ ] Hermes 허용 input/output root 설정

### 업데이트

- [ ] CLI 버전 변경 감지
- [ ] 해당 작업자 자동 `UNVERIFIED` 처리
- [ ] deep doctor 재실행
- [ ] adapter spec hash 갱신
- [ ] 회귀 작업 10건 실행
- [ ] 통과 후 재활성화

### 장애 대응

- [ ] job 상태 확인
- [ ] attempt stderr 확인
- [ ] process tree 잔존 확인
- [ ] 결과 staging 보존
- [ ] 실패 코드 검토
- [ ] 필요 시 특정 작업자 disable

---

## 38. 최종 제품 원칙

1. **호출자는 CLI별 사용법을 몰라도 된다.**
2. **AI CLI는 결과 파일과 아티팩트를 지정 workspace에 남겨야 한다.**
3. **사람의 추가 입력이 필요한 순간 해당 시도는 실패다.**
4. **종료 코드가 아니라 검증된 산출물이 성공 기준이다.**
5. **최종 사용자 경로는 Relay만 쓴다.**
6. **Hermes 작업은 daemon에 제출하고 job ID로 추적한다.**
7. **작업자 버전이 바뀌면 재검증 전까지 자동 실행하지 않는다.**
8. **Hermes 무인 실행은 전용 저권한 Windows 계정에서만 허용한다.**
9. **결과의 전달 신뢰성과 내용의 사실성은 구분한다.**
10. **SDK로 전환하지 않고 CLI subprocess 모델을 지속적으로 안정화한다.**

---

## 부록 A. 기본 Hermes 호출 예시

### A.1 요청 파일 생성

```markdown
# User Task

2026년 7월 14일 기준 트럼프의 오늘 주요 공개 발언을 조사하라.
발언 내용, 장소 또는 매체, 시각, 관련 정책 분야를 구분한다.
중요 발언마다 출처 URL을 기록한다.
확인할 수 없는 내용은 uncertainties에 명시한다.
```

### A.2 제출

```powershell
relay submit `
  --task-file "D:\Hermes\relay-input\telegram-chat123-message8821.md" `
  --format json `
  --out "D:\Hermes\relay-results\telegram-chat123-message8821.json" `
  --artifacts "D:\Hermes\relay-artifacts\telegram-chat123-message8821" `
  --request-id "telegram-chat123-message8821" `
  --caller hermes `
  --machine
```

### A.3 상태

```powershell
relay status 01J2X9R8ABCD --machine
```

### A.4 결과

```powershell
relay result 01J2X9R8ABCD --machine
```

---

## 부록 B. 공식 기능 확인 기준

Relay 구현 시 다음 공식 자료를 참고하되, 최종 진실은 설치된 버전의 capability audit 결과다.

- OpenAI Codex CLI command/reference 및 approvals·sandbox 문서
- Anthropic Claude Code CLI reference 및 headless/programmatic usage 문서
- Google Antigravity CLI hands-on codelab 및 공식 Antigravity CLI 자료

공식 문서에 옵션이 존재하더라도, 다음을 실제로 재검증하지 않으면 adapter에 활성화하지 않는다.

- 현재 버전에서 옵션이 수락되는지
- 옵션 조합이 충돌하지 않는지
- 비대화형 실행이 실제로 종료되는지
- 결과 파일과 stdout을 회수할 수 있는지
- 권한 질문과 폴더 신뢰 질문이 발생하지 않는지
- Windows 환경에서 동일하게 동작하는지

---

# 문서 종료
