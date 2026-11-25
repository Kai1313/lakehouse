import time
from typing import Sequence

import litellm

from app.config import settings


def embed_texts(
    texts: Sequence[str],
    *,
    batch_size: int = 20,
    max_retries: int = 3,
) -> list[list[float]]:
    """Generates embeddings via LiteLLM using the configured external embedding provider."""
    if not texts:
        return []

    vectors: list[list[float]] = []
    model = settings.embedding_model
    dim = settings.embedding_dim

    for start in range(0, len(texts), batch_size):
        batch = list(texts[start : start + batch_size])
        last_err: Exception | None = None

        for attempt in range(max_retries):
            try:
                kwargs: dict = {"model": model, "input": batch}
                if dim:
                    kwargs["dimensions"] = dim
                resp = litellm.embedding(**kwargs)
                data = sorted(resp["data"], key=lambda item: item["index"])
                for item in data:
                    vec = item["embedding"]
                    if dim and len(vec) != dim:
                        raise ValueError(
                            f"Embedding dimension mismatch: expected {dim}, got {len(vec)} for model '{model}'"
                        )
                    vectors.append(vec)
                last_err = None
                break
            except Exception as exc:
                last_err = exc
                if attempt < max_retries - 1:
                    time.sleep(1.5 * (2**attempt))

        if last_err is not None:
            raise RuntimeError(f"Embedding generation failed for model '{model}': {last_err}") from last_err

    return vectors
