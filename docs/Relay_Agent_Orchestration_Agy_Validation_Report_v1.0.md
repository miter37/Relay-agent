# Relay Agent 오케스트레이션 검증 보고서 — Antigravity (agy) v1.0

| 항목 | 값 |
|---|---|
| 검증일 | 2026-08-04 |
| 역할 | Orchestration Agent |
| Python | `D:\Python314\python.exe` |
| Worker | **real Antigravity CLI (`agy`) 1.1.10** — mock 아님 |
| 실행 파일 | `C:\Users\doyoon.kim\AppData\Local\agy\bin\agy.exe` |
| 러너 | `tests/test_agent_orchestration_agy.py` |
| 소요 | 약 283초 (doctor deep 포함, 전체 S1–S6) |
| 운영 DB 조작 | 없음 (임시 Relay Home) |

## 1. 요약

| 시나리오 | 판정 |
|---|---|
| S1 단독 Artifact 체인 | ✅ 통과 |
| S2 순차 Project | ✅ 통과 |
| S3 병렬 통합 Project | ✅ 통과 |
| S4 Project 간 Artifact 재사용 | ✅ 통과 |
| S5 실패 영수증 + 재실행 | ✅ 통과 |
| S6 Catalog / 검색 / 본문 | ✅ 통과 |

**종합: 6/6 통과. Worker = 실제 agy.**

| 지표 | 결과 |
|---:|---:|
| Doctor deep | healthy |
| 등록 Task | 11 |
| Catalog Task Run | 12 |
| Project | 3 |
| Project Run 완료 | 3 |
| Run 검색 (ORCHAGY) | 12 |
| Artifact 검색 | 20 |
| 실패 Run 검색 | 1 |
| Artifact content field | `text` |

---

## 2. 사전 준비 (agy 전용)

검증 전에 다음을 맞췄다.

1. **PATH / 바이너리**  
   - 죽은 경로 `Local\agy\bin` 복구 (WinGet `agy.exe` 하드링크)  
   - User PATH에 canonical bin + WinGet 패키지 경로
2. **Relay 설정**  
   - `service_isolation_acknowledged=true`  
   - `workers.antigravity.security_verified=true`  
   - `workers.antigravity.full_access_mode=true`  
   - `workers.antigravity.command` = 절대 경로  
   - `relay config enable-worker antigravity`
3. **Deep doctor**  
   - `relay doctor --worker antigravity --deep` → healthy

### 2.1 검증 중 발견한 agy 연동 이슈와 수정

| 문제 | 증상 | 조치 |
|---|---|---|
| PATH 단절 | `agy` / doctor `executable: null` | `Local\agy\bin` 하드링크 + PATH 정리 |
| scratch 경로 | agy가 cwd 대신 `~/.gemini/antigravity-cli/scratch`에 결과 기록 | adapter가 **절대 경로** + `--add-dir` 사용, scratch 폴백 읽기 |
| JSON 파싱 | stdout이 설명문일 때 `INVALID_JSON` | result 파일/scratch/markdown fence 처리 강화 (`relay/adapters/antigravity.py`) |
| doctor artifact 경로 | `artifacts/probe-artifact.txt` vs `probe-artifact.txt` | doctor probe artifact 판정 완화 (`relay/doctor.py`) |

이 수정 없이는 deep doctor와 Task Run이 간헐적으로 실패했다. 수정 후 deep doctor와 smoke Task Run, 전체 시나리오가 통과했다.

---

## 3. 시나리오 결과

### S1 — 단독 Task 결과 이어받기 ✅

```text
Collect source material
  → Artifact 01KZ615GR78ECFA81M6TW0N2ZT
  → Summarize source material (입력 A1)
```

| 항목 | 값 |
|---|---|
| 원본 Task Run | `01KZ614V12PYBHYRWYFM4JAZ4E` completed |
| 소비 Task Run | `01KZ615GT3T52QQXMNNTYVB4Y0` completed |
| lineage count | 1 |
| result_summary (원본) | Collected brief source material… ORCHAGY |

### S2 — 순차 Project ✅

**Market research report** · Run `01KZ6168KAPHKGKJ9WNTNJSZRG`

```text
research → clean → report
```

모든 step `completed` (3/3).

### S3 — 병렬 후 통합 ✅

**Product launch review** · Run `01KZ618AAQEFMJ4QQ7BSHM3ZCA`

```text
market ─┐
        ├─ synthesis
risk   ─┘
```

모든 step `completed` (3/3).

### S4 — Project 간 Artifact 재사용 ✅

Project A report Artifact `01KZ618A6G79A9V82QAA04AGE8` → Project C external input.

| 항목 | 값 |
|---|---|
| consumer Project Run | `01KZ61AFV6GSC060KTB695BEKM` completed |
| external_input_count | 1 |
| source lineage consumers | 1 |

### S5 — 실패 + 재실행 ✅

Worker 명령을 깨뜨린 뒤 복구.

| Run | status | 비고 |
|---|---|---|
| `01KZ61BZG56Y9DD2ZXHG0N68EE` | failed | `WORKER_UNVERIFIED` / failure_reason 보존 |
| `01KZ61BZJ2ER13GT5WAKSXZQ0H` | completed | Artifact `01KZ61CKAN6738GY1W2V34ZF3Q` |

### S6 — Catalog / 검색 / 본문 ✅

| 조회 | 결과 |
|---:|---:|
| Task Catalog | 11 |
| Task Run Catalog | 12 |
| Project Catalog | 3 |
| Project Run Catalog | 3 |
| Run 검색 ORCHAGY | 12 |
| Artifact 검색 | 20 |
| content.available | true (`text`) |

---

## 4. mock 검증과의 차이

| | mock Codex (v1.1) | **real agy (본 보고서)** |
|---|---|---|
| Worker | `mocks/codex.cmd` | `agy` 1.1.10 |
| 추론 | 고정 문자열 | 실제 Antigravity 응답 |
| 품질 보증 | 없음 | 없음 (계약/오케스트레이션만) |
| 사전 조건 | 거의 없음 | isolation ack, security_verified, deep doctor, PATH |
| 실패 모드 | 거의 없음 | scratch 경로, JSON 형식, 간헐 crash |

본 검증은 **“agy가 똑똑한가”가 아니라 “Relay가 실제 agy로 Task/Project/Artifact 루프를 돌릴 수 있는가”** 를 확인한다.

---

## 5. 재현

```powershell
# PATH / agy 확인
agy --version

# 설정 (최초 1회)
relay config set service_isolation_acknowledged true
relay config set workers.antigravity.full_access_mode true
relay config set workers.antigravity.security_verified true
relay config set workers.antigravity.command "$env:LOCALAPPDATA\agy\bin\agy.exe"
relay doctor --worker antigravity --deep
relay config enable-worker antigravity

# 시나리오
cd D:\APPs\Relay-agent
D:\Python314\python.exe tests\test_agent_orchestration_agy.py
```

---

## 6. 결론

**실제 Antigravity(`agy`) Worker로 오케스트레이션 시나리오 S1–S6 전부 통과했다.**

Relay는 다음을 실제 Worker 경로에서 수행했다.

1. Task 등록·실행  
2. Artifact UID 입력 연결·lineage  
3. 순차/병렬 Project Run  
4. Project 간 Artifact 재사용  
5. 실패 영수증 보존 후 재실행  
6. Catalog·검색·본문 조회  

추가로, agy 1.1.10의 scratch 출력 습성에 맞춘 adapter/doctor 보정이 이번 검증의 전제 조건이었다.
