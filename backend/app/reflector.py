"""Reflector — auto-extract durable facts from a conversation and store them.

Runs AFTER a turn (typically as a queued job so it never blocks the reply). It
asks the LLM to pull out stable, self-contained facts worth remembering, then
stores each one with an embedding, deduping against semantically-similar memories
already on file.
"""

from __future__ import annotations

import json
import logging
from typing import List

from sqlalchemy.orm import Session

from app import vectorstore
from app.config import settings
from app.embeddings import embed
from app.llm import create_message
from app.memory import load_history
from app.models import Memory

log = logging.getLogger(__name__)

_EXTRACT_SYSTEM = (
    "You extract durable, long-term facts about the user from a conversation. "
    "Return ONLY a JSON array of objects: "
    '[{"content": "<self-contained fact>", "category": "people|projects|preferences|context|general", '
    '"sensitive": false}]. '
    "Include only stable facts worth remembering later (preferences, people, "
    "projects, standing context). Exclude one-off requests, questions, small talk, "
    "and anything transient. If nothing is worth saving, return []."
)


def _parse_facts(raw: str) -> List[dict]:
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("```", 2)[1] if "```" in raw[3:] else raw.strip("`")
        raw = raw[raw.find("[") :] if "[" in raw else raw
    start, end = raw.find("["), raw.rfind("]")
    if start == -1 or end == -1:
        return []
    try:
        data = json.loads(raw[start : end + 1])
    except Exception:
        return []
    out = []
    for item in data if isinstance(data, list) else []:
        if isinstance(item, dict) and item.get("content"):
            out.append(item)
    return out


def extract_facts(conversation_text: str) -> List[dict]:
    """Call the LLM (Haiku router model) to pull candidate facts."""
    resp = create_message(
        system=_EXTRACT_SYSTEM,
        messages=[{"role": "user", "content": conversation_text}],
        model=settings.jarvis_router_model,
    )
    text_parts = [b.text for b in resp.content if getattr(b, "type", None) == "text"]
    return _parse_facts("\n".join(text_parts))


def store_fact(db: Session, content: str, category: str = "general", sensitive: bool = False,
               source: str = "reflector") -> Memory | None:
    """Store a fact unless a near-duplicate already exists (semantic dedup)."""
    content = content.strip()
    if not content:
        return None
    vec = embed(content)
    existing, sim = vectorstore.most_similar(db, content)
    if existing is not None and sim >= settings.memory_dedup_threshold:
        return None  # already know this
    m = Memory(content=content, category=category, source=source, sensitive=bool(sensitive))
    vectorstore.add(db, m, vec)
    return m


def reflect_conversation(db: Session, conversation_id: int) -> int:
    """Extract + store facts from a conversation. Returns count stored."""
    if not settings.enable_reflector:
        return 0
    vectorstore.ensure_ready(db)
    history = load_history(db, conversation_id, limit=20)
    if not history:
        return 0
    convo_text = "\n".join(f"{h['role']}: {h['content']}" for h in history)
    stored = 0
    for fact in extract_facts(convo_text):
        if store_fact(
            db,
            content=fact["content"],
            category=fact.get("category", "general"),
            sensitive=bool(fact.get("sensitive", False)),
        ):
            stored += 1
    log.info("reflector: stored %d fact(s) from conversation %s", stored, conversation_id)
    return stored
