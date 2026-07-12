"""Memory audit — everything JARVIS believes about you, in one email.

WHY THIS EXISTS. She has been quietly inferring things about you from every
conversation. Most of it is right. Some of it is not — she once concluded, from a
conversation about driving to a boat, that Anacortes was the owner's home base,
and then confidently told him so.

That was caught by luck: he happened to ask a question whose answer he already
knew. A wrong belief nobody thinks to check is the dangerous kind, because it
gets trusted later and its origin is invisible.

So: the whole picture, on demand, in a form you can read at leisure and act on.

THE MOST IMPORTANT COLUMN IS `source`. It separates:
  * what YOU TOLD her (manual, or explicitly saved) — she should trust this
  * what she INFERRED from conversation (reflector) — this is where errors live

Anything from the reflector is a guess. Presenting it as if it carried the same
weight as something you stated would be dishonest, and would make the audit
useless — you'd have no idea which lines to scrutinize.
"""

from __future__ import annotations

import logging
from datetime import datetime

from sqlalchemy import select

from app.config import settings
from app.handlers.base import Context, Registry
from app.models import Contact, Memory, PersonaProfile, Preference

log = logging.getLogger(__name__)


def _fmt(dt: datetime | None) -> str:
    if dt is None:
        return "?"
    return dt.strftime("%Y-%m-%d")


def build_audit(db) -> str:
    """The whole picture, as readable text."""
    from app.memory import _owner_identity

    out: list[str] = [
        "WHAT JARVIS BELIEVES ABOUT YOU",
        f"Generated {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        "",
        "Anything marked INFERRED was guessed from conversation and may simply be",
        "wrong. Tell JARVIS to forget anything that isn't true.",
        "",
        "=" * 70,
        "",
    ]

    # ── Ground truth ────────────────────────────────────────────────────────
    identity = _owner_identity()
    out.append("## CONFIGURED (you set these directly — authoritative)")
    out.append("")
    if identity:
        out.extend(f"  {line}" for line in identity)
    else:
        out.append("  (nothing configured — set OWNER_NAME, OWNER_HOME_ADDRESS, etc.)")
    out.append("")

    # ── Persona ─────────────────────────────────────────────────────────────
    persona = db.execute(select(PersonaProfile).order_by(PersonaProfile.category)).scalars().all()
    if persona:
        out.append("## PERSONA (how she's told to think and speak as you)")
        out.append("")
        by_cat: dict[str, list[str]] = {}
        for p in persona:
            by_cat.setdefault(p.category, []).append(p.content)
        for cat, items in by_cat.items():
            out.append(f"  [{cat}]")
            out.extend(f"    - {i}" for i in items)
        out.append("")

    # ── Preferences ─────────────────────────────────────────────────────────
    prefs = db.execute(select(Preference).order_by(Preference.key)).scalars().all()
    if prefs:
        out.append("## STANDING PREFERENCES")
        out.append("")
        out.extend(f"  {p.key}: {p.value}" for p in prefs)
        out.append("")

    # ── Learned facts — THE POINT OF THE AUDIT ──────────────────────────────
    facts = db.execute(select(Memory).order_by(Memory.created_at.desc())).scalars().all()

    # Split by source. This is the whole reason the audit is worth reading: a
    # thing you SAID and a thing she GUESSED are not the same kind of claim, and
    # collapsing them would hide exactly the errors you're looking for.
    inferred = [m for m in facts if m.source in ("conversation", "reflector", "")]
    stated = [m for m in facts if m not in inferred]

    out.append(f"## LEARNED FACTS ({len(facts)} total)")
    out.append("")

    if stated:
        out.append(f"### You told her these ({len(stated)}) — she should trust them")
        out.append("")
        for m in stated:
            flag = " [SENSITIVE]" if m.sensitive else ""
            out.append(f"  #{m.id} [{m.category}] {_fmt(m.created_at)}{flag}")
            out.append(f"      {m.content}")
        out.append("")

    if inferred:
        out.append(f"### INFERRED from conversation ({len(inferred)}) — CHECK THESE")
        out.append("")
        out.append("  These are guesses. Read them properly — this is where a wrong")
        out.append("  belief hides, and a wrong belief gets trusted later.")
        out.append("")
        for m in inferred:
            flag = " [SENSITIVE]" if m.sensitive else ""
            out.append(f"  #{m.id} [{m.category}] {_fmt(m.created_at)}{flag}")
            out.append(f"      {m.content}")
        out.append("")

    if not facts:
        out.append("  (nothing learned yet)")
        out.append("")

    # ── Contacts ────────────────────────────────────────────────────────────
    contacts = db.execute(select(Contact).order_by(Contact.name)).scalars().all()
    out.append(f"## ADDRESS BOOK ({len(contacts)})")
    out.append("")
    if contacts:
        # Don't dump 466 rows into an email. Say how many, show a sample.
        noun = "contact" if len(contacts) == 1 else "contacts"
        out.append(f"  {len(contacts)} {noun} on file."
                   + (" First 20:" if len(contacts) > 20 else ""))
        out.append("")
        for c in contacts[:20]:
            bits = [c.name]
            if c.email:
                bits.append(c.email)
            if c.phone:
                bits.append(c.phone)
            out.append("  " + " | ".join(bits))
        if len(contacts) > 20:
            out.append(f"  ...and {len(contacts) - 20} more.")
    else:
        out.append("  (none)")
    out.append("")

    out += [
        "=" * 70,
        "",
        "TO CORRECT SOMETHING:",
        '  Call or text JARVIS: "Forget that I live in Anacortes"',
        '  Or by number:        "Forget fact 47"',
        "",
        "A wrong fact she can't be told about is worse than no memory at all.",
    ]

    return "\n".join(out)


def _audit_memory(args: dict, ctx: Context) -> str:
    """Email the owner everything JARVIS believes about them."""
    if not settings.owner_email_resolved:
        return "I don't have your email address. Set OWNER_EMAIL."

    text = build_audit(ctx.db)

    from app.jobs import enqueue

    enqueue(
        ctx.db, "email_copy",
        {"to": settings.owner_email_resolved,
         "subject": "JARVIS: what I believe about you",
         "body": text},
        channel=ctx.channel, thread_key=ctx.thread_key, actor=ctx.actor,
    )

    from app.models import Memory

    n = len(ctx.db.execute(select(Memory)).scalars().all())
    return (f"Emailed you the full audit — {n} learned facts, split into what you told me "
            f"and what I inferred. The inferred ones are the ones worth reading; that's "
            f"where I'd be wrong. Tell me to forget anything that isn't true.")


def register(reg: Registry) -> None:
    reg.register(
        {
            "name": "audit_memory",
            "description": (
                "Email the user EVERYTHING JARVIS believes about them: configured facts, "
                "persona, preferences, learned facts (split into what they told her versus "
                "what she inferred and might have got wrong), and the address book. Use when "
                "they ask what you know or remember about them, or want to check or audit "
                "your memory."
            ),
            "input_schema": {"type": "object", "properties": {}},
        },
        _audit_memory,
    )
