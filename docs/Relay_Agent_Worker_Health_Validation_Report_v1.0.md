# Relay Agent Worker Health Validation Report v1.0

검증일: 2026-08-04  
Python: `D:\Python314\python.exe`  
검증 방식: 실제 설치된 Worker CLI, Relay deep doctor, 임시 Relay Home

## 결과

| Worker | 실제 버전 | 결과 | 핵심 확인 |
|---|---|---|---|
| Codex | `codex-cli 0.144.3` | healthy | strict output schema, unattended, output, Artifact |
| Claude | `2.1.221 (Claude Code)` | healthy | 로그인 후 unattended, output, Artifact |
| Antigravity | `1.1.10` | healthy | full-access temporary Home에서 unattended, output, Artifact |

## 수정 사항

### Codex

Codex API는 JSON Schema의 모든 top-level property가 `required`에 포함되어야 한다.
Relay 표준 계약의 `summary`는 일반 Worker에게 optional이므로 표준 schema는 유지하고,
Codex adapter가 `--output-schema`를 전달하기 직전에 Codex 전용 schema에서 모든 top-level property를 required로 확장했다.

그 결과 기존의 다음 오류가 해결됐다.

```text
Invalid schema for response_format 'codex_output_schema' ... Missing 'summary'
```

### Claude

Claude CLI가 인증되지 않았을 때 stdout JSON의 `Not logged in`을 doctor가
`PROCESS_CRASHED`로 오인하던 문제를 수정했다. 이제 `AUTH_REQUIRED`로 분류한다.
로그인 후 동일한 deep doctor가 healthy로 통과했다.

### Antigravity

현재 설치 버전은 full-access temporary Relay Home에서 deep doctor가 통과했다.
사용자 계정의 다른 live `agy` 세션과 동시에 probe하면 provider 응답이 흔들릴 수 있으므로,
health 검증과 실제 orchestration 실행은 별도 세션에서 수행해야 한다.

## 검증 한계

- Claude의 health 통과는 CLI 로그인 상태에 의존한다.
- Antigravity full-access 모드는 전용 임시 Relay Home에서만 사용했다.
- 이번 보고서는 Worker health와 Relay 계약 검증이며, 복합 Project live 실행 결과 보고서는 별도다.
