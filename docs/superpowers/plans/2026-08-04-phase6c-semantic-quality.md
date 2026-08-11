# Phase 6c Semantic Search and Quality Scoring Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans.

**Goal:** Add pluggable embedding-backed semantic search and deterministic quality scoring.

**Architecture:** An EmbeddingBackend interface with a NullEmbedding default (falls back to Phase 2 FTS5). A QualityService computes a deterministic score from existing Run fields. Semantic search returns ranked summaries without raw content.

**Tech Stack:** Python 3.11+, SQLite FTS5 (existing), unittest, Ruff.

## Global Constraints

- Phase 6c is daemon/API/CLI core only; no GUI.
- No embedding model dependency is bundled; the interface is pluggable.
- Without a configured backend, search degrades to Phase 2 FTS5 and reports the fallback.
- Quality scoring derives only from existing Run fields; never invents facts.
- `relay-receipt.json`, `test_result.json`, `test_task.md` never staged.
- Version stays 1.1.0; work on `feat/phase0-domain-compat`.

---

### Task 1: Embedding backend interface + NullEmbedding

**Files:** Create `relay/search/embedding.py`; Create `tests/test_phase6c_embedding.py`

**Interfaces:**
- `EmbeddingBackend` ABC: `embed(text) -> list[float] | None`, `available() -> bool`.
- `NullEmbedding` returns None / not available.
- `get_embedding_backend(config) -> EmbeddingBackend` factory.

- [ ] **Step 1: Write failing tests** — NullEmbedding.available() is False; factory returns NullEmbedding by default.
- [ ] **Step 2: Run RED.**
- [ ] **Step 3: Implement interface + NullEmbedding + factory.**
- [ ] **Step 4: Run GREEN and commit** — `feat: add embedding backend interface (phase 6c)`.

### Task 2: Semantic search service

**Files:** Create `relay/search/semantic.py`; Create `tests/test_phase6c_search.py`

**Interfaces:**
- `semantic_search(db, backend, query, kind, limit) -> dict` — if backend available, rank by vector similarity; else fall back to FTS5 and set `fallback=True`.

- [ ] **Step 1: Write failing tests** — NullEmbedding falls back to FTS5 with `fallback=True`; results are summaries (no raw content dump).
- [ ] **Step 2: Run RED.**
- [ ] **Step 3: Implement** semantic_search with fallback path reusing Phase 2 search_runs/search_artifacts.
- [ ] **Step 4: Run GREEN and commit** — `feat: add semantic search with FTS5 fallback (phase 6c)`.

### Task 3: Quality scoring service

**Files:** Create `relay/quality/service.py`; Create `tests/test_phase6c_quality.py`

**Interfaces:**
- `QualityService.score_run(db, run_id) -> dict` — deterministic score struct from Run fields.
- `QualityService.attention_runs(db, status_filter, limit) -> list`.

- [ ] **Step 1: Write failing tests** — completed+0 uncertainties → high; failed → low; partial → medium; missing_items → medium/low.
- [ ] **Step 2: Run RED.**
- [ ] **Step 3: Implement** scoring rules per spec section 4.
- [ ] **Step 4: Run GREEN and commit** — `feat: add quality scoring service (phase 6c)`.

### Task 4: API + daemon routes + CLI

**Files:** Modify `relay/api.py`, `relay/daemon.py`, `relay/cli.py`; Create `tests/test_phase6c_api.py`, `tests/test_phase6c_cli.py`

- [ ] **Step 1: Write failing tests.**
- [ ] **Step 2: Run RED.**
- [ ] **Step 3: Add routes** (`POST /v1/search/semantic`, `GET /v1/runs/{id}/quality`, `GET /v1/quality/attention`) and CLI (`relay search semantic`, `relay run quality`, `relay quality attention`).
- [ ] **Step 4: Run GREEN and commit** — `feat: expose semantic search and quality API/CLI (phase 6c)`.

### Task 5: Final verification

- [ ] Full suite + Ruff + compileall + git diff --check + log.md.

## Stop Gate

- Semantic search returns ranked summaries without raw content.
- Without embedding backend, degrades to FTS5 and reports fallback.
- Quality scoring is deterministic and derived only from existing fields.
- Low-quality Runs discoverable via attention endpoint.
- Full suite, Ruff, compileall pass.
