# Relay 0.5.0

Relay는 **Antigravity CLI, Claude Code, Codex CLI에 일회성 작업을 위임하고 지정 경로에서 JSON/TXT 결과와 아티팩트를 확실하게 회수하는 Windows·Linux·macOS에서 동작하는 로컬 작업 브로커**입니다.

## 핵심 보장

- 각 CLI의 실제 설치 버전을 `doctor --deep`로 시험한 뒤 사용합니다.
- 승인 질문 없이 수행되는 비대화형 실행만 정상 worker로 인정합니다.
- AI CLI는 사용자 최종 경로가 아니라 Relay workspace에 결과를 작성합니다.
- 종료 코드가 아니라 결과 파일 존재, UTF-8, JSON 스키마, 아티팩트 경로를 검증합니다.
- 검증된 결과만 최종 경로로 원자적으로 배포합니다.
- 작업, 시도, 오류, 결과, 아티팩트 해시를 SQLite에 기록합니다.
- Hermes는 `submit → status/wait → result` 패턴으로 장시간 작업을 맡길 수 있습니다. Hermes/service caller는 전용 저권한 계정과 ACL 격리를 구성한 뒤 `service_isolation_acknowledged=true`를 설정해야 합니다.
- request ID 및 task hash로 유료 중복 실행을 줄입니다.
- 기술적 실패 시 설정된 worker 순서로 폴백할 수 있습니다.

Relay가 보장하는 것은 **실행·전달 계약**입니다. 조사 결과의 사실성이나 논리적 타당성은 보장하지 않습니다.

## 포함 범위

| Phase | 구현 내용 | 상태 |
|---|---|---|
| 0 | CLI 발견, 버전/help 수집, deep unattended probe, 버전별 adapter spec | 구현 완료. 사용자 PC에서 실제 audit 필요 |
| 1 | 동기 run, staging, JSON/TXT 검증, 원자적 배포, SQLite, 교차 플랫폼 process tree 관리 | 구현 완료 |
| 2 | daemon, submit/status/wait/result/cancel, Hermes machine receipt, request ID | 구현 완료 |
| 3 | 기술적 fallback, soft dedup, queue/concurrency, rerun, partial, 자동 주기 cleanup | 구현 완료 |
| 4 | Antigravity adapter 및 deep-doctor 기반 opt-in 활성화 | 구현 완료. 실제 설치 버전 검증 전 기본 비활성 |

## 시스템 요구사항

- Windows 11, Linux, macOS
- Python 3.11 이상
- 사용할 worker CLI가 설치되고 로그인된 상태
  - `claude`
  - `codex`
  - `agy`
- Hermes 무인 실행은 전용 저권한 OS 계정과 파일 권한 격리 필수

외부 Python 패키지는 필요하지 않습니다. SQLite, HTTP daemon, 프로세스 관리 등은 표준 라이브러리만 사용합니다.

## 빠른 설치

PowerShell에서 압축을 해제한 폴더로 이동한 뒤:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\scripts\install_windows.ps1
```

새 터미널에서:

```powershell
relay init
relay doctor --worker claude --deep
relay doctor --worker codex --deep
```

두 worker가 `healthy`가 된 뒤:

```powershell
relay "트럼프의 오늘 주요 발언을 조사해"
```


## Linux/macOS 설치

```sh
chmod +x scripts/install_unix.sh
./scripts/install_unix.sh
```

기본 설치 위치는 `~/.local/bin`, 기본 데이터 위치는 Linux `~/.relay`, macOS `~/Library/Application Support/Relay`입니다. 필요하면 `INSTALL_DIR`, `RELAY_HOME`, `PYTHON` 환경변수로 변경합니다.

```sh
relay init
relay doctor --worker claude --deep
relay doctor --worker codex --deep
```

Antigravity는 deep doctor 통과 뒤 명시적으로 켭니다.

```powershell
relay doctor --worker antigravity --deep
relay config set workers.antigravity.security_verified true
relay config enable-worker antigravity
```

## 기본 사용

```powershell
relay "트럼프의 오늘 주요 발언을 조사해"
```

작업자 지정:

```powershell
relay "오늘 AI 반도체 뉴스를 조사해" --worker codex
```

결과·아티팩트 경로 지정:

```powershell
relay "CSP CAPEX를 조사해" `
  --worker claude `
  --format json `
  --out "D:\Research\csp-capex.json" `
  --artifacts "D:\Research\csp-capex-artifacts"
```

긴 작업문:

```powershell
relay run --task-file "D:\RelayInput\request.md" --machine
```

TXT 결과:

```powershell
relay "이 문서를 요약해" --format txt --attach "D:\Input\report.pdf"
```

## Hermes 사용

```powershell
relay config set service_isolation_acknowledged true

relay submit `
  --task-file "D:\Hermes\relay-input\telegram-8821.md" `
  --format json `
  --out "D:\Hermes\relay-results\telegram-8821.json" `
  --artifacts "D:\Hermes\relay-artifacts\telegram-8821" `
  --request-id "telegram-chat123-message8821" `
  --caller hermes `
  --machine
```

반환된 job ID로:

```powershell
relay wait <job_id> --machine
relay result <job_id> --machine
```

Hermes 스킬 문서는 `skills/hermes-relay/SKILL.md`에 있습니다.

## 설정

기본 홈은 Windows `%LOCALAPPDATA%\Relay`, Linux `~/.relay`, macOS `~/Library/Application Support/Relay`입니다. 다른 위치를 쓰려면 `RELAY_HOME`으로 지정합니다.

```powershell
$env:RELAY_HOME = "D:\Relay"
relay init --force
```

설정 조회:

```powershell
relay config show
```

기본 worker와 폴백 순서:

```powershell
relay config set default_worker claude
relay config set fallback_order codex,antigravity
```


## 자동 임시폴더 정리

Daemon 실행 중에는 1시간마다 정리 시점을 확인하며, 기본적으로 24시간 간격으로 만료된 workspace/staging을 삭제합니다. 동기 `run`만 사용하는 경우에도 새 작업 시작 시 정리 시점이 지났으면 자동 실행됩니다.

기본 보존 기간:

- 완료: 7일
- 부분 완료: 14일
- 실패: 30일
- 취소: 14일
- DB에 없는 orphan 폴더: 7일

최종 결과, 최종 아티팩트, SQLite 이력은 자동 삭제하지 않습니다.

```sh
relay cleanup --status
relay cleanup --dry-run
relay cleanup
```

자세한 내용은 `docs/AUTOMATIC_CLEANUP.md`를 참고합니다.

## 중요한 보안 원칙

Claude의 `bypassPermissions`, Antigravity의 `--dangerously-skip-permissions`는 강한 권한을 가질 수 있습니다. 텔레그램과 웹 콘텐츠가 모두 프롬프트 인젝션 입력이 될 수 있으므로 Hermes 운용에서는 다음을 적용해야 합니다.

1. 전용 저권한 OS 계정에서 Relay와 세 CLI를 실행합니다.
2. 해당 계정은 Relay input/result/artifact/workspace에만 수정 권한을 갖게 합니다.
3. 개인 문서, 회사 공유 드라이브, 브라우저 프로필 등에는 권한을 주지 않습니다.
4. `web-research` 프로필에서 임의 shell 사용을 최소화합니다.
5. Codex는 기본적으로 `--ask-for-approval never + --sandbox workspace-write`를 사용하며 raw `--yolo`는 비활성화합니다.

`relay security`로 현재 정책 경고와 허용 root를 확인할 수 있습니다.

## 로컬 검증

실제 유료 CLI를 호출하지 않고 mock worker로 테스트할 수 있습니다.

Linux/macOS:

```bash
PATH="$PWD/mocks:$PATH" PYTHONPATH=. python -m unittest tests.test_relay.RelayTests.test_sync_json_delivery -v
```

Windows에서는 `mocks\*.cmd`를 PATH 앞에 추가해 같은 방식으로 시험할 수 있습니다.

## 구현상 의도적 변경

초기 계획의 Windows named pipe 대신, 무의존 패키징과 디버깅 용이성을 위해 **127.0.0.1 token-authenticated HTTP daemon**을 사용했습니다. 외부 인터페이스에 바인딩하지 않으며 token 파일은 Relay runtime 폴더에 저장됩니다.

## 문서

- `docs/DEVELOPMENT_PLAN.md`: 상세 개발 계획과 구현 상태
- `docs/CAPABILITY_AUDIT.md`: 실제 설치 CLI 조사 절차
- `docs/SECURITY.md`: Windows/Linux/macOS 격리와 운영 보안
- `docs/RESEARCH_NOTES.md`: 2026-07-14 기준 공식 CLI 기능 조사
- `docs/TEST_REPORT.md`: 패키지 검증 결과와 미검증 항목
- `docs/KNOWN_LIMITATIONS.md`: 현장 검증 필요사항과 알려진 제약
- `docs/CROSS_PLATFORM.md`: Windows/Linux/macOS 운영 차이
- `docs/AUTOMATIC_CLEANUP.md`: 자동 workspace 정리 정책
