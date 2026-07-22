# Phase 0 Capability Audit

설계 문서나 온라인 문서의 플래그를 그대로 신뢰하지 않는다. **설치된 실행 파일 버전에서 실제 probe가 통과한 조합만 실행에 사용한다.**

## 1. Shallow audit

```powershell
relay doctor
```

수집 항목:

- executable path
- version 문자열
- help output hash
- 알려진 flag hint
- worker enabled 상태

Shallow audit는 실행 가능성을 보장하지 않는다.

## 2. Deep audit

```powershell
relay doctor --worker claude --deep
relay doctor --worker codex --deep
relay doctor --worker antigravity --deep
```

Deep probe가 요청하는 작업:

1. `request.md` 읽기
2. 표준 JSON 결과 반환
3. `artifacts/probe-artifact.txt` 생성
4. 사용자 질문이나 승인 없이 종료

통과 조건:

- exit code 0
- timeout/stall 없음
- interactive marker 없음
- JSON 필수 필드 충족
- answer가 `RELAY_UNATTENDED_OK`
- artifact 내용이 `RELAY_ARTIFACT_OK`

## 3. 버전 변경

spec 경로는 worker와 version 문자열로 나뉜다.

```text
adapter-specs/claude/<version>.json
adapter-specs/codex/<version>.json
adapter-specs/antigravity/<version>.json
```

업데이트된 version에는 기존 healthy spec이 적용되지 않는다. 새 deep doctor를 통과해야 한다.

## 4. Antigravity 정책

Antigravity는 deep doctor가 통과해도 자동 enabled 되지 않는다.

```powershell
relay config set workers.antigravity.security_verified true
relay config enable-worker antigravity
```

이 명령은 현재 설치 버전의 healthy spec이 없으면 거부된다.

## 5. 실제 audit 보관

- adapter spec JSON
- SQLite capability audit row
- probe stdout/stderr
- probe workspace

CLI update 전후 비교에 사용한다.
