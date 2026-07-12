"""General handler — lets JARVIS persist things it learns about you.

Phase 0: an explicit `remember_fact` tool. Phase 1 adds an automatic reflector
that extracts facts without the model having to call a tool.
"""

from app.handlers.base import Context, Registry
from app.memory import remember


def _remember_fact(args: dict, ctx: Context) -> str:
    content = args["content"].strip()
    category = args.get("category", "general")
    sensitive = bool(args.get("sensitive", False))
    remember(ctx.db, content=content, category=category, source=ctx.channel, sensitive=sensitive)
    return f"Noted and remembered: {content}"


def _recall_facts(args: dict, ctx: Context) -> str:
    """What does she actually believe about the user? Being able to ASK is the
    prerequisite for being able to CORRECT."""
    from sqlalchemy import select

    from app.models import Memory

    q = (args.get("about") or "").strip().lower()
    rows = (
        ctx.db.execute(select(Memory).order_by(Memory.created_at.desc()).limit(200))
        .scalars()
        .all()
    )
    if q:
        rows = [m for m in rows if q in m.content.lower()]
    if not rows:
        return "Nothing on file about that." if q else "No learned facts yet."

    rows = rows[:25]
    return f"{len(rows)} learned fact(s):\n" + "\n".join(
        f"#{m.id}: {m.content}" for m in rows
    )


def _forget_fact(args: dict, ctx: Context) -> str:
    """Delete a learned fact.

    JARVIS could remember but NOT forget, which meant a wrong fact was permanent.
    That's how "he lives in Anacortes" — inferred from a conversation about
    driving to a boat — survived being contradicted by his configured address.

    Not gated: deleting a wrong belief about the user is a correction, not a
    destructive act. Requiring confirmation to fix a mistake would be perverse.
    """
    from sqlalchemy import select

    from app.models import Memory

    fid = args.get("fact_id")
    if fid is not None:
        m = ctx.db.get(Memory, int(fid))
        if m is None:
            return f"No fact #{fid}."
        content = m.content
        ctx.db.delete(m)
        ctx.db.commit()
        return f"Forgotten: {content}"

    about = (args.get("about") or "").strip().lower()
    if not about:
        return "Which fact? Give a fact number from recall_facts, or say what it's about."

    rows = [
        m for m in ctx.db.execute(select(Memory)).scalars().all()
        if about in m.content.lower()
    ]
    if not rows:
        return f"Nothing on file about '{args.get('about')}'."
    if len(rows) > 1:
        listed = "\n".join(f"#{m.id}: {m.content}" for m in rows[:10])
        return f"Several match — which one?\n{listed}"

    content = rows[0].content
    ctx.db.delete(rows[0])
    ctx.db.commit()
    return f"Forgotten: {content}"


def register(reg: Registry) -> None:
    reg.register(
        {
            "name": "remember_fact",
            "description": (
                "Save a durable fact about the user or their world for future "
                "recall (preferences, people, projects, context). Use when the user "
                "shares something worth remembering long-term."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "content": {"type": "string", "description": "The fact to remember, self-contained."},
                    "category": {"type": "string", "description": "e.g. people, projects, preferences, context"},
                    "sensitive": {"type": "boolean", "description": "True for sensitive personal info."},
                },
                "required": ["content"],
            },
        },
        _remember_fact,
    )
    reg.register(
        {
            "name": "recall_facts",
            "description": (
                "List what JARVIS has LEARNED about the user (inferred from conversation, "
                "and possibly wrong). Use when they ask what you know or remember about "
                "them, or when you suspect a belief of yours is out of date."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "about": {"type": "string",
                              "description": "Filter, e.g. 'address'. Omit for everything."},
                },
            },
        },
        _recall_facts,
    )
    reg.register(
        {
            "name": "forget_fact",
            "description": (
                "Delete a learned fact that is WRONG or out of date. Use whenever the user "
                "corrects you about something you believed. Learned facts are inferred from "
                "conversation and are sometimes simply mistaken."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "fact_id": {"type": "integer", "description": "From recall_facts."},
                    "about": {"type": "string",
                              "description": "Or describe it, e.g. 'lives in Anacortes'."},
                },
            },
        },
        _forget_fact,
    )
