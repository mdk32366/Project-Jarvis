"""Memory: assemble the 'think like me' context injected into every request.

Phase 0 loads the always-on layers (persona + standing preferences) and recent
conversation history. Phase 1 adds the reflector (auto-extract facts) and
pgvector similarity retrieval over the `memories` table.
"""

from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Conversation, Memory, Message, PersonaProfile, Preference


def build_system_preamble(db: Session, query: str = "") -> str:
    """Compose the persona + preferences block that precedes JARVIS's instructions.

    When ``query`` is given, learned facts are retrieved by semantic similarity
    (pgvector in prod, in-Python cosine in dev). Falls back to the most recent
    facts when there is no query or no embeddings yet.
    """
    persona = db.execute(select(PersonaProfile)).scalars().all()
    prefs = db.execute(select(Preference)).scalars().all()

    parts: list[str] = [
        "You are JARVIS, a personal majordomo and chief of staff for your principal.",
        "Act in their interest, in their voice, and reflect how they think and decide.",
    ]

    if persona:
        parts.append("\n## Who your principal is (persona — emulate this):")
        by_cat: dict[str, list[str]] = {}
        for p in persona:
            by_cat.setdefault(p.category, []).append(p.content)
        for cat, items in by_cat.items():
            parts.append(f"\n### {cat.title()}")
            parts.extend(f"- {it}" for it in items)

    if prefs:
        parts.append("\n## Standing preferences (how they like things done — follow these):")
        parts.extend(f"- {p.key}: {p.value}" for p in prefs)

    facts = _relevant_facts(db, query)
    if facts:
        parts.append("\n## Things you've learned about them:")
        parts.extend(f"- {m.content}" for m in facts)

    return "\n".join(parts)


def _relevant_facts(db: Session, query: str, limit: int = 10):
    """Semantic recall when possible; otherwise most-recent facts."""
    if query:
        try:
            from app import vectorstore

            hits = vectorstore.search(db, query)
            if hits:
                return [m for m, _sim in hits]
        except Exception:
            pass  # fall back to recency
    return (
        db.execute(select(Memory).order_by(Memory.created_at.desc()).limit(limit))
        .scalars()
        .all()
    )


def get_or_create_conversation(
    db: Session, channel: str, thread_key: str, subject: str = ""
) -> Conversation:
    convo = (
        db.execute(
            select(Conversation)
            .where(Conversation.channel == channel)
            .where(Conversation.thread_key == thread_key)
        )
        .scalars()
        .first()
    )
    if convo is None:
        convo = Conversation(channel=channel, thread_key=thread_key, subject=subject)
        db.add(convo)
        db.commit()
        db.refresh(convo)
    return convo


def load_history(db: Session, conversation_id: int, limit: int = 20) -> list[dict]:
    """Return recent messages as Anthropic-style {role, content} dicts (chronological)."""
    rows = (
        db.execute(
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.created_at.desc())
            .limit(limit)
        )
        .scalars()
        .all()
    )
    rows = list(reversed(rows))
    return [{"role": r.role, "content": r.content} for r in rows if r.role in ("user", "assistant")]


def add_message(db: Session, conversation_id: int, role: str, content: str) -> None:
    db.add(Message(conversation_id=conversation_id, role=role, content=content))
    db.commit()


def remember(
    db: Session,
    content: str,
    category: str = "general",
    source: str = "conversation",
    sensitive: bool = False,
) -> Memory:
    """Store a durable fact (dedup by exact content)."""
    existing = (
        db.execute(select(Memory).where(Memory.content == content)).scalars().first()
    )
    if existing:
        return existing
    m = Memory(content=content, category=category, source=source, sensitive=sensitive)
    try:
        from app import vectorstore

        vectorstore.add(db, m)  # commits + stores embedding for semantic recall
    except Exception:
        db.add(m)
        db.commit()
        db.refresh(m)
    return m
