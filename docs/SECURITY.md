# Relay Security Guide

이 문서는 Windows, Linux, macOS 공통 운영 원칙을 설명한다.

## 위협 모델

```text
Telegram 사용자 입력
→ Hermes가 task 작성
→ AI CLI가 웹 콘텐츠 읽기
→ 무인 권한 모드에서 도구 사용
```

사용자 입력과 웹페이지 모두 prompt injection 입력이 될 수 있다. Relay의 staging과 path validation은 결과 전달 오류를 줄이지만, provider가 가진 OS 권한 자체를 완전하게 제한하지는 못한다.

## 필수 운영 구조

### 1. 전용 저권한 OS 계정

Hermes용 Relay daemon과 세 CLI를 `RelayWorker` 같은 별도 저권한 OS 계정에서 실행한다.

해당 계정에 필요한 권한:

- Relay input 읽기
- Relay workspace/result/artifact/log 수정
- 각 CLI 로그인 상태 저장 폴더

주지 말아야 할 권한:

- 개인 사용자 문서
- 회사 공유 드라이브
- 관리자 권한
- Windows registry 시스템 영역
- 다른 사용자 브라우저 프로필
- SSH key와 cloud credential 폴더

`scripts/create_relay_worker.ps1`은 Windows 계정과 Relay root ACL 구성의 출발점을 제공한다. Linux/macOS에서는 전용 사용자와 owner-only 권한을 운영자가 구성해야 한다. 실제 조직 정책에 맞게 보안 담당자가 검토해야 한다.

격리 구성이 끝난 뒤 RelayWorker 계정에서 다음을 설정한다.

```powershell
relay config set service_isolation_acknowledged true
```

### 2. Worker 정책

Claude:

- `bypassPermissions`를 사용하므로 OS 계정이 실제 경계다.
- `--tools`로 WebSearch/WebFetch/Read/Write/Glob/Grep만 제공한다.
- `--disallowedTools mcp__*`로 MCP를 기본 차단한다.

Codex:

- `approval=never`
- `sandbox=workspace-write`
- sandbox 우회 플래그를 사용하지 않음

Antigravity:

- 기본 disabled
- deep doctor 필요
- `--dangerously-skip-permissions` 사용 시 OS 격리 필수
- 격리 검토 후 `workers.antigravity.security_verified=true` 설정 전에는 enable 불가

### 3. 경로 정책

Hermes caller는 설정의 allowlist root 밖 경로를 요청할 수 없다.

```toml
allowed_input_roots = ["D:/Hermes/relay-input"]
allowed_output_roots = ["D:/Hermes/relay-results"]
allowed_artifact_roots = ["D:/Hermes/relay-artifacts"]
```

### 4. Daemon

- 127.0.0.1만 bind
- runtime token 필요
- token 파일은 저권한 계정만 읽도록 ACL 적용
- 외부 네트워크에 daemon port를 노출하지 않음

### 5. 데이터 보존

SQLite `request_json`에는 작업 내용이 포함된다. 기밀 요청을 처리하면 Relay home 전체를 BitLocker 보호 볼륨에 두고 ACL을 최소화한다.

## Relay가 제공하지 않는 보안

- 완전한 VM/container sandbox
- 웹 prompt injection 방어 보장
- provider 도구 호출 allowlist의 완전한 집행
- 결과 콘텐츠 malware 검사
- 출처의 신뢰성 검증


## Linux/macOS 권장 권한

```sh
chmod 700 "$RELAY_HOME"
chmod 600 "$RELAY_HOME/runtime/daemon.token" 2>/dev/null || true
```

서비스 계정에는 개인 `~/.ssh`, cloud credential, browser profile, macOS Full Disk Access를 제공하지 않는다.
