# Phase 6c Semantic Search and Quality Scoring Design

**Date:** 2026-08-04
**Status:** Approved
**Source:** Phase 6 (6c) of docs/relay_product_direction_v1.0.md

## 1. Goal

Add embedding-backed semantic search across Runs and Artifacts (complementing Phase 2 lexical FTS5), and structured result quality scoring that surfaces low-quality or partial results.

## 2. Scope

6c includes:

- Pluggable embedding backend interface with a local default (hash/keyword fallback when no embedding model is configured).
- Semantic Artifact/Run search ranked by vector similarity, returning summaries and IDs (Agent context is not blown up).
- Quality scoring of Task/Project Run results from receipt fields (status, uncertainties, missing_items, validation status).
- Needs-attention flagging of low-quality Runs surfaced via the 6d inbox.
- Authenticated daemon APIs and machine-readable CLI commands.

6c excludes:

- Bundling a specific embedding model dependency. The interface is pluggable; a real model is configured by the operator.
- Automatic result rejection (scoring surfaces; humans/routines decide).
- Re-ranking with a cross-encoder.

## 3. Embedding Backend Interface

```text
EmbeddingBackend.embed(text) -> vector
EmbeddingBackend.search(query_vector, k) -> [(id, score)]
```

Default backend: `NullEmbedding` returns no vectors and search falls back to Phase 2 FTS5. A configured backend (e.g., a local sentence-transformer or an HTTP embedding service) implements the interface. The daemon picks the backend from config at startup.

## 4. Quality Scoring

A Run quality score is a deterministic struct derived from existing fields:

```text
{
  "status_ok": bool,
  "uncertainty_count": int,
  "missing_count": int,
  "artifact_count": int,
  "validation_status": str | null,
  "score": "high" | "medium" | "low" | "unknown"
}
```

Scoring rules are explicit (e.g., failed -> low; complete with 0 uncertainties and >=1 artifact -> high; partial or missing items -> medium/low). Scoring never invents facts not present in the Run.

## 5. API

```text
POST /v1/search/semantic   {"query": "...", "kind": "runs|artifacts", "limit": 10}
GET  /v1/runs/{id}/quality
GET  /v1/quality/attention?status=low
```

Stable errors: EMBEDDING_UNAVAILABLE (falls back to lexical with a warning), SEMANTIC_INDEX_EMPTY.

## 6. CLI

```text
relay search semantic "<query>" --kind artifacts --limit 10
relay run quality <run-id>
relay quality attention --status low
```

## 7. Completion Criteria

- Semantic search returns ranked Run/Artifact summaries without dumping raw content.
- With no embedding backend configured, search degrades to Phase 2 FTS5 and reports the fallback.
- Quality scoring is deterministic and derived only from existing Run fields.
- Low-quality Runs are discoverable via the attention endpoint.
- CLI, daemon API, service, and embedding interface each have focused tests; full suite, Ruff, and compileall pass.
