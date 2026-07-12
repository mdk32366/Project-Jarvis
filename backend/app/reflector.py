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

_EXTRACT_SYSTEM_BASE = (
    "You extract durable, long-term facts about the user from a conversation. "
    "Return ONLY a JSON array of objects: "
    '[{"content": "<self-contained fact>", "category": "people|projects|preferences|context|general", '
    '"sensitive": false}]. '
    "Include only stable facts worth remembering later (preferences, people, "
    "projects, standing context). Exclude one-off requests, questions, small talk, "
    "and anything transient. If nothing is worth saving, return []."
    "\n\n"
    "DO NOT INFER FACTS THAT ARE ALREADY KNOWN. Anything in the AUTHORITATIVE "
    "block below is configured by the user directly and is not up for inference. "
    "Never write a fact that contradicts it, and never restate it.\n"
    "This is not hypothetical: a conversation about driving to a boat in Anacortes "
    "once produced the 'fact' that the user LIVED there. He does not — his address "
    "is configured, and the guess overrode it. A place someone is TRAVELLING TO is "
    "not where they LIVE. A place they MENTION is not where they WORK. If a "
    "conversation seems to imply otherwise, the conversation is about a trip.\n"
    "When in doubt, save nothing. A wrong durable fact is far worse than a missing "
    "one, because it will be trusted later and nobody will know where it came from."
    "\n\n"
    "NEVER SAVE WHAT WAS READ ON THE WEB. If the conversation contains search "
    "results, article text, or anything retrieved from the internet, that is not a "
    "fact ABOUT THE USER. It is something they LOOKED AT.\n"
    "The distinction is not pedantic. 'The user asked about the Fed rate' is a fact "
    "about him; 'the Fed rate is 4.5%' is not \u2014 it is a fact about the world, it "
    "will go stale, and saving it as something JARVIS 'knows about him' poisons the "
    "memory with unbounded internet content that nobody can audit.\n"
    "Save only what the USER SAID about themselves, their people, their projects, "
    "and their preferences."
)


def _extract_system() -> str:
    """The extraction prompt, with the owner's ground truth attached so the
    reflector can't 'learn' something that contradicts it."""
    from app.memory import _owner_identity

    identity = _owner_identity()
    if not identity:
        return _EXTRACT_SYSTEM_BASE
    return (
        _EXTRACT_SYSTEM_BASE
        + "\n\n## AUTHORITATIVE (already known — never contradict, never restate):\n"
        + "\n".join(f"- {line}" for line in identity)
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
        system=_extract_system(),
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
