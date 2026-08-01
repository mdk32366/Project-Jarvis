"""The completeness gate — TDD #2 §5. **This is the invention.**

Everything else in planning sessions is plumbing. The gate is the part that
makes a planning session different from a better prompt, and it does it by
REFUSING.

WHAT IT IS FOR. On 2026-07-20 JARVIS was asked for a TDD and produced one with
every section present and every section a placeholder. The shape of a design
document is trivially generatable; its content is the residue of an argument
that has to have actually happened. The gate's whole job is to make the absence
of that argument mechanically detectable, so the correct behaviour — *ask the
next question* — happens instead of an emission nobody can use.

THE TWO UNFAKEABLE SLOTS (§5.2). Every slot here can be bluffed from a topic
name alone except two:

  - `rejected` — an alternative AND WHY IT LOST. You cannot produce that without
    having considered a path and having a reason it failed, which means either
    the owner supplied it or a real argument happened. An alternative with no
    reason is a list, not a rejection.
  - `open_questions` — a design with no uncertainty in it is a design nobody
    thought hard about. **An empty `open_questions` is evidence of insufficient
    thought, not of thoroughness**, and the refusal says so rather than just
    reporting a missing field.

WHAT THIS GATE CANNOT DO, stated so nobody mistakes it for a guarantee (§5.4):
it catches EMPTY, not SHALLOW. A 200-character `problem` slot that says nothing
passes. It raises the floor from "placeholder" to "someone typed something
real" — which is the entire distance between the 07-20 artifact and a usable
document, and no further. Depth is the Planner session's job, and trying to
enforce depth mechanically would either block real work or produce a bar so low
it is theatre.

BUILT BEFORE EMISSION, DELIBERATELY (§9). A system that emits, with a gate
bolted on afterwards, is how gates end up bypassable. The gate shipped alone in
#58 and was proven refusing before anything could write; emission (#59) now
CALLS it rather than carrying its own copy of the rules.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.config import settings

# ── The slots (§5.1) ─────────────────────────────────────────────────────────
# `data_model` is required UNLESS explicitly marked not-applicable, and that
# marking is itself recorded as a note — "no schema change" is a real design
# statement, and letting it be silently absent would make "we didn't think about
# it" indistinguishable from "we decided it needs nothing".
REQUIRED_SLOTS = ("problem", "goals", "non_goals", "approach", "rejected",
                  "tests", "open_questions")
CONDITIONAL_SLOTS = ("data_model",)
ALL_SLOTS = REQUIRED_SLOTS + CONDITIONAL_SLOTS

# The question that would fill each gap. The gate returns these WITH the missing
# slots (§5.3) — a refusal that names what is missing and how to fix it is
# actionable; one that just says "incomplete" is a dead end, and a dead end is
# how a tool stops being used.
SLOT_QUESTIONS: dict[str, str] = {
    "problem": "What's actually broken or missing, and how does it show up in practice?",
    "goals": "What does done look like? What must be true when this works?",
    "non_goals": "What are you explicitly NOT doing here — what's out of scope?",
    "approach": "How does it work? What's the design you've settled on?",
    "rejected": "What else did you consider, and why did it lose? Name one alternative "
                "and the reason you're not doing it.",
    "data_model": "What state or schema changes does this need — or is the answer "
                  "explicitly none?",
    "tests": "What would prove this works? What would you check?",
    "open_questions": "What's still unresolved? What are you unsure about?",
}

# Why the refusal, for the two slots where "you left a field blank" is the wrong
# framing — the emptiness is itself the finding.
SLOT_REFUSAL_WHY: dict[str, str] = {
    "rejected": ("If nothing was considered and discarded, this wasn't planning — it was "
                 "transcribing. An alternative without a reason it lost is a list, not a "
                 "rejection."),
    "open_questions": ("An empty open-questions is evidence of insufficient thought, not of "
                       "thoroughness. A design with no uncertainty in it is one nobody "
                       "pushed on."),
}

# ── Placeholder detection (§5.3) ─────────────────────────────────────────────
# Matched against the ACTUAL 07-20 failure: sections reading "TBD",
# "To be determined", "[details to follow]".
_PLACEHOLDER_RE = re.compile(
    r"""(?ix)
    \b(?:TBD|TODO|N/?A|none\s+yet|to\s+be\s+(?:determined|decided|defined)|
        details?\s+to\s+follow|coming\s+soon|fill\s+(?:this\s+)?in|placeholder)\b
    | \[[^\]]{0,80}\]          # [details], [...]
    | <[^>]{0,80}>             # <fill in>
    """,
)

# A reason marker for `rejected`. Deliberately a HEURISTIC and deliberately
# generous: the goal is to catch "considered Redis" with nothing after it, not
# to grade the argument. Over-strictness here would block real work, which §5.4
# says is the worse failure of the two.
# The ONLY tokens that mean "we considered this and it needs nothing" for
# `data_model`. Narrow on purpose: "TBD" is not a decision, it is the absence of
# one, and an early version of this gate accepted any placeholder here — which
# let the 07-20 artifact through on one slot. "No schema change" is a design
# statement; "to be determined" is the thing the gate exists to refuse.
_NOT_APPLICABLE_RE = re.compile(
    r"""(?ix)
    ^\W*(?:N/?A|none|not\s+applicable|no\s+(?:schema|state|data[\s_-]?model|table)
        (?:\s+change(?:s)?)?)\W*$
    """,
)

_REASON_RE = re.compile(
    r"""(?ix)
    \b(?:because|since|as\s+it|but\b|however|rather\s+than|instead\s+of|
        too\s+\w+|would\s+(?:have|need|mean|require)|means?\b|costs?\b|
        rejected\s+(?:it|for)|lost\b|downside|drawback|doesn'?t|does\s+not|
        can'?t|cannot|won'?t|will\s+not|no\s+\w+\s+support|overkill|
        not\s+worth|trade[- ]?off)\b
    """,
)


def _substantive(text: str) -> str:
    """The content with placeholder tokens stripped out.

    This is what unifies the length check and the placeholder check into one
    honest question: *how much real text is left once the filler is removed?*
    "TBD" collapses to nothing and fails on length. A genuine 300-word slot that
    happens to contain the word "TODO" loses four characters and passes — which
    is right, because a real paragraph mentioning a TODO is not a placeholder,
    and a rule that punished it would train people to avoid the word rather than
    to write more.
    """
    return re.sub(r"\s+", " ", _PLACEHOLDER_RE.sub(" ", text or "")).strip()


@dataclass
class SlotVerdict:
    """Why one slot passed or failed. Carries the QUESTION, not just the fault —
    the refusal has to tell you what to do next."""

    slot: str
    filled: bool
    reason: str = ""
    question: str = ""


@dataclass
class Readiness:
    """The gate's answer. `ready` is the bool; everything else is why."""

    ready: bool
    missing: list[SlotVerdict] = field(default_factory=list)
    filled: list[str] = field(default_factory=list)
    unclassified: int = 0

    def summary(self) -> str:
        """Human-readable, and shaped as the next thing to DO rather than a
        list of failures. Refusal is the feature; a refusal nobody can act on
        is just an obstacle."""
        if self.ready:
            return (f"Ready — all {len(self.filled)} required slots are substantively "
                    f"filled." + (f" ({self.unclassified} note(s) still unclassified.)"
                                  if self.unclassified else ""))
        lines = [f"Not ready — {len(self.missing)} slot(s) still need real content:"]
        for v in self.missing:
            why = f" {SLOT_REFUSAL_WHY[v.slot]}" if v.slot in SLOT_REFUSAL_WHY else ""
            lines.append(f"  • {v.slot} — {v.reason}.{why}")
            lines.append(f"    → {v.question}")
        if self.unclassified:
            lines.append(f"  ({self.unclassified} note(s) unclassified — they count toward "
                         f"nothing until they're filed.)")
        return "\n".join(lines)


# ── Emission (§7.1) ──────────────────────────────────────────────────────────
# VERBATIM AND FIRST, every time. This is the KEEL boundary made visible in the
# artifact itself: a document that LOOKS build-ready and is not is worse than no
# document, because the reader spends their trust before discovering the gap.
# The banner is not a courtesy line — it is the thing that keeps a JARVIS-drafted
# design from being mistaken for a Builder-ready spec, which is the exact seam
# the Planner/Builder split exists to hold.
BANNER = (
    "> Drafted in a JARVIS planning session on {date}. Planner-ready, NOT\n"
    "> build-ready — bring to a design session before implementation."
)

# The emitted document's section order. Mirrors the slot set so a reader who
# knows the gate knows the shape.
_SECTIONS = (
    ("problem", "1. Problem"),
    ("goals", "2. Goals"),
    ("non_goals", "3. Non-goals"),
    ("approach", "4. Approach"),
    ("rejected", "5. Alternatives considered and rejected"),
    ("data_model", "6. Data model"),
    ("tests", "7. Test plan"),
    ("open_questions", "8. Open questions"),
)


def compose_document(session, notes, *, date: str) -> str:
    """Compose the emitted markdown: banner, sections, provenance.

    PROVENANCE PAIRS WITH THE GATE, and the pairing is the point (§7.1.3). The
    gate refuses thin content; provenance makes thin content VISIBLE even when it
    passes — session id, dates, channels, note count. A document assembled from
    four notes in one sitting and one assembled from thirty across a week and
    three channels read identically without it, and the first is exactly what the
    07-20 artifact was. Together they are two layers against one failure: a
    placeholder document passed off as thought-through.
    """
    slots = compose_slots(notes)
    channels = sorted({n.channel for n in notes if n.channel})

    parts = [
        BANNER.format(date=date),
        "",
        f"# TDD — {session.topic}",
        "",
    ]
    for slot, heading in _SECTIONS:
        body = slots.get(slot, "").strip()
        parts += [f"## {heading}", "", body or "_(not recorded)_", ""]

    parts += [
        "---",
        "",
        "## Provenance",
        "",
        f"- Planning session: #{session.id}",
        f"- Opened: {session.created_at}",
        f"- Emitted: {date}",
        f"- Channels used: {', '.join(channels) or 'none recorded'}",
        f"- Notes captured: {len(notes)}",
        "",
        "_This document is the residue of a conversation, not a single-turn "
        "generation. The note count and channel spread above are the evidence._",
        "",
    ]
    return "\n".join(parts)


def compose_slots(notes) -> dict[str, str]:
    """Compose slot text from notes. The notes are the record; this is a view.

    Joined in capture order and never rewritten — see `PlanningNote`.
    """
    out: dict[str, str] = {}
    for n in sorted(notes, key=lambda x: (x.id or 0)):
        if not n.slot:
            continue
        out[n.slot] = f"{out.get(n.slot, '')}\n\n{n.content}".strip()
    return out


def _judge_slot(slot: str, text: str, min_chars: int) -> SlotVerdict:
    q = SLOT_QUESTIONS.get(slot, "")
    body = _substantive(text)

    if not body:
        return SlotVerdict(slot, False, "nothing here but placeholders" if text
                           else "empty", q)
    if len(body) < min_chars:
        return SlotVerdict(
            slot, False,
            f"only {len(body)} characters of real content (needs {min_chars})", q)

    # `rejected` carries the extra bar: an alternative AND a reason it lost.
    if slot == "rejected" and not _REASON_RE.search(body):
        return SlotVerdict(slot, False,
                           "an alternative is named but no reason it lost", q)

    return SlotVerdict(slot, True, "", q)


def session_readiness(notes, *, min_chars: int | None = None) -> Readiness:
    """Judge whether a session has enough substance to be written down (§5.3).

    Returns WHAT IS MISSING AND THE QUESTION THAT FILLS IT, not a bare bool —
    the refusal is meant to move the session forward.

    Emission CALLS this; it does not re-implement it. A second readiness rule is
    a second thing that can disagree with the first, and the one that gets fixed
    is whichever is failing loudly at the time.
    """
    min_chars = settings.planning_min_slot_chars if min_chars is None else min_chars
    slots = compose_slots(notes)
    unclassified = sum(1 for n in notes if not n.slot)

    verdicts = [_judge_slot(s, slots.get(s, ""), min_chars) for s in REQUIRED_SLOTS]

    # `data_model` is satisfied either by real content OR by an explicit
    # not-applicable — recorded, not assumed. "No schema change" is a design
    # statement; silence is not.
    dm = slots.get("data_model", "")
    if dm.strip():
        if _NOT_APPLICABLE_RE.match(dm.strip()):
            # The recorded not-applicable — the one slot where a short answer is
            # a legitimate one, because "this needs no schema change" IS the
            # design statement. Note it must be the WHOLE content: an N/A buried
            # in a paragraph is not a decision, and "TBD" is never one.
            verdicts.append(SlotVerdict("data_model", True, "explicitly not applicable"))
        else:
            verdicts.append(_judge_slot("data_model", dm, min_chars))
    else:
        verdicts.append(SlotVerdict("data_model", False, "empty",
                                    SLOT_QUESTIONS["data_model"]))

    missing = [v for v in verdicts if not v.filled]
    return Readiness(
        ready=not missing,
        missing=missing,
        filled=[v.slot for v in verdicts if v.filled],
        unclassified=unclassified,
    )
