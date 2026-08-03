"""Watches — JARVIS acting while you're not thinking about her.

Until now she only ever moves when you call. A watch inverts that: she checks a
condition on a schedule and, when it fires, RINGS YOU.

    "Tell me if rpi-02 goes down."
    "Call me if that fare drops below two hundred."
    "Let me know when it's time to leave for the 9am."

Every piece of this already exists — the job worker ticks, tools return strings,
outbound calling works. A watch is just the wiring: run a tool on a schedule,
judge the result, and call if it matters.

HOW THE CONDITION IS JUDGED. The tool returns prose ("rpi-02 is offline"), not a
number. So the LLM reads the tool output and the user's condition and answers one
question: has it fired? That's a small, cheap call and it's far more robust than
trying to parse "under $200" out of free text with a regex.

WHY IT WON'T SPAM YOU. Three guards, and they matter more than the feature:
  * A watch that has fired is DONE. It does not fire again unless it's recurring.
  * `min_interval_minutes` floors how often a recurring watch can ring.
  * The outbound rate limit (6/hour) is a backstop underneath all of it.

A watch that calls you every five minutes is worse than no watch, because you'll
turn the whole thing off — and it will do that at 3am unless something stops it.
Quiet hours apply: an alert is not a callback the user asked for.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select

from app.config import settings
from app.handlers.base import Context, Registry
from app.models import Watch

log = logging.getLogger(__name__)

# Tools a watch may poll. Read-only, all of them — a watch runs unattended, and
# nothing unattended should be able to send mail, book a meeting, or spend money.
WATCHABLE = {
    "get_node_status",
    "get_service_health",
    "fleet_health",
    "fleet_spend",
    "tailscale_status",
    "get_traffic",
    "search_flights",
    "get_stock_price",
    "calendar_lookup",
}


def _tz() -> ZoneInfo:
    try:
        return ZoneInfo(settings.calendar_timezone)
    except Exception:
        return ZoneInfo("UTC")


def _create_watch(args: dict, ctx: Context) -> str:
    tool = (args.get("tool") or "").strip()
    condition = (args.get("condition") or "").strip()
    opening = (args.get("opening") or "").strip()

    if tool not in WATCHABLE:
        return (f"I can't watch '{tool}'. I can watch: {', '.join(sorted(WATCHABLE))}. "
                f"Watches only ever READ — nothing that sends, books, or spends.")
    if not condition:
        return "What should I be watching for?"
    if not opening:
        return ("What should I SAY when I call you about it? They won't know why you're "
                "ringing.")

    every = max(int(args.get("every_minutes") or 15), 5)

    w = Watch(
        tool=tool,
        tool_args=json.dumps(args.get("tool_args") or {}),
        condition=condition,
        opening=opening,
        every_minutes=every,
        recurring=bool(args.get("recurring")),
        status="active",
        created_by=ctx.channel,
    )
    ctx.db.add(w)
    ctx.db.commit()
    ctx.db.refresh(w)

    how = "every time it happens" if w.recurring else "once, then I'll stop"
    return (f"Watch #{w.id} set: I'll check {tool} every {every} minutes and call you "
            f"when {condition} — {how}.")


def _list_watches(args: dict, ctx: Context) -> str:
    rows = (
        ctx.db.execute(select(Watch).where(Watch.status == "active").order_by(Watch.id))
        .scalars()
        .all()
    )
    if not rows:
        return "Nothing being watched."
    return f"{len(rows)} active:\n" + "\n".join(
        f"#{w.id}: {w.condition} (checks {w.tool} every {w.every_minutes} min)"
        for w in rows
    )


def _cancel_watch(args: dict, ctx: Context) -> str:
    wid = args.get("watch_id")
    if wid is None:
        return "Which watch? Give the number."
    w = ctx.db.get(Watch, int(wid))
    if w is None:
        return f"No watch #{wid}."
    w.status = "cancelled"
    ctx.db.commit()
    return f"Stopped watching: {w.condition}"


# ── The engine (runs on the worker) ─────────────────────────────────────────
def due_watches(db, now: datetime | None = None) -> list[Watch]:
    now = now or datetime.now(_tz())
    rows = (
        db.execute(select(Watch).where(Watch.status == "active")).scalars().all()
    )
    out = []
    for w in rows:
        last = w.last_checked_at
        if last is not None:
            if last.tzinfo is None:          # SQLite hands back naive
                last = last.replace(tzinfo=_tz())
            if now - last < timedelta(minutes=w.every_minutes):
                continue
        out.append(w)
    return out


def check_watch(db, w: Watch) -> str:
    """Run one watch. If it fires, queue a call."""
    from app.channels.outbound_voice import schedule_call
    from app.handlers.base import Context, build_registry

    w.last_checked_at = datetime.now(_tz())
    db.commit()

    reg = build_registry()
    if not reg.has(w.tool):
        w.status = "error"
        w.error = f"tool {w.tool} no longer exists"
        db.commit()
        return w.error

    ctx = Context(db=db, channel="watch", actor="system", thread_key=f"watch:{w.id}")
    try:
        observation = reg.execute(w.tool, json.loads(w.tool_args or "{}"), ctx)
    except Exception as e:  # noqa: BLE001
        log.warning("watch #%s tool failed: %s", w.id, e)
        return f"tool error: {e}"

    if not _fired(w.condition, str(observation)):
        return "not fired"

    # Don't ring more often than the floor, however often the condition is true.
    last = w.last_fired_at
    if last is not None:
        if last.tzinfo is None:
            last = last.replace(tzinfo=_tz())
        if datetime.now(_tz()) - last < timedelta(minutes=settings.watch_min_interval_minutes):
            return "fired but rate-limited"

    row = schedule_call(db, opening=w.opening, kind="alert",
                        context=f"Watch: {w.condition}. Observed: {observation}")
    if row is None:
        # The condition fired but the call couldn't be placed (owner phone unset
        # or not allowlisted). Do NOT consume the watch — leave it active so it
        # retries on its next scheduled check instead of firing once into the void
        # (audit M4). last_checked_at is already set, so this won't hot-loop.
        log.error("watch #%s FIRED but could not place the alert call "
                  "(owner phone unset / not allowlisted) — leaving active to retry", w.id)
        return "fired but could not call"

    w.last_fired_at = datetime.now(_tz())
    w.fire_count += 1
    if not w.recurring:
        w.status = "done"       # one-shot: fired, finished. Don't nag.
    db.commit()

    log.info("watch #%s FIRED: %s", w.id, w.condition)
    return f"fired — queued call #{row.id}"


SYSTEM_WATCH_TOOL = "check_location_freshness"


def seed_system_watches(db) -> int:
    """Seed the absence watch — the dead-man's-switch for the location loop.

    IDEMPOTENT ON `(created_by, tool)`, deliberately not on the condition text.
    The condition is prose and will be reworded; keying on it would look correct
    right up until an edit produced a second watch, and two watches on one
    condition is a double-ring the owner disables.

    Returns the number of rows created (0 on every run after the first).
    """
    existing = (
        db.query(Watch)
        .filter(Watch.created_by == "system", Watch.tool == SYSTEM_WATCH_TOOL)
        .first()
    )
    if existing is not None:
        return 0

    db.add(Watch(
        tool=SYSTEM_WATCH_TOOL,
        tool_args="{}",
        condition=("no position fix has registered in over 30 minutes during active "
                   "hours"),
        opening=("This is JARVIS. Your phone has stopped reporting its position — "
                 "I have not had a fix in over half an hour."),
        every_minutes=15,          # two checks inside the 30-minute window
        # NOT recurring. A watch that nags is one you disable, and a disabled
        # location alarm is how you get six hours stale again. `rearm_system_watches`
        # is what makes it repeatable — see there for why that lives in the engine.
        recurring=False,
        created_by="system",
    ))
    db.commit()
    log.info("seeded system watch: %s", SYSTEM_WATCH_TOOL)
    return 1


def rearm_system_watches(db) -> int:
    """Re-arm a fired system watch once its condition has CLEARED.

    THE PROBLEM `recurring` CANNOT SOLVE. True rings every interval while the
    condition holds — the nagging the design rejects. False sets `done` on the
    first fire, forever — so the NEXT outage never alerts, and a dead-man's-switch
    that dies after one use is worse than none, because you believe you have one.
    What is wanted is fire once per OUTAGE, and neither setting expresses that.

    So the re-arm lives here, in the engine, keyed on `(created_by='system', tool)`.
    `recurring` stays False and one-shot semantics are untouched for every user
    watch.

    REJECTED, and recorded so it isn't reintroduced as a tidy-up: re-arming from
    inside `check_location_freshness`. A health check that mutates a watch row
    couples the status surface to the alerting surface, and the next person adding
    a check would reasonably copy the pattern. **Checks read; the engine writes.**

    THE CLEAR CONDITION IS READ STRUCTURALLY, never through `_fired`. The judge
    fails closed by design, so routing recovery through it means one hiccup leaves
    the watch permanently `done` — silent, and indistinguishable from "no outage
    since". Recovery is a structural fact (`status == "ok"`); it is read as one.

    `fire_count` and `last_fired_at` are left intact: they are the history, and
    the only record the alarm ever went off.
    """
    from app.health_checks import check_location_freshness
    from app.models import Component

    done = (
        db.query(Watch)
        .filter(Watch.created_by == "system", Watch.status == "done")
        .all()
    )
    if not done:
        return 0

    rearmed = 0
    for w in done:
        if w.tool != SYSTEM_WATCH_TOOL:
            continue
        c = db.query(Component).filter_by(name="location_freshness").first()
        if c is None:
            continue
        try:
            result = check_location_freshness(db, c)
        except Exception as e:  # noqa: BLE001 — never break the worker on a check
            log.warning("re-arm check failed for watch #%s: %s", w.id, e)
            continue
        if result.status == "ok":
            w.status = "active"
            rearmed += 1
            log.info("re-armed system watch #%s (location_freshness cleared)", w.id)
    if rearmed:
        db.commit()
    return rearmed


def _fired(condition: str, observation: str) -> bool:
    """Has the condition been met?

    The LLM reads the tool's prose output and the user's condition and answers
    yes or no. Trying to regex "below $200" out of free text would be brittle and
    would fail in exactly the ways that matter.

    Fails CLOSED: any error means "not fired". A watch that rings you because the
    judge broke is far worse than one that stays quiet.
    """
    from app.llm import create_message

    try:
        # Deliberate: Haiku, not the full conversational model. The judge is a
        # narrow binary classifier (does this prose observation satisfy this
        # condition string?) — same pattern as the reflector. No tool calls,
        # no multi-step reasoning; Haiku handles this class reliably and cheaply.
        from app.config import settings
        resp = create_message(
            system=(
                "You decide whether a monitoring condition has been met. Answer with "
                "exactly one word: YES or NO. Nothing else.\n"
                "YES only if the observation clearly satisfies the condition. If it is "
                "ambiguous, or the observation doesn't address the condition, answer NO — "
                "a false alarm is worse than a missed one, because the user will switch "
                "the whole thing off."
            ),
            messages=[{"role": "user", "content":
                       f"Condition: {condition}\n\nObservation: {observation}\n\nMet?"}],
            tools=[],
            model=settings.jarvis_router_model,
        )
        text = "".join(b.text for b in resp.content if b.type == "text").strip().upper()
        return text.startswith("YES")
    except Exception as e:  # noqa: BLE001
        log.warning("watch judge failed (treating as not fired): %s", e)
        return False


def register(reg: Registry) -> None:
    reg.register(
        {
            "name": "watch_for",
            "description": (
                "Watch for something and CALL THE USER when it happens. Use when they ask "
                "you to keep an eye on something, tell them if X changes, or let them know "
                "when Y — anything that means 'don't make me keep asking'."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "tool": {"type": "string", "enum": sorted(WATCHABLE),
                             "description": "Which read-only tool to poll."},
                    "tool_args": {"type": "object",
                                  "description": "Args for that tool, e.g. "
                                                 "{'destination': 'work'} for get_traffic."},
                    "condition": {"type": "string",
                                  "description": "Plain English — 'rpi-02 is down', 'the "
                                                 "fare is under $200'. You'll be shown the "
                                                 "tool's output and asked if this is true."},
                    "opening": {"type": "string",
                                "description": "EXACTLY what you'll say when you ring. They "
                                               "won't know why you're calling: 'It's JARVIS "
                                               "— rpi-02 just dropped off the network.'"},
                    "every_minutes": {"type": "integer", "description": "Default 15, min 5."},
                    "recurring": {"type": "boolean",
                                  "description": "False (default) = tell them once, then "
                                                 "stop. True = every time it happens."},
                },
                "required": ["tool", "condition", "opening"],
            },
        },
        _create_watch,
    )
    reg.register(
        {
            "name": "list_watches",
            "description": "What JARVIS is currently watching for.",
            "input_schema": {"type": "object", "properties": {}},
        },
        _list_watches,
    )
    reg.register(
        {
            "name": "cancel_watch",
            "description": "Stop watching for something.",
            "input_schema": {
                "type": "object",
                "properties": {"watch_id": {"type": "integer"}},
                "required": ["watch_id"],
            },
        },
        _cancel_watch,
    )
