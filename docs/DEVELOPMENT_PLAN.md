# Relay 개발 계획 및 구현 기준 v0.4

- 기준일: 2026-07-14
- 플랫폼: Windows 11 우선
- 구현 방식: Python 3.11+ 표준 라이브러리, CLI subprocess 전용
- worker: Claude Code, Codex CLI, Antigravity CLI
- SDK·벤더 API·MCP 서버 전환: 범위 제외

## 1. 제품 정의

Relay는 사람 또는 Hermes가 AI CLI에 독립적인 일회성 작업을 위임하고, 요청된 경로에서 JSON/TXT 결과와 부속 아티팩트를 확실하게 회수하도록 하는 로컬 작업 브로커다.

Relay의 완료 판정은 다음을 의미한다.

1. 설치 버전에 대해 deep doctor가 통과한 worker가 실행되었다.
2. 중간 승인이나 사용자 질문 없이 프로세스가 종료되었다.
3. staging 결과가 존재하며 형식 검증을 통과했다.
4. 아티팩트가 허용된 staging 폴더 안에 존재한다.
5. 결과와 아티팩트가 최종 경로에 배포되었다.
6. SQLite에 작업 이력과 실행 근거가 기록되었다.

Relay는 결과 내용의 사실성, 출처 적합성, 분석 논리를 보장하지 않는다.

## 2. 고정 설계 결정

### 2.1 CLI subprocess 전용

- `claude`, `codex`, `agy` 실행 파일만 호출한다.
- SDK, 직접 API, MCP server, app server를 사용하지 않는다.
- 설치 버전 변경 시 기존 capability audit를 무효화한다.

### 2.2 작업 파일과 고정 인자

Hermes는 긴 작업문을 shell 문자열로 조립하지 않는다. UTF-8 `request.md`를 만들고 Relay에 경로만 전달한다.

### 2.3 Verified Unattended Execution

worker가 공식 무인 모드로 실행되더라도 deep probe가 다음을 확인하기 전에는 작업을 배정하지 않는다.

- 비대화형 실행
- 승인 입력 없음
- 결과 파일 생성
- 아티팩트 생성
- 정상 종료
- 빈 출력 없음
- JSON/TXT 검증 가능

### 2.4 staging 및 원자적 배포

AI CLI는 사용자 최종 경로에 직접 기록하지 않는다.

```text
worker workspace
→ result.*.partial + artifacts
→ Relay 검증
→ 최종 결과 경로 및 아티팩트 경로
```

### 2.5 Hermes 비동기 기본

```text
relay submit
→ relay status 또는 relay wait
→ relay result
```

사람의 간단한 작업은 `relay "task"` 동기 실행을 사용한다.

## 3. 실제 구현 아키텍처

```text
사람 / Hermes
   │
   ▼
relay.pyz / relay.cmd
   │
   ├── sync run → RelayEngine
   │
   └── submit → 127.0.0.1 token-authenticated daemon
                    │
                    ▼
             SQLite job queue
                    │
      ┌─────────────┼─────────────┐
      ▼             ▼             ▼
   Claude         Codex       Antigravity
   adapter        adapter       adapter
      │             │             │
      └──────────── workspace ─────┘
                    │
               validation
                    │
               final delivery
```

초기 계획의 Windows named pipe 대신 표준 라이브러리만으로 배포하기 위해 loopback HTTP를 채택했다. daemon은 `127.0.0.1`에만 바인딩하고 runtime token을 요구한다.

## 4. Phase별 구현

### Phase 0 — Capability Audit

구현됨:

- executable 탐색
- version 후보 명령 실행
- help 캡처 및 hash
- worker별 capability hint 추출
- 실제 JSON/아티팩트 생성 deep probe
- 버전별 `adapter-specs/<worker>/<version>.json`
- SQLite `capability_audits`
- CLI 버전 변경 시 다른 spec 경로 사용으로 자동 미검증 처리

사용자 PC에서 필요한 절차:

```powershell
relay doctor --worker claude --deep
relay doctor --worker codex --deep
relay doctor --worker antigravity --deep
```

### Phase 1 — 동기 핵심 실행

구현됨:

- `relay "task"`
- `relay run --task-file ...`
- JSON/TXT 결과
- attachment 복사
- 고정 workspace
- request.md 및 schema.json 생성
- subprocess 직접 argv 실행
- timeout/stall/prompt marker 감지
- Windows Job Object 및 `taskkill /T /F` fallback
- staging validation
- same-volume rename/cross-volume hash copy
- SQLite jobs/attempts/events/artifacts
- `doctor --deep`

### Phase 2 — Hermes 비동기 실행

구현됨:

- 자동 시작 daemon
- `submit/status/wait/result/show/logs/cancel`
- machine stdout JSON 한 건
- request ID exact dedup
- daemon restart 시 active 작업 실패 처리
- token-authenticated loopback RPC
- Hermes 스킬 문서

### Phase 3 — 복구와 운영

구현됨:

- 기술적 실패 fallback
- task hash soft dedup
- global 및 worker별 concurrency 제한
- partial result 전달
- rerun
- history 조회
- retention cleanup
- 표준 실패 코드
- 결과 및 artifact hash

폴백 대상:

- 설치/health/audit 실패
- 인증·quota·rate limit
- timeout/stall/interactive prompt
- process crash
- 결과 없음/빈 결과
- JSON/schema 오류
- artifact 경로 위반

자동 폴백하지 않는 대상:

- 정상 `partial`
- 모델 정책 거부
- 결과 내용이 마음에 들지 않음
- 사실 정확성 의심

### Phase 4 — Antigravity

구현됨:

- `agy -p` 후보 비대화형 adapter
- `--dangerously-skip-permissions` 후보
- `--model` 지원
- stdout 또는 결과 파일 정규화
- deep doctor gating
- 기본 disabled
- healthy spec 없이는 enable 차단

실제 사용자 PC에서 deep doctor를 통과한 뒤에만:

```powershell
relay config set workers.antigravity.security_verified true
relay config enable-worker antigravity
```

## 5. CLI 계약

### 가장 단순한 실행

```powershell
relay "트럼프 오늘 발언 조사"
```

기본값:

- worker: 설정의 default worker
- format: JSON
- output: Relay result root 아래 자동 생성
- artifacts: Relay artifact root 아래 자동 생성
- profile: web-research
- timeout: 설정값

### 명시 실행

```powershell
relay "트럼프 오늘 발언 조사" `
  --worker claude `
  --fallback `
  --format json `
  --out "D:\Research\trump.json" `
  --artifacts "D:\Research\trump-artifacts"
```

### Hermes

```powershell
relay submit `
  --task-file "D:\Hermes\relay-input\request.md" `
  --caller hermes `
  --request-id "telegram-<chat>-<message>" `
  --out "D:\Hermes\relay-results\result.json" `
  --artifacts "D:\Hermes\relay-artifacts\job" `
  --machine
```

## 6. 결과 계약

JSON 필수 필드:

```json
{
  "schema_version": "1.0",
  "status": "complete",
  "answer": "...",
  "sources": [],
  "uncertainties": [],
  "missing_items": [],
  "artifacts": []
}
```

`status`는 `complete`, `partial`, `failed` 중 하나다.

TXT는 UTF-8 비어 있지 않은 텍스트다. TXT 작업도 내부 receipt와 artifact manifest를 생성한다.

## 7. 상태 머신

```text
CREATED → QUEUED → PREPARING → RUNNING
→ VALIDATING → DELIVERING → COMPLETED | PARTIAL

QUEUED | PREPARING | RUNNING
→ CANCEL_REQUESTED → CANCELLED

어느 단계든 기술적 오류 → FAILED
```

## 8. 데이터베이스

SQLite 테이블:

- `jobs`: 요청, 상태, 출력 경로, 최종 receipt
- `attempts`: worker별 명령, 버전, 권한/sandbox, 로그, 오류
- `artifacts`: 최종 경로, 크기, MIME, SHA-256
- `events`: 상태 전이 및 soft-stall 이벤트
- `capability_audits`: 버전별 shallow/deep 결과

기본 `history_mode=metadata`에서는 작업 원문 대신 hash와 요청 JSON을 저장한다. 현재 구현은 재실행을 위해 `request_json`에는 task가 포함되므로 고도의 기밀 작업에는 전용 Relay home 암호화/ACL이 필요하다.

## 9. Windows 구현

- shell=False 직접 argv 실행
- UTF-8 request file
- stdout/stderr 파일 직접 리다이렉션
- 파일 크기/mtime 및 workspace fingerprint로 활동 감시
- prompt 문자열은 보조 신호
- Windows Job Object `KILL_ON_JOB_CLOSE`
- timeout/cancel 시 Job Object 및 `taskkill /T /F`
- 같은 볼륨은 `os.replace`
- 다른 볼륨은 `.relay-partial` 복사, SHA-256 검증 후 replace

## 10. 보안

Hermes service mode는 출력/아티팩트/첨부 경로를 allowlist root와 비교한다. 또한 `service_isolation_acknowledged=true`가 아니면 작업 생성 자체를 거부한다.

필수 운영 조치:

- 전용 Windows 로컬 계정
- Relay root만 수정 권한
- 개인 사용자 프로필 및 문서 접근 제거
- CLI 로그인은 전용 계정에서 수행
- Claude MCP 도구 기본 차단
- Codex raw YOLO 비활성
- Antigravity deep doctor 전 비활성

Python 프로세스 자체를 완전한 sandbox로 만드는 기능은 포함하지 않는다. OS ACL과 저권한 계정이 실제 보안 경계다.

## 11. 출시 기준

| 항목 | 기준 |
|---|---:|
| 결과 파일 정상 수령 | 98% 이상 목표 |
| exit 0 + 결과 없음 성공 처리 | 0건 |
| JSON schema 위반 성공 처리 | 0건 |
| request ID 중복 실행 | 0건 |
| 최종 경로 밖 artifact 인정 | 0건 |
| CLI 버전 변경 후 미검증 자동 실행 | 0건 |
| timeout 후 process tree 잔존 | Windows 실기기 시험 0건 목표 |
| machine receipt JSON 파싱 | 100% |

## 12. 남은 실기기 검증

이 패키지 제작 환경에는 사용자 Windows PC의 실제 Claude/Codex/Antigravity 설치 및 로그인 세션이 없다. 따라서 다음은 코드 구현은 완료됐지만 사용자 PC에서 확인해야 한다.

1. 실제 `--help`/version audit 결과
2. 실제 구독 로그인 세션의 headless 동작
3. Claude tool 제한의 실제 웹 조사 가능 여부
4. Codex `workspace-write`에서 live search와 output schema 동시 동작
5. Antigravity 비TTY stdout 및 파일 쓰기
6. Windows Job Object가 CLI가 만든 browser/helper까지 종료하는지
7. 전용 계정 ACL 및 CLI 로그인 유지
8. Hermes terminal tool과 machine stdout JSON 한 건의 호환성

## 13. 만들지 않는 것

- 결과 사실 검증
- 모델 간 답변 비교/토론
- API/SDK adapter
- MCP server adapter
- 자체 웹검색
- GUI
- 결제, 이메일 발송 등 외부 부작용 작업
- 자동 CAPTCHA/OTP 처리


## 0.5 확장: 자동 정리와 교차 플랫폼

- daemon maintenance thread가 만료된 workspace/staging을 주기적으로 정리한다.
- daemon을 사용하지 않는 동기 사용자도 새 작업 시작 시 due cleanup을 수행한다.
- 완료/부분/실패/취소 상태별 보존 기간을 분리한다.
- Windows는 Job Object, Linux/macOS는 POSIX process group으로 자식 프로세스를 종료한다.
- Linux/macOS용 설치 스크립트와 플랫폼별 기본 Relay home을 제공한다.
- 최종 결과·아티팩트·DB 이력은 자동 정리 대상이 아니다.
