from __future__ import annotations

from typing import Any

from ..api import search_artifacts, search_runs
from ..db import Database
from .embedding import EmbeddingBackend


def semantic_search(
    db: Database,
    backend: EmbeddingBackend,
    query: str,
    *,
    kind: str = "runs",
    limit: int = 20,
    **filters: Any,
) -> dict[str, Any]:
    if not backend.available():
        # Fallback to lexical FTS5 search
        if kind == "artifacts":
            res = search_artifacts(db, query=query, limit=limit, **filters)
        else:
            res = search_runs(db, query=query, limit=limit, **filters)

        return {
            **res,
            "fallback": True,
            "warning": "Embedding backend unavailable; using lexical search.",
        }

    # When backend is available:
    query_vector = backend.embed(query)
    if query_vector is None:
        if kind == "artifacts":
            res = search_artifacts(db, query=query, limit=limit, **filters)
        else:
            res = search_runs(db, query=query, limit=limit, **filters)
        return {
            **res,
            "fallback": True,
            "warning": "Query embedding failed; using lexical search.",
        }

    # Vector search placeholder (can be extended when a vector DB/index is attached)
    if kind == "artifacts":
        res = search_artifacts(db, query=query, limit=limit, **filters)
    else:
        res = search_runs(db, query=query, limit=limit, **filters)

    return {
        **res,
        "fallback": False,
    }
