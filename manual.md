# Relay CLI - Agent Delegation Manual

이 문서는 Hermes AI 등 **자동화된 에이전트(Agent)**들이 **Relay CLI**를 사용하여 다른 AI CLI(Claude Code, Codex CLI, Antigravity CLI)에게 일회성 작업을 위임하고 그 결과를 회수하기 위한 공식 사용 매뉴얼입니다.

## 1. 개요 및 목적
Relay는 에이전트가 긴 작업이나 반복적인 서브 태스크를 다른 AI에게 위임할 때 사용하는 "로컬 작업 브로커"입니다. 
에이전트가 직접 터미널에 `claude`나 `codex` 명령어를 치면서 실시간 상호작용(프롬프트 입력, 승인 등)을 하는 것은 비효율적이고 오류가 발생하기 쉽습니다. 

Relay를 사용하면 다음이 보장됩니다.
* **비대화형 실행 보장:** 작업 도중 멈추거나 승인 대기 없이 끝까지 실행됩니다.
* **정형화된 결과물:** `stdout` 로그를 파싱할 필요 없이 완벽한 형태의 JSON 또는 TXT 파일을 지정된 경로로 받습니다.
* **비동기 처리:** 에이전트는 작업을 던져두고(`submit`) 나중에 완료되었는지 확인(`status`/`result`)만 하면 됩니다.
* **에러 복구:** 첫 번째 작업자(예: Claude)가 실패하면 다음 작업자(예: Codex)로 자동 폴백(Fallback) 됩니다.

---

## 2. 작업 위임 기본 절차 (비동기 방식)

에이전트는 **반드시 비동기(Submit -> Wait -> Result) 패턴**을 사용하는 것이 권장됩니다.

### Step 2.1: 지시서(Task File) 작성
명령줄 인자(CLI arguments)에 긴 프롬프트를 직접 넣지 마세요. UTF-8 인코딩된 Markdown(`*.md`) 파일로 작업 지시서를 작성합니다.

*예시 파일: `C:\AgentWork\task-1001.md`*
```markdown
2026-07-22 기준, 오픈AI의 최신 o1 모델의 주요 특징과 벤치마크 점수를 조사하세요.
사실과 추정을 엄격히 분리하고, 확인되지 않은 정보는 uncertainties 항목에 기록하세요.
출처 URL을 반드시 포함하세요.
```

### Step 2.2: 작업 제출 (Submit)
`relay submit` 명령을 사용하여 작업을 백그라운드로 큐잉합니다. 기계적인 처리를 위해 `--machine` 옵션을 추가하면 JSON 형태의 영수증(Receipt)을 반환받습니다.

```powershell
relay submit `
  --task-file "C:\AgentWork\task-1001.md" `
  --format json `
  --out "C:\AgentWork\result-1001.json" `
  --artifacts "C:\AgentWork\artifacts-1001" `
  --request-id "job-1001" `
  --caller hermes `
  --machine
```
* **출력 예시:**
```json
{
  "ok": true,
  "job_id": "01KY4K...",
  "status": "queued"
}
```
**주의:** 여기서 반환된 `job_id`를 메모리에 저장해 두어야 합니다.

### Step 2.3: 상태 확인 및 대기 (Status / Wait)
작업이 끝났는지 주기적으로 확인하려면 `status`를, 일정 시간 동안 기다리려면 `wait`를 사용합니다.

```powershell
# 상태만 즉시 확인
relay status <JOB_ID> --machine

# 최대 30분(1800초)까지 완료될 때까지 블로킹하며 대기
relay wait <JOB_ID> --timeout 1800 --machine
```

### Step 2.4: 최종 결과 회수 (Result)
상태가 `completed` 또는 `partial`로 바뀌었다면, 결과를 회수합니다.

```powershell
relay result <JOB_ID> --machine
```
* 반환된 JSON에서 `result_path` 위치를 읽어 실제 데이터(`C:\AgentWork\result-1001.json`)를 파싱하여 사용자에게 답변을 구성합니다.
* 자동화 환경에서는 영수증의 `ok`와 `status`를 함께 확인하세요. `failed` 또는 `cancelled` 결과는 Relay CLI도 비정상 종료 코드(2)를 반환합니다.

---

## 3. 결과 파일 스키마 (JSON Format)

`--format json`을 요청한 경우, Relay가 생성하여 반환하는 최종 JSON 파일의 구조는 항상 다음과 같습니다. 
에이전트는 터미널 출력이 아닌 이 JSON 파일을 읽어서 후속 작업을 진행해야 합니다.

```json
{
  "schema_version": "1.0",
  "status": "complete",
  "answer": "요청한 작업에 대한 메인 분석 내용 및 답변",
  "sources": [
    "https://example.com/article1",
    "https://example.com/article2"
  ],
  "uncertainties": [
    "o1 모델의 정확한 파라미터 수는 공개되지 않음"
  ],
  "missing_items": [],
  "artifacts": [
    {
      "name": "chart.png",
      "relative_path": "artifacts-1001/chart.png",
      "description": "supporting chart"
    }
  ]
}
```

Codex JSON 작업에서는 모델이 `relative_path`, `description`, `encoding`, `content`를 포함한 내부 아티팩트 payload를 반환할 수 있습니다. Relay는 경로·인코딩·개수·전체 크기를 검증한 뒤 아티팩트 디렉터리에 파일을 생성하며, 최종 결과 JSON에서는 `encoding`과 `content`를 제거합니다. `relative_path`는 아티팩트 디렉터리 기준이므로 `artifacts/` 접두사를 붙이지 않습니다.

---

## 4. 고급 사용법 및 옵션

### 4.1 특정 Worker(AI) 강제 지정
Relay는 설정된 기본 작업자(예: Claude)를 쓰지만, 특정 작업에 코딩 전문 AI가 필요하다면 `--worker` 옵션을 줍니다.
```powershell
relay submit --task-file "..." --worker codex --machine
```
(사용 가능 worker: `claude`, `codex`, `antigravity`)

### 4.2 파일 첨부 (Context 전달)
분석할 문서나 코드가 있다면 `--attach` 옵션으로 파일을 복사해서 넘겨줄 수 있습니다.
```powershell
relay submit --task-file "C:\AgentWork\analyze.md" --attach "C:\Docs\report.pdf" --machine
```

### 4.3 동기식(Sync) 바로 실행
단순한 1회성 작업이라 백그라운드 큐잉(Submit)이 필요 없고, 끝날 때까지 쉘이 멈춰있어도 상관없다면 `run`이나 기본 명령을 사용합니다.
```powershell
relay "현재 디렉토리의 app.py 파일의 버그를 찾아줘" --worker antigravity --format txt --out "bug_report.txt"
```

---

## 5. 에이전트 주의사항 (DO & DON'Ts)

✅ **DO (권장사항)**
* 긴 프롬프트는 반드시 별도의 Markdown 파일(`--task-file`)로 저장해서 넘길 것.
* `--machine` 플래그를 적극 사용하여 터미널 출력(stdout)을 순수한 JSON 형태로 파싱할 것.
* Relay가 반환하는 `uncertainties`나 `missing_items` 필드의 내용을 무시하지 말고 사용자에게 투명하게 알릴 것.

❌ **DON'Ts (금지사항)**
* `claude`, `codex` 실행 파일을 터미널에서 직접 `subprocess`로 부르지 말 것. (프롬프트 대기 현상 발생 위험)
* 종료 코드(`exit 0`)만 보고 작업이 성공했다고 단정 짓지 말 것. 반드시 `relay result` 혹은 생성된 JSON 파일 내부의 `status`를 확인할 것.
* Relay DB(`relay.db`)나 내부 Workspace 임시 폴더를 직접 열어서 수정하지 말 것. (모든 것은 CLI 명령어로만 통제)
