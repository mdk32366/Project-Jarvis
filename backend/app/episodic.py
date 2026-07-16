"""Episodic memory — distill closed conversations into dated, quotable records.

Tier 3 of the memory model (TDD #14). At the close of a conversation the
distiller reads the raw turns from that channel's cold store (`voice_turns`
for calls, `messages` for text — both left PRISTINE, see §3's design fork),
asks the LLM for a structured summary, validates every claimed quote against
the raw turns, embeds the result, and persists one Episode.

Two invariants live here and are enforced in code, not convention:

  * A quote is stored ONLY if it is a verbatim, speaker-matched substring of a
    raw turn. A fabricated quote is the one unacceptable failure — it launders
    a hallucination into "your exact words" — so non-matching quotes are
    DROPPED and logged loudly, never stored.
  * Distillation never writes to the raw stores. Its input is `voice_turns` or
    `messages`; its output is `episodes`. Nothing is copied sideways — that is
    what keeps quote validation trustworthy (test #16 is the tripwire).

Distillation is a JOB (kind "distill_episode"), never inline: it makes an LLM
call, which must not block a hangup or a reply.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import List, Optional, Tuple
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.embeddings import cosine, embed
from app.llm import create_message
from app.models import Conversation, Episode, EpisodeQuote, Job, Message, VoiceTurn

log = logging.getLogger(__name__)


# ── Trigger ──────────────────────────────────────────────────────────────────
def close_episode(db: Session, channel: str, thread_key: str,
                  source_ref: str = "") -> Optional[Job]:
    """Mark a conversation closed: enqueue distillation, out-of-band.

    The one channel-agnostic boundary every channel calls at its natural end —
    voice on call completion today; text channels on thread lull later, with
    NO schema change. Deduped against the job queue (same pattern as
    email_transcript): a duplicate status callback must not distill twice.
    """
    if not settings.episodes_enabled:
        return None
    source_ref = source_ref or thread_key

    dupe = (
        db.execute(
            select(Job)
            .where(Job.kind == "distill_episode")
            .where(Job.thread_key == thread_key)
        )
        .scalars()
        .first()
    )
    if dupe is not None:
        return None

    from app.jobs import enqueue

    return enqueue(
        db, "distill_episode",
        {"channel": channel, "thread_key": thread_key, "source_ref": source_ref},
        channel=channel, thread_key=thread_key, actor="system",
    )


# ── Raw-turn loading (READ-ONLY — the cold stores are never written here) ────
@dataclass
class _RawTurn:
    speaker: str    # owner|jarvis
    text: str
    ref: str        # provenance: voice_turn:<id>:user / message:<id>
    at: Optional[datetime]


def _load_turns(db: Session, channel: str, thread_key: str,
                source_ref: str) -> List[_RawTurn]:
    if channel == "voice":
        rows = (
            db.execute(
                select(VoiceTurn)
                .where(VoiceTurn.call_sid == (source_ref or thread_key))
                .order_by(VoiceTurn.turn)
            )
            .scalars()
            .all()
        )
        out: List[_RawTurn] = []
        for r in rows:
            if r.user_text:
                out.append(_RawTurn("owner", r.user_text, f"voice_turn:{r.id}:user", r.created_at))
            if r.reply:
                out.append(_RawTurn("jarvis", r.reply, f"voice_turn:{r.id}:reply", r.created_at))
        return out

    # Text channels: the conversation/messages cold store.
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
        return []
    rows = (
        db.execute(
            select(Message)
            .where(Message.conversation_id == convo.id)
            .order_by(Message.created_at)
        )
        .scalars()
        .all()
    )
    speaker = {"user": "owner", "assistant": "jarvis"}
    return [
        _RawTurn(speaker[m.role], m.content, f"message:{m.id}", m.created_at)
        for m in rows
        if m.role in speaker and m.content
    ]


# ── LLM distillation ─────────────────────────────────────────────────────────
_DISTILL_SYSTEM = (
    "You distill one finished conversation into a durable episodic memory. "
    "Return ONLY a JSON object:\n"
    '{"title": "<one-line handle>", "summary": "<2-4 sentence narrative of what '
    'was discussed and decided>", "topics": ["tag", ...], '
    '"action_items": ["<thing to do>", ...], "salience": <0.0-1.0 how much this '
    'mattered>, "quotes": [{"speaker": "owner"|"jarvis", '
    '"kind": "decision"|"commitment"|"key_fact"|"preference", '
    '"quote": "<VERBATIM copy of part of one turn>"}]}\n\n'
    "QUOTES MUST BE VERBATIM. Copy the exact characters of the turn text — no "
    "paraphrase, no cleanup, no added punctuation. Every quote is checked "
    "against the transcript and a quote that does not match EXACTLY is thrown "
    "away. Quote only the load-bearing moments: decisions made, commitments "
    "given, key facts stated, preferences expressed.\n"
    "The summary is your interpretation; keep it faithful and plain. Routine "
    "pleasantries are not action items. A short logistical call is low "
    "salience; a decision about money, projects, or people is high."
)


def _parse_distillation(raw: str) -> Optional[dict]:
    """Extract the JSON object from the LLM reply (same tolerance as reflector)."""
    raw = raw.strip()
    start, end = raw.find("{"), raw.rfind("}")
    if start == -1 or end == -1:
        return None
    try:
        data = json.loads(raw[start:end + 1])
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def _validate_quotes(candidates: list, turns: List[_RawTurn]) -> List[EpisodeQuote]:
    """Keep only quotes that are verbatim, speaker-matched substrings of a turn.

    THE FAITHFULNESS GATE (TDD §4 step 5). A dropped quote costs a citation;
    a stored fabrication poisons the archive as 'your exact words.' Drop, and
    say so loudly.
    """
    kept: List[EpisodeQuote] = []
    for c in candidates if isinstance(candidates, list) else []:
        if not isinstance(c, dict):
            continue
        speaker = str(c.get("speaker", "")).strip().lower()
        quote = str(c.get("quote", ""))
        kind = str(c.get("kind", "key_fact")).strip().lower()
        if speaker not in ("owner", "jarvis") or not quote.strip():
            log.warning("episodic: malformed quote dropped: %r", c)
            continue
        if kind not in ("decision", "commitment", "key_fact", "preference"):
            kind = "key_fact"
        match = next((t for t in turns if t.speaker == speaker and quote in t.text), None)
        if match is None:
            log.warning(
                "episodic: NON-VERBATIM quote dropped (would have laundered a "
                "hallucination into 'your exact words'): %s: %r", speaker, quote[:200],
            )
            continue
        kept.append(EpisodeQuote(speaker=speaker, quote=quote, kind=kind,
                                 turn_ref=match.ref[:64]))
    return kept


def _occurred(turns: List[_RawTurn]) -> Tuple[date, datetime]:
    """When the conversation happened — owner-local date, precise UTC start."""
    first = next((t.at for t in turns if t.at is not None), None)
    if first is None:
        first = datetime.now(timezone.utc)
    if first.tzinfo is None:            # SQLite hands back naive datetimes
        first = first.replace(tzinfo=timezone.utc)
    try:
        local = first.astimezone(ZoneInfo(settings.calendar_timezone))
    except Exception:
        local = first
    return local.date(), first


# ── The job body ─────────────────────────────────────────────────────────────
def distill_episode(db: Session, channel: str, thread_key: str,
                    source_ref: str = "") -> str:
    """Distill one closed conversation into an Episode. Returns a result line."""
    source_ref = source_ref or thread_key

    existing = (
        db.execute(
            select(Episode)
            .where(Episode.channel == channel)
            .where(Episode.source_ref == source_ref)
        )
        .scalars()
        .first()
    )
    if existing is not None:
        return f"already distilled as episode #{existing.id}"

    turns = _load_turns(db, channel, thread_key, source_ref)
    owner_turns = sum(1 for t in turns if t.speaker == "owner")
    if owner_turns < settings.episode_min_turns:
        return f"skipped: only {owner_turns} owner turn(s) — not an episode"

    transcript = "\n".join(f"{t.speaker}: {t.text}" for t in turns)
    resp = create_message(
        system=_DISTILL_SYSTEM,
        messages=[{"role": "user", "content": transcript}],
        model=settings.jarvis_router_model,
    )
    raw = "\n".join(b.text for b in resp.content if getattr(b, "type", None) == "text")
    data = _parse_distillation(raw)
    if data is None:
        raise ValueError(f"distiller returned unparseable output: {raw[:200]!r}")

    title = str(data.get("title", "")).strip()[:512]
    summary = str(data.get("summary", "")).strip()
    topics = [str(t) for t in data.get("topics", []) if str(t).strip()]
    action_items = [str(a) for a in data.get("action_items", []) if str(a).strip()]
    try:
        salience = min(1.0, max(0.0, float(data.get("salience", 0.5))))
    except (TypeError, ValueError):
        salience = 0.5
    if not (title and summary):
        raise ValueError("distiller output missing title/summary")

    quotes = _validate_quotes(data.get("quotes", []), turns)
    occurred_on, occurred_at = _occurred(turns)

    ep = Episode(
        channel=channel,
        thread_key=thread_key,
        occurred_on=occurred_on,
        occurred_at=occurred_at,
        title=title,
        summary=summary,
        topics=json.dumps(topics),
        action_items=json.dumps(action_items),
        salience=salience,
        embedding=json.dumps(embed(f"{title}\n{summary}\n{' '.join(topics)}")),
        source_ref=source_ref[:128],
    )
    ep.quotes.extend(quotes)
    db.add(ep)
    db.commit()
    db.refresh(ep)
    log.info("episodic: distilled %s/%s -> episode #%s (%d quote(s))",
             channel, thread_key, ep.id, len(quotes))
    return f"episode #{ep.id}: {title} ({len(quotes)} quote(s))"


# ── Search (same portable pattern as vectorstore: pgvector later, cosine now) ─
def search_episodes(db: Session, query: str, k: Optional[int] = None,
                    since: Optional[date] = None, until: Optional[date] = None,
                    topic: str = "") -> List[Tuple[Episode, float]]:
    """Hybrid recall: embedding similarity × salience, filtered by date/topic.

    Personal-scale corpus (a few conversations a day for a decade is ~10k
    rows), so the portable in-Python cosine path is the implementation; the
    SQL filters keep the candidate set small first. A pgvector mirror can slot
    in behind this signature later without touching any caller.
    """
    k = k or settings.episode_recall_k
    q = select(Episode)
    if since is not None:
        q = q.where(Episode.occurred_on >= since)
    if until is not None:
        q = q.where(Episode.occurred_on <= until)
    rows = db.execute(q).scalars().all()
    if topic:
        t = topic.strip().lower()
        rows = [e for e in rows if t in (e.topics or "").lower()]

    qvec = embed(query)
    scored: List[Tuple[Episode, float]] = []
    for e in rows:
        if not e.embedding:
            continue
        try:
            evec = json.loads(e.embedding)
        except Exception:
            continue
        scored.append((e, cosine(qvec, evec) * (0.5 + 0.5 * (e.salience or 0.5))))
    scored.sort(key=lambda t: t[1], reverse=True)
    return scored[:k]
