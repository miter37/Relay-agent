# 알려진 제약 및 현장 검증 필요사항

## 1. 실제 벤더 CLI capability audit는 사용자 PC에서 수행해야 함

어댑터 명령 템플릿은 2026-07-14 기준 공식 문서와 mock CLI 시험을 바탕으로 구현했습니다. 사용자의 실제 설치 버전에 대해서는 다음 명령을 반드시 수행해야 합니다.

```powershell
relay doctor --worker claude --deep
relay doctor --worker codex --deep
relay doctor --worker antigravity --deep
```

Relay는 버전별 deep doctor를 통과하지 않은 worker를 자동 작업에 사용하지 않습니다.

### 실기 검증 기록 (2026-07-23, Windows 11)

실제 벤더 CLI로 deep doctor를 수행한 결과입니다.

| Worker | 검증한 버전 | 결과 | probe 소요 |
|---|---|---|---|
| `claude` | 2.1.218 (Claude Code) | healthy | 18.6초 |
| `codex` | codex-cli 0.144.3 | healthy | 61.6초 |
| `antigravity` | 1.1.5 | healthy | 136.3초 |

세 worker 모두 `unattended_ok`, `output_ok`, `artifact_ok`가 참이었습니다. 즉 사람의 개입 없이 완료되고, 스키마에 맞는 결과와 아티팩트를 생성했습니다.

이 표는 **위 버전에 대한 기록**이지 보증이 아닙니다. CLI를 업그레이드하면 다시 수행해야 합니다.

참고로 `codex`는 `--help`에 `--output-schema`가 노출되지 않아 `schema_hint`가 거짓이었으나 deep probe는 통과했습니다. help 출력은 참고용이고 deep probe가 최종 판단이라는 설계가 의도대로 동작한 사례입니다.

## 2. 플랫폼별 검증 현황

### 검증 완료

- **자동 테스트 66개** — Windows, macOS, Linux × Python 3.11/3.12/3.13 총 9개 조합에서 통과 (GitHub Actions, 매 커밋마다 재검증)
- **PowerShell 설치 스크립트 및 PATH 등록** — Windows 11에서 새 클론부터 설치·실행까지 확인
- **세 벤더 CLI의 무인 실행·결과·아티팩트 계약** — 1절 검증 기록 참조 (Windows 11)

### 아직 검증되지 않음

- **Linux/macOS의 실제 벤더 CLI 동작** — CI는 mock CLI로만 수행합니다. 실기 검증은 Windows 11에서만 이루어졌습니다.
- Windows Job Object를 이용한 **전체 프로세스 트리 종료** — 자식 프로세스를 만드는 작업으로 시험되지 않았습니다.
- 전용 `RelayWorker` 계정 및 NTFS ACL
- Windows Defender·회사 보안 솔루션과의 상호작용
- Claude/Codex/Antigravity가 생성하는 브라우저 helper 정리
- 장시간 무출력 작업의 stall 기준

Hermes 연동 전 격리 계정에서 장애 주입 시험을 수행해야 합니다.

## 3. 결과의 사실성은 검증하지 않음

Relay가 검증하는 것은 실행과 파일 전달 계약입니다.

- 결과 파일이 생성됐는가
- JSON/TXT 형식이 맞는가
- 아티팩트가 지정된 경로 안에 있는가
- 프로세스가 정상적으로 종료됐는가

웹 조사 내용의 사실성, 출처 적합성, 최신성, 논리적 타당성은 보증하지 않습니다. Hermes는 `uncertainties`, `missing_items`, `partial` 상태를 사용자에게 숨기면 안 됩니다.

## 4. Antigravity는 기본 비활성

Antigravity는 다음 조건을 모두 만족해야만 활성화됩니다.

1. 설치 버전에서 `doctor --deep` 통과
2. 고정 workspace에서 무인 결과·아티팩트 생성 성공
3. 운영자가 OS 격리 상태를 확인
4. `workers.antigravity.security_verified=true` 설정
5. worker 명시적 활성화

## 5. 로컬 daemon의 보안 범위

Daemon은 `127.0.0.1`에만 바인딩하고 runtime token을 요구합니다. 이는 로컬 프로세스 사이의 우발적 접근을 줄이는 장치이지, 악성 코드가 이미 같은 Windows 계정 권한으로 실행되는 상황을 방어하는 강한 보안 경계는 아닙니다. Hermes service mode는 반드시 별도 저권한 OS 계정과 ACL 격리를 사용해야 합니다.

## 6. CLI subprocess 전용

본 프로젝트는 설계 결정상 SDK, 직접 API, MCP server, app server로 전환하지 않습니다. 벤더 CLI 옵션이나 출력이 변경되면 adapter audit와 수정이 필요할 수 있습니다.


## 0.5 cross-platform notes

- Linux/macOS package installation is supported, but each provider CLI may expose OS-specific flags or authentication behavior. `doctor --deep` remains mandatory.
- Relay creates a detached user daemon, not a systemd unit or macOS LaunchAgent. It auto-starts on `submit` and stops only when requested or when the user session/process is terminated.
- Automatic cleanup is best-effort. Locked files, restrictive permissions, antivirus, or external processes can prevent deletion; failures appear in the cleanup report and are retried later.
