"""Semantic memory store over the `memories` table.

Production (Postgres + pgvector): a parallel `memory_embeddings` table holds a
real `vector` column and similarity search uses the `<=>` operator (ANN-ready).

Dev / tests (SQLite, or use_pgvector off): embeddings are read from the JSON
`memories.embedding` column and cosine similarity is computed in Python.

Both paths implement the same tiny interface: add(), search(), all_embeddings().
"""

from __future__ import annotations

import json
from typing import List, Optional, Tuple

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.config import settings
from app.embeddings import cosine, embed
from app.models import Memory


def _use_pg(db: Session) -> bool:
    return settings.use_pgvector and settings.is_postgres and db.bind is not None and db.bind.dialect.name == "postgresql"


def ensure_ready(db: Session) -> None:
    """Create the pgvector extension + embedding table when on Postgres."""
    if not _use_pg(db):
        return
    db.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
    db.execute(
        text(
            f"CREATE TABLE IF NOT EXISTS memory_embeddings ("
            f"  memory_id INTEGER PRIMARY KEY REFERENCES memories(id) ON DELETE CASCADE,"
            f"  embedding vector({settings.embedding_dim})"
            f")"
        )
    )
    db.commit()


def add(db: Session, memory: Memory, vector: Optional[List[float]] = None) -> None:
    """Persist a memory's embedding (JSON column always; pgvector table in prod)."""
    vec = vector if vector is not None else embed(memory.content)
    memory.embedding = json.dumps(vec)
    db.add(memory)
    db.commit()
    if _use_pg(db):
        db.execute(
            text(
                "INSERT INTO memory_embeddings (memory_id, embedding) VALUES (:id, (:emb)::vector) "
                "ON CONFLICT (memory_id) DO UPDATE SET embedding = EXCLUDED.embedding"
            ),
            {"id": memory.id, "emb": "[" + ",".join(str(x) for x in vec) + "]"},
        )
        db.commit()


def search(db: Session, query: str, k: Optional[int] = None) -> List[Tuple[Memory, float]]:
    """Return up to k (memory, similarity) pairs most similar to the query text."""
    k = k or settings.memory_recall_k
    qvec = embed(query)
    if _use_pg(db):
        emb_literal = "[" + ",".join(str(x) for x in qvec) + "]"
        rows = db.execute(
            text(
                "SELECT memory_id, 1 - (embedding <=> (:q)::vector) AS sim "
                "FROM memory_embeddings ORDER BY embedding <=> (:q)::vector LIMIT :k"
            ),
            {"q": emb_literal, "k": k},
        ).all()
        out: List[Tuple[Memory, float]] = []
        for mid, sim in rows:
            m = db.get(Memory, mid)
            if m is not None:
                out.append((m, float(sim)))
        return out
    # Portable fallback: cosine in Python over stored JSON embeddings.
    scored: List[Tuple[Memory, float]] = []
    for m in db.execute(select(Memory)).scalars().all():
        if not m.embedding:
            continue
        try:
            mvec = json.loads(m.embedding)
        except Exception:
            continue
        scored.append((m, cosine(qvec, mvec)))
    scored.sort(key=lambda t: t[1], reverse=True)
    return scored[:k]


def most_similar(db: Session, query: str) -> Tuple[Optional[Memory], float]:
    hits = search(db, query, k=1)
    return hits[0] if hits else (None, 0.0)
