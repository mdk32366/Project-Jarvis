"""Text embeddings for the memory reflector.

Two backends:
  * "voyage": Anthropic-recommended Voyage AI embeddings (needs VOYAGE_API_KEY).
  * "local":  deterministic, offline hashing embedder. Zero cost, no network —
              good enough for dedup/recall of a single user's facts, and lets the
              reflector work in dev/CI/prod without any extra API key.

Both return a unit-normalized vector of length settings.embedding_dim so cosine
similarity is just a dot product.
"""

from __future__ import annotations

import hashlib
import math
from typing import List

from app.config import settings


def _normalize(vec: List[float]) -> List[float]:
    norm = math.sqrt(sum(x * x for x in vec))
    if norm == 0:
        return vec
    return [x / norm for x in vec]


def _local_embed(text: str, dim: int) -> List[float]:
    """Deterministic bag-of-tokens hashing embedder.

    Each token is hashed into a bucket with a signed weight. Similar texts share
    tokens and therefore point in similar directions. Not semantic like a real
    model, but stable and dependency-free.
    """
    vec = [0.0] * dim
    tokens = [t for t in "".join(c.lower() if c.isalnum() else " " for c in text).split() if t]
    for tok in tokens:
        h = hashlib.sha256(tok.encode("utf-8")).digest()
        bucket = int.from_bytes(h[:4], "big") % dim
        sign = 1.0 if h[4] & 1 else -1.0
        vec[bucket] += sign
    return _normalize(vec)


def _voyage_embed(texts: List[str]) -> List[List[float]]:
    import httpx

    resp = httpx.post(
        "https://api.voyageai.com/v1/embeddings",
        headers={"Authorization": f"Bearer {settings.voyage_api_key}"},
        json={"input": texts, "model": settings.voyage_model},
        timeout=30.0,
    )
    resp.raise_for_status()
    data = resp.json()["data"]
    return [_normalize(item["embedding"]) for item in data]


def embed(text: str) -> List[float]:
    return embed_batch([text])[0]


def embed_batch(texts: List[str]) -> List[List[float]]:
    if settings.embedding_provider == "voyage" and settings.voyage_api_key:
        try:
            return _voyage_embed(texts)
        except Exception:
            # Fall back to local so the reflector never hard-fails on a network blip.
            pass
    return [_local_embed(t, settings.embedding_dim) for t in texts]


def cosine(a: List[float], b: List[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    return sum(x * y for x, y in zip(a, b))  # inputs are unit-normalized
