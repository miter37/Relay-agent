# CLI Research Notes

조사일: 2026-07-14

구현은 아래 공식 기능을 후보 템플릿으로 사용하지만, 실제 설치 버전의 deep doctor가 최종 권위다.

## Claude Code

공식 CLI reference:

- `claude -p`: 비대화형 print mode
- `--output-format text|json|stream-json`
- `--json-schema`
- `--max-turns`
- `--max-budget-usd`
- `--no-session-persistence`
- `--permission-mode bypassPermissions`
- `--dangerously-skip-permissions`: bypassPermissions와 동등
- `--tools`: built-in tool 제한
- `--disallowedTools mcp__*`: MCP 도구 차단
- 공식 문서는 `claude --help`가 모든 flag를 표시하지 않을 수 있다고 명시

Source: https://code.claude.com/docs/en/cli-reference

## Codex CLI

공식 non-interactive/reference:

- `codex exec`: script/CI 비대화형 실행
- progress는 stderr, final message는 stdout
- `--ephemeral`
- `--ask-for-approval never`
- `--sandbox workspace-write`
- `--search`
- `--output-last-message`
- `--output-schema`
- `PROMPT -`: stdin prompt
- `--yolo`는 approval과 sandbox를 모두 우회하므로 Relay 기본값에서 사용하지 않음

Sources:

- https://developers.openai.com/codex/non-interactive-mode
- https://developers.openai.com/codex/cli/reference

## Antigravity CLI

Google 공식 codelab:

- `agy -p "..."`: non-interactive mode
- `--model`
- `--dangerously-skip-permissions`: tool permission 자동 승인
- trusted workspace와 tool permission 설정 존재
- `always-proceed`는 host command/file write를 승인 없이 수행

Sources:

- https://codelabs.developers.google.com/antigravity-cli-hands-on
- https://cloud.google.com/blog/topics/developers-practitioners/choosing-your-surface-antigravity-20-antigravity-cli-antigravity-ide-or-antigravity-sdk

Antigravity의 headless 출력 안정성은 사용자 환경과 버전에 따라 달라질 수 있으므로 기본 disabled 및 deep doctor opt-in으로 유지한다.
