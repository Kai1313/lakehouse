from typing import Any

import litellm

from app import db
from app.config import settings


def search_documents(
    query: str,
    filename: str | None = None,
    top_k: int = 5,
) -> dict[str, Any]:
    """Semantic search over Gold PDF chunks stored in pgvector."""
    clean_query = (query or "").strip()
    if not clean_query:
        return {"results": [], "count": 0, "error": "Query cannot be empty."}

    clamped_k = max(1, min(int(top_k or 5), 12))
    resp = litellm.embedding(
        model=settings.embedding_model,
        input=[clean_query],
        dimensions=settings.embedding_dim,
    )
    query_vec = resp["data"][0]["embedding"]

    rows = db.search_document_chunks(
        query_vec,
        top_k=clamped_k,
        filename=filename.strip() if filename else None,
    )

    results: list[dict[str, Any]] = []
    for row in rows:
        results.append(
            {
                "chunk_id": str(row["id"]),
                "filename": row["filename"],
                "page_number": row["page_number"],
                "chunk_index": row["chunk_index"],
                "score": round(float(row["score"]), 4),
                "content": row["content"],
                "metadata": row["metadata_json"] or {},
            }
        )

    return {"results": results, "count": len(results)}
