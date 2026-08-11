# Relay Machine Read Contract v1.0

Relay Agent가 CLI/API 응답을 해석할 때 사용하는 canonical read fields다. 기존 compatibility alias는 제거하지 않지만, Agent는 canonical field를 우선한다.

## Common rules

| Rule | Contract |
|---|---|
| Catalog schema | `catalog_schema_version=1` |
| List envelope | `kind`, `items`, `next_cursor`, `has_more` |
| Cursor | opaque URL-safe string |
| Invalid cursor | `INVALID_CURSOR` |
| Catalog status | lowercase |
| Full detail | 별도 detail path에서만 조회 |

## Capability

```text
relay catalog --machine
GET /v1/catalog
```

The capability response declares supported `kinds`, list paths, detail templates, ordering, and response metadata.

## Canonical fields

| Resource | List field | Detail path | Important fields |
|---|---|---|---|
| Task | `items` | `/v1/tasks/{task_id}` | `task_id`, `name`, `version`, `task_summary` |
| Task Run | `items` | `/v1/task-runs/{task_run_id}` | `task_run_id`, `status`, summaries, Artifact metadata |
| Project | `items` | `/v1/projects/{project_id}` | `project_id`, `version`, `project_summary`, node counts |
| Project Run | `items` | `/v1/project-runs/{project_run_id}` | `project_run_id`, `status`, step counts, failure reason |
| Artifact content | N/A | `/v1/artifacts/{artifact_uid}/content` | `available`, `text`, `truncated` |

## Compatibility aliases

Existing non-Catalog endpoints may also return:

- Project list: `projects`
- Project Run list: `project_runs`
- legacy execution identifiers and routes

Use `items` whenever present. Do not assume that an alias is the only list key.

## Artifact content example

```json
{
  "ok": true,
  "artifact_uid": "01K...",
  "available": true,
  "text": "UTF-8 content",
  "size": 123,
  "truncated": false,
  "mime_type": "text/markdown"
}
```

The canonical body key is `text`, not `content`.

## Selection workflow

```text
catalog list
→ summary and metadata comparison
→ selected detail
→ execution or reuse
→ receipt/result/artifact
→ Lineage verification
```

Relay does not return query scores, similarity, recommendation, or automatic selection.
