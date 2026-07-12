"""Tasks handler — JARVIS owns its own task list.

JARVIS OWNS THE TASKS. The DB is the source of truth, not Google.

Originally this was forced: the calendar uses a *service account*, and a service
account cannot reach a consumer Google account's task list at all (no domain-wide
delegation for @gmail.com — it would get its own invisible list).

OAuth now makes Google Tasks reachable (see app/google_oauth.py), so tasks can be
PUSHED to Google — which is what puts them on the user's phone. But the local
table stays authoritative:

  * JARVIS creates tasks mid-phone-call. A network round-trip to Google inside a
    voice turn is latency we cannot afford.
  * The morning briefing and dashboard read the DB directly.
  * If Google is unreachable, task creation must still work.

So: write locally, push to Google out-of-band via a job. One-way, deliberately —
two-way sync means conflict resolution, and that is a lot of complexity for a
personal task list.

`add_task` and `list_tasks` are safe. `complete_task` is a small write but not
destructive (it sets status, keeps the row), so it is ungated — a mistaken
completion is one sentence to undo. `cancel_task` likewise.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select

from app.config import settings
from app.handlers.base import Context, Registry
from app.models import Task

log = logging.getLogger(__name__)


def _tz() -> ZoneInfo:
    try:
        return ZoneInfo(settings.calendar_timezone)
    except Exception:
        return ZoneInfo("UTC")


def _parse_due(raw: str) -> datetime | None:
    """Accept 'today', 'tomorrow', 'friday', or an ISO date. None if unparseable.

    Deliberately conservative: an unrecognized string yields None (no due date)
    rather than a guessed one. A wrong due date is worse than no due date.
    """
    if not raw:
        return None
    tz = _tz()
    now = datetime.now(tz)
    r = raw.strip().lower()

    if r in ("today", "eod", "end of day"):
        return now.replace(hour=17, minute=0, second=0, microsecond=0)
    if r == "tomorrow":
        return (now + timedelta(days=1)).replace(hour=17, minute=0, second=0, microsecond=0)
    if r in ("this week", "end of week", "eow"):
        return (now + timedelta(days=(4 - now.weekday()) % 7)).replace(
            hour=17, minute=0, second=0, microsecond=0)
    if r == "next week":
        return (now + timedelta(days=7)).replace(hour=17, minute=0, second=0, microsecond=0)

    days = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
    if r in days:
        delta = (days.index(r) - now.weekday()) % 7 or 7
        return (now + timedelta(days=delta)).replace(hour=17, minute=0, second=0, microsecond=0)

    try:
        dt = datetime.fromisoformat(raw)
        return dt if dt.tzinfo else dt.replace(tzinfo=tz)
    except ValueError:
        return None


def _fmt_due(dt: datetime | None) -> str:
    if dt is None:
        return "no due date"
    local = dt.astimezone(_tz())
    now = datetime.now(_tz())
    if local.date() == now.date():
        return "due today"
    if local.date() == (now + timedelta(days=1)).date():
        return "due tomorrow"
    if local < now:
        return f"OVERDUE ({local.strftime('%b %-d')})"
    return f"due {local.strftime('%a %b %-d')}"


def _push_async(ctx: Context, kind: str, task_id: int) -> None:
    """Queue a Google sync. Bookkeeping must NEVER break the user's reply."""
    from app.google_oauth import is_configured

    if not is_configured():
        return
    try:
        from app.jobs import enqueue

        enqueue(ctx.db, kind, {"task_id": task_id}, channel=ctx.channel,
                thread_key=ctx.thread_key, actor=ctx.actor)
    except Exception as e:  # noqa: BLE001
        log.warning("could not enqueue %s for task %s: %s", kind, task_id, e)


def _add_task(args: dict, ctx: Context) -> str:
    title = (args.get("title") or "").strip()
    if not title:
        return "No task title given."
    t = Task(
        title=title[:500],
        notes=(args.get("notes") or "")[:4000],
        due=_parse_due(args.get("due") or ""),
        priority=(args.get("priority") or "normal").lower(),
        source=ctx.channel,
    )
    ctx.db.add(t)
    ctx.db.commit()
    ctx.db.refresh(t)

    # Push to Google out-of-band so it appears on the user's phone. Never inline:
    # a round-trip to Google inside a voice turn is latency we can't afford.
    _push_async(ctx, "push_task", t.id)

    warn = ""
    if args.get("due") and t.due is None:
        # Say so out loud rather than silently dropping it.
        warn = f" (I couldn't parse the due date {args['due']!r}, so it has none.)"
    return f"Task #{t.id} added: {t.title} — {_fmt_due(t.due)}.{warn}"


def _list_tasks(args: dict, ctx: Context) -> str:
    status = (args.get("status") or "open").lower()
    q = select(Task).order_by(Task.due.is_(None), Task.due, Task.id)
    if status != "all":
        q = q.where(Task.status == status)
    rows = ctx.db.execute(q.limit(30)).scalars().all()
    if not rows:
        return f"No {status} tasks."

    overdue = [t for t in rows if t.due and t.due.astimezone(_tz()) < datetime.now(_tz())]
    lines = [f"#{t.id}: {t.title} — {_fmt_due(t.due)}"
             + (f" [{t.priority}]" if t.priority != "normal" else "")
             for t in rows]
    header = f"{len(rows)} {status} task{'s' if len(rows) != 1 else ''}"
    if overdue:
        header += f", {len(overdue)} overdue"
    return header + ":\n" + "\n".join(lines)


def _complete_task(args: dict, ctx: Context) -> str:
    tid = args.get("task_id")
    if tid is None:
        return "Which task? Give the task number."
    t = ctx.db.get(Task, int(tid))
    if t is None:
        return f"No task #{tid}."
    if t.status == "done":
        return f"Task #{t.id} was already done."
    t.status = "done"
    t.completed_at = datetime.now(_tz())
    ctx.db.commit()
    _push_async(ctx, "complete_task_google", t.id)
    return f"Task #{t.id} complete: {t.title}"


def _cancel_task(args: dict, ctx: Context) -> str:
    tid = args.get("task_id")
    if tid is None:
        return "Which task? Give the task number."
    t = ctx.db.get(Task, int(tid))
    if t is None:
        return f"No task #{tid}."
    t.status = "cancelled"
    ctx.db.commit()
    return f"Task #{t.id} cancelled: {t.title}"


def open_task_summary(db) -> str:
    """For the morning briefing."""
    rows = db.execute(
        select(Task).where(Task.status == "open").order_by(Task.due.is_(None), Task.due).limit(10)
    ).scalars().all()
    if not rows:
        return "No open tasks."
    return "\n".join(f"- {t.title} ({_fmt_due(t.due)})" for t in rows)


def register(reg: Registry) -> None:
    reg.register(
        {
            "name": "add_task",
            "description": "Add a task to the user's task list. Use when they ask you to "
                           "remember to do something, or to follow up on something.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "Short task description."},
                    "notes": {"type": "string", "description": "Optional detail."},
                    "due": {"type": "string",
                            "description": "e.g. 'today', 'tomorrow', 'friday', 'next week', "
                                           "or an ISO date. Omit if none given."},
                    "priority": {"type": "string", "enum": ["low", "normal", "high"]},
                },
                "required": ["title"],
            },
        },
        _add_task,
    )
    reg.register(
        {
            "name": "list_tasks",
            "description": "List the user's tasks. Defaults to open tasks.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "status": {"type": "string", "enum": ["open", "done", "cancelled", "all"]},
                },
            },
        },
        _list_tasks,
    )
    reg.register(
        {
            "name": "complete_task",
            "description": "Mark a task done. Needs the task number from list_tasks.",
            "input_schema": {
                "type": "object",
                "properties": {"task_id": {"type": "integer"}},
                "required": ["task_id"],
            },
        },
        _complete_task,
    )
    reg.register(
        {
            "name": "cancel_task",
            "description": "Cancel a task (it won't be done). Needs the task number.",
            "input_schema": {
                "type": "object",
                "properties": {"task_id": {"type": "integer"}},
                "required": ["task_id"],
            },
        },
        _cancel_task,
    )


# ── Google Tasks push (needs OAuth — impossible with the service account) ────
def push_task_to_google(db, task_id: int) -> str:
    """Push one task to Google Tasks so it lands on the user's phone.

    Runs as a job. One-way (JARVIS -> Google), deliberately: two-way sync means
    conflict resolution, which is a lot of machinery for a personal task list.
    """
    from app.google_oauth import NOT_CONNECTED, tasks_service

    task = db.get(Task, task_id)
    if task is None:
        return f"no task #{task_id}"
    if task.google_id:
        return f"task #{task_id} already pushed"

    svc = tasks_service()
    if svc is None:
        return NOT_CONNECTED

    body = {"title": task.title}
    if task.notes:
        body["notes"] = task.notes
    if task.due:
        # Google Tasks takes RFC3339 and, irritatingly, ignores the time part —
        # it stores date only. Not worth fighting.
        body["due"] = task.due.astimezone(_tz()).isoformat()

    try:
        created = svc.tasks().insert(tasklist="@default", body=body).execute()
    except Exception as e:  # noqa: BLE001
        from app.google_oauth import explain

        hint = explain(e)
        if hint:
            log.error("task push blocked: %s", hint)
            raise RuntimeError(hint) from e
        raise

    task.google_id = created.get("id", "")
    db.commit()
    log.info("pushed task #%s to Google Tasks", task_id)
    return f"pushed task #{task_id} to Google Tasks"


def complete_task_in_google(db, task_id: int) -> str:
    """Mirror a completion to Google so it doesn't sit there ticked-off-nowhere."""
    from app.google_oauth import tasks_service

    task = db.get(Task, task_id)
    if task is None or not task.google_id:
        return "nothing to complete in Google"

    svc = tasks_service()
    if svc is None:
        return "google not connected"

    svc.tasks().patch(
        tasklist="@default", task=task.google_id, body={"status": "completed"}
    ).execute()
    return f"completed task #{task_id} in Google Tasks"
