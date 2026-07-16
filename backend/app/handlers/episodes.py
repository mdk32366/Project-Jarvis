"""Episode recall tools — "remember when we talked about..." (TDD #14 §5).

Three tools, all archivist-rostered:

  * recall_episodes — hybrid search over distilled conversations (meaning ×
    date × topic). The dated answer to "a couple of years ago."
  * recall          — the unified surface: episodes AND learned facts, merged
    with explicit precedence (quote > summary > inferred fact), each item
    labeled with its provenance so the model knows how much to trust it.
  * forget_episode  — corrections. Memory you can't correct is worse than none.
    Ungated, same reasoning as forget_fact.

Episodes are NOT injected into every preamble — a decade of history cannot
ride in the context window. They are retrieved here, on demand, when a query
implicates the past.
"""

from __future__ import annotations

import json
from datetime import date

from app.handlers.base import Context, Registry


def _parse_date(s: str) -> date | None:
    try:
        return date.fromisoformat(str(s).strip())
    except (TypeError, ValueError):
        return None


def _format_episode(ep, quotes=True) -> str:
    try:
        topics = ", ".join(json.loads(ep.topics or "[]"))
    except ValueError:
        topics = ""
    lines = [f"[{ep.occurred_on.isoformat()}] {ep.title}"
             + (f"  (topics: {topics})" if topics else "")]
    lines.append(f"  {ep.summary}")
    if quotes:
        for q in ep.quotes:
            who = "You" if q.speaker == "owner" else "JARVIS"
            lines.append(f'  {q.kind.upper()} — {who} said, verbatim: "{q.quote}"')
    return "\n".join(lines)


def _recall_episodes(args: dict, ctx: Context) -> str:
    from app.episodic import search_episodes

    query = str(args.get("query", "")).strip()
    if not query:
        return "What should I look for in past conversations?"
    since = _parse_date(args.get("since", ""))
    until = _parse_date(args.get("until", ""))
    topic = str(args.get("topic", "")).strip()

    hits = search_episodes(ctx.db, query, since=since, until=until, topic=topic)
    if not hits:
        return "No past conversations on file match that."
    return f"{len(hits)} past conversation(s), most relevant first:\n\n" + "\n\n".join(
        _format_episode(ep) for ep, _sim in hits
    )


def _recall(args: dict, ctx: Context) -> str:
    """Unified recall: episodes (narrative, dated) + memories (atomic facts).

    Ordered by trust, and SAYS SO: a verbatim quote is what was actually said;
    a summary is JARVIS's interpretation; a learned fact is an inference that
    may simply be wrong. Tier 1 (configured ground truth) already rides in the
    preamble and outranks everything here — restated at the end so a
    contradicting recollection can't quietly win."""
    from app import vectorstore
    from app.episodic import search_episodes
    from app.memory import _owner_identity

    query = str(args.get("query", "")).strip()
    if not query:
        return "What should I try to remember?"

    parts: list[str] = []

    hits = search_episodes(ctx.db, query)
    quoted = [ep for ep, _s in hits if ep.quotes]
    unquoted = [ep for ep, _s in hits if not ep.quotes]
    if quoted:
        parts.append("## From past conversations (quote-anchored — verbatim):")
        parts.extend(_format_episode(ep) for ep in quoted)
    if unquoted:
        parts.append("## From past conversations (summaries — my interpretation):")
        parts.extend(_format_episode(ep, quotes=False) for ep in unquoted)

    try:
        facts = vectorstore.search(ctx.db, query)
    except Exception:
        facts = []
    if facts:
        parts.append("## Learned facts (inferred — may be wrong):")
        parts.extend(f"- {m.content}" for m, _sim in facts)

    if not parts:
        return "Nothing in memory matches that — no episodes, no learned facts."

    if _owner_identity():
        parts.append(
            "(These are recollections. Configured ground truth in the "
            "AUTHORITATIVE block outranks anything here if they conflict.)"
        )
    return "\n\n".join(parts)


def _forget_episode(args: dict, ctx: Context) -> str:
    from app.models import Episode

    eid = args.get("episode_id")
    if eid is None:
        return "Which episode? Give an episode id from recall_episodes."
    ep = ctx.db.get(Episode, int(eid))
    if ep is None:
        return f"No episode #{eid}."
    title = ep.title
    ctx.db.delete(ep)   # quotes cascade
    ctx.db.commit()
    return f"Forgotten: episode '{title}'."


def register(reg: Registry) -> None:
    reg.register(
        {
            "name": "recall_episodes",
            "description": (
                "Search PAST CONVERSATIONS by meaning, date, and topic — the "
                "dated record of what was discussed and decided, going back "
                "years. Use for 'remember when we talked about…', 'what did I "
                "decide about…', 'when did we last discuss…'. Returns dated "
                "summaries with verbatim quotes for decisions and commitments."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string",
                              "description": "What to look for, by meaning."},
                    "since": {"type": "string",
                              "description": "Only episodes on/after this date (YYYY-MM-DD)."},
                    "until": {"type": "string",
                              "description": "Only episodes on/before this date (YYYY-MM-DD)."},
                    "topic": {"type": "string",
                              "description": "Optional topic tag filter, e.g. 'travel'."},
                },
                "required": ["query"],
            },
        },
        _recall_episodes,
    )
    reg.register(
        {
            "name": "recall",
            "description": (
                "Unified memory search: PAST CONVERSATIONS (dated episodes with "
                "verbatim quotes) AND learned facts, merged and ranked by trust. "
                "Use when the user asks what you remember or know about "
                "something and you don't know which kind of memory holds it."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "What to remember."},
                },
                "required": ["query"],
            },
        },
        _recall,
    )
    reg.register(
        {
            "name": "forget_episode",
            "description": (
                "Delete a distilled past-conversation episode that is wrong, "
                "mis-scoped, or that the user asks you to forget. Get the id "
                "from recall_episodes. Corrections are not destructive acts."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "episode_id": {"type": "integer",
                                   "description": "From recall_episodes."},
                },
                "required": ["episode_id"],
            },
        },
        _forget_episode,
    )
