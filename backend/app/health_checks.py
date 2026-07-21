"""Health checks — the v1 check set (health TDD §4.4, §5, roadmap R5).

Each check reads real state and returns a `CheckResult`; `run_all_checks` upserts
those into the `health_result` table. Checks NEVER raise into their caller: a
check that blows up returns `unknown` with the exception in `detail`, so one
broken check can't take down the whole status page (§4.4). The remediation
runbook is NOT carried here — it's joined from `remediation` at surface time
(PR-D/PR-E) via `(component, fault_code)`.

v1 checks: credential **liveness** (§5.1, reads `actions_audit` — PR-0 made that
substrate truthful), scheduler **heartbeat** (§5.2, reads `scheduler_heartbeat` +
its seeded `stale_seconds`), the two halves of the **location split**
(`location_scheduler` / `location_responsiveness`, honouring the runtime
active-hours window), and **app up-status**.

The location split replaced a single freshness check that read one signal —
"pings are stale" — for two different faults. It could not distinguish a server
that had stopped asking from a phone that had stopped answering, and 2026-07-19
was spent establishing by hand which one it was. The two checks below each read a
fault they can actually attribute, and their runbooks point at different machines.

Deliberately deferred (a conscious call, not an oversight): **secret-age** needs
Fly secret metadata, which isn't reachable from inside the app container without
a Fly API token — shipping it as perpetually `unknown` would violate the "three
honest tiers, no guessing" rule; it lands once the token plumbing exists (the
90-day threshold is already configured). **Published expiry** applies only to a
service that genuinely publishes one; Google OAuth refresh tokens don't, so there
is nothing honest to report beyond liveness — no fabricated countdown.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import settings
from app.health import component_for_tool, get_runbook
from app.models import (
    ActionAudit, Component, HealthResult, LocationRequest, SchedulerHeartbeat,
)

log = logging.getLogger(__name__)

# Audit statuses that mean "the call was fine" — a confirmed send and a refused
# gated call are the SAFETY MACHINERY WORKING, not faults (PR-0 / build §0.3).
_OK_AUDIT = {"ok", "confirmed", "refused"}
_LIVENESS_WINDOW_DAYS = 30   # bounded lookback over actions_audit

# The audit substrate only became truthful at the PR-0 deploy (commit 9855a28,
# deployed 2026-07-19T19:09:19Z): before it, every actions_audit row was
# status="ok" by construction (the old code hardcoded it). Those rows are NOT
# evidence of health — counting them would let a component seen ONLY before the
# epoch report a false "ok" instead of the honest "unknown" (the exact false-green
# the "no evidence → unknown" rule exists to prevent). Liveness floors its window
# here, so pre-epoch fabricated history can't dilute the signal.
_AUDIT_TRUTHFUL_EPOCH = datetime(2026, 7, 19, 19, 9, 19, tzinfo=timezone.utc)


@dataclass
class CheckResult:
    component: str
    status: str                      # ok | degraded | down | unknown
    fault_code: str | None = None
    detail: str = ""
    checked_at: datetime | None = None
    expires_at: datetime | None = None
    age_days: int | None = None
    last_success_at: datetime | None = None
    last_failure_at: datetime | None = None


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dt: datetime | None) -> datetime | None:
    """Normalize a stored datetime to tz-aware UTC. Postgres timestamptz round-
    trips aware, but SQLite (dev/tests) drops the tz — so any DB datetime is
    coerced before arithmetic, or `_now() - dt` raises on the naive case."""
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _cfg(component: Component) -> dict:
    try:
        return json.loads(component.check_config) if component.check_config else {}
    except (TypeError, ValueError):
        return {}


# ── the checks ───────────────────────────────────────────────────────────────

def check_liveness(db: Session, c: Component) -> CheckResult:
    """Derive credential/API liveness from recent `actions_audit` (§5.1) — no new
    write path. Reads the outcomes of the component's tools: unhealthy iff its most
    recent call FAILED; `unknown` when there's no evidence (absence ≠ health)."""
    since = max(_now() - timedelta(days=_LIVENESS_WINDOW_DAYS), _AUDIT_TRUTHFUL_EPOCH)
    rows = [
        r for r in db.query(ActionAudit).filter(ActionAudit.created_at >= since).all()
        if component_for_tool(r.tool) == c.name
    ]
    if not rows:
        return CheckResult(c.name, "unknown", "no_evidence",
                           f"no calls in {_LIVENESS_WINDOW_DAYS}d", checked_at=_now())
    last_success = max((r.created_at for r in rows if r.status in _OK_AUDIT), default=None)
    last_failure = max((r.created_at for r in rows if r.status not in _OK_AUDIT), default=None)
    if last_failure and (last_success is None or last_failure > last_success):
        return CheckResult(c.name, "down", "call_failed",
                           "most recent call failed", checked_at=_now(),
                           last_success_at=last_success, last_failure_at=last_failure)
    return CheckResult(c.name, "ok", None, "recent calls succeeding", checked_at=_now(),
                       last_success_at=last_success, last_failure_at=last_failure)


def check_heartbeat(db: Session, c: Component) -> CheckResult:
    """Scheduler proof-of-life (§5.2): stale beat -> down, disabled -> ok+labeled,
    alive -> ok with next-run. Staleness threshold comes from the seeded
    `check_config` (data, not code)."""
    stale_seconds = _cfg(c).get("stale_seconds", 300)
    hb = db.get(SchedulerHeartbeat, 1)
    beat = _aware(hb.beat_at) if hb else None
    if hb is None or beat is None:
        return CheckResult(c.name, "unknown", "no_heartbeat",
                           "scheduler has not reported yet", checked_at=_now())
    if not hb.enabled:
        return CheckResult(c.name, "ok", None, "briefing disabled", checked_at=_now(),
                           last_success_at=beat)
    age = (_now() - beat).total_seconds()
    if age > stale_seconds:
        return CheckResult(c.name, "down", "heartbeat_stale",
                           f"no heartbeat in {int(age)}s (> {stale_seconds}s)",
                           checked_at=_now(), last_failure_at=beat)
    return CheckResult(c.name, "ok", None,
                       f"alive; next run {hb.next_run_at}", checked_at=_now(),
                       last_success_at=beat)


def check_location_scheduler(db: Session, c: Component) -> CheckResult:
    """*Is the server asking?* — half one of the location split (TDD §7.1).

    Reads only `location_request` rows with `trigger=scheduled`. This deliberately
    ignores whether the phone answered: that is the other check's job, and keeping
    them separate is what makes a missing fix attributable instead of inferred.
    """
    from app.handlers.location import in_active_hours
    from app.runtime_settings import get_effective

    interval = get_effective(db, "location_pull_interval_minutes")
    last = (
        db.query(LocationRequest)
        .filter(LocationRequest.trigger == "scheduled")
        .order_by(LocationRequest.id.desc())
        .first()
    )
    if last is None:
        # No evidence is not health. A fresh deploy has no basis for a green.
        return CheckResult(c.name, "unknown", "no_requests",
                           "no scheduled pull has ever been sent", checked_at=_now())

    at = _aware(last.requested_at)
    age_min = (_now() - at).total_seconds() / 60

    if not in_active_hours(db):
        return CheckResult(c.name, "ok", None,
                           f"outside active hours; last pull {int(age_min)}m ago",
                           checked_at=_now(), last_success_at=at)

    # A relay rejection is a distinct fault from a scheduler that never ran, and it
    # sends you somewhere else entirely (the key, not the worker), so it gets its
    # own code even while requests are being minted on time.
    #
    # SCOPE, stated because the previous version overclaimed it: this sees only
    # whether the RELAY accepted the message. It cannot see the relay→FCM→phone
    # leg. A message accepted and then never delivered reads ok here while
    # `location_responsiveness` correctly goes down — so when responsiveness is
    # down and this is ok, the phone-side runbook is not automatically right.
    if not last.relay_accepted:
        return CheckResult(c.name, "down", "relay_rejected",
                           f"last pull {int(age_min)}m ago was not accepted by the relay: "
                           f"{last.relay_error or 'unknown error'}",
                           checked_at=_now(), last_failure_at=at)

    if age_min <= interval + 5:
        status, fault = "ok", None
    elif age_min <= interval * 2:
        # Late but not dead — the same three-tier shape as the freshness check.
        status, fault = "degraded", "not_asking"
    else:
        status, fault = "down", "not_asking"
    return CheckResult(c.name, status, fault,
                       f"last scheduled pull {int(age_min)}m ago (interval {interval}m)",
                       checked_at=_now(),
                       last_success_at=at if status == "ok" else None,
                       last_failure_at=None if status == "ok" else at)


def check_location_responsiveness(db: Session, c: Component) -> CheckResult:
    """*Is the phone answering?* — half two of the location split (TDD §7.2).

    Scores the trailing window of COMPLETED requests (both triggers). Pending rows
    are excluded because they haven't had their chance yet; counting them would
    read as a fault every time a pull is in flight.
    """
    cfg = _cfg(c)
    window = cfg.get("window", 6)
    ok_min = cfg.get("ok_min", 5)
    deg_min = cfg.get("degraded_min", 3)

    rows = (
        db.query(LocationRequest)
        .filter(LocationRequest.status.in_(("fulfilled", "timeout")))
        .order_by(LocationRequest.id.desc())
        .limit(window)
        .all()
    )
    if len(rows) < deg_min:
        return CheckResult(c.name, "unknown", "no_evidence",
                           f"only {len(rows)} completed request(s); need {deg_min} to judge",
                           checked_at=_now())

    fulfilled = [r for r in rows if r.status == "fulfilled"]
    n = len(fulfilled)
    if n >= ok_min:
        status, fault = "ok", None
    elif n >= deg_min:
        status, fault = "degraded", "not_answering"
    else:
        status, fault = "down", "not_answering"
    return CheckResult(
        c.name, status, fault,
        f"{n} of the last {len(rows)} requests answered", checked_at=_now(),
        last_success_at=max((_aware(r.responded_at) for r in fulfilled
                             if r.responded_at), default=None),
        last_failure_at=max((_aware(r.requested_at) for r in rows
                             if r.status == "timeout" and r.requested_at), default=None),
    )


def check_app_up(db: Session, c: Component) -> CheckResult:
    """App up-status: if this code is running and the DB answers, the app + its
    Postgres are up. A trivially-true check by construction — but it makes 'the
    app is serving' an explicit, visible line rather than an assumption."""
    db.execute(text("SELECT 1"))
    return CheckResult(c.name, "ok", None, "serving", checked_at=_now(), last_success_at=_now())


# check_type -> check fn
_CHECKS = {
    "liveness": check_liveness,
    "heartbeat": check_heartbeat,
    "location_scheduler": check_location_scheduler,
    "location_responsiveness": check_location_responsiveness,
}
# components whose up-status is the app itself (postgres/anthropic liveness is
# really "is the app up") get app_up when they have no more specific check.
_APP_UP = {"postgres"}


def run_check(db: Session, c: Component) -> CheckResult:
    """Run the check for a component, guaranteeing it never raises: any exception
    becomes `unknown` with the error in `detail` (§4.4). One broken check must not
    take the status page down."""
    try:
        if c.name in _APP_UP:
            return check_app_up(db, c)
        fn = _CHECKS.get(c.check_type)
        if fn is None:
            return CheckResult(c.name, "unknown", None,
                               f"no check for type '{c.check_type}'", checked_at=_now())
        return fn(db, c)
    except Exception as e:  # noqa: BLE001 — a check must never raise into its caller
        log.error("health check %r raised: %s", c.name, e)
        return CheckResult(c.name, "unknown", "check_error", f"check raised: {e}",
                           checked_at=_now())


def _upsert(db: Session, r: CheckResult) -> None:
    row = db.get(HealthResult, r.component)
    if row is None:
        row = HealthResult(component=r.component)
        db.add(row)
    row.status = r.status
    row.fault_code = r.fault_code
    row.detail = r.detail
    row.checked_at = r.checked_at
    row.expires_at = r.expires_at
    row.age_days = r.age_days
    row.last_success_at = r.last_success_at
    row.last_failure_at = r.last_failure_at


def run_all_checks(db: Session) -> list[CheckResult]:
    """Run every enabled component's check and upsert `health_result`. Returns the
    results (trunk first, so a blast-radius=multi failure surfaces prominently).

    Sequential on the request thread on purpose: every v1 check is DB-bound, and a
    SQLAlchemy Session is not thread-safe (build §2.4 — DB checks stay on the main
    thread). Parallelization via a thread pool becomes worthwhile only once
    external-call checks (e.g. secret-age hitting the Fly API) land; those are the
    ones that can run off-thread without touching the session."""
    comps = db.query(Component).filter(Component.enabled.is_(True)).all()
    comps.sort(key=lambda c: (c.blast_radius != "multi", c.name))  # trunk first
    results = []
    for c in comps:
        if c.check_type == "none" and c.name not in _APP_UP:
            continue  # organizational rows (agents, stubs) carry no direct check
        r = run_check(db, c)
        _upsert(db, r)
        results.append(r)
    db.commit()
    return results


def _evidence_for(db: Session, component: str, limit: int = 5) -> list[dict]:
    """The recent post-epoch non-ok `actions_audit` rows for a component — the
    §4A bridge that turns 'scheduling: down' into the actual failing calls. Only
    truthful (post-PR-0-epoch) rows count; a `confirmed`/`refused` row is not
    evidence of a fault."""
    rows = (
        db.query(ActionAudit)
        .filter(ActionAudit.created_at >= _AUDIT_TRUTHFUL_EPOCH,
                ActionAudit.status.notin_(_OK_AUDIT))
        .order_by(ActionAudit.created_at.desc())
        .limit(200)
        .all()
    )
    mine = [r for r in rows if component_for_tool(r.tool) == component][:limit]
    return [{"tool": r.tool, "status": r.status,
             "detail": (r.result or "")[:160],
             "at": _aware(r.created_at).isoformat() if r.created_at else None}
            for r in mine]


def _iso(dt: datetime | None) -> str | None:
    dt = _aware(dt)
    return dt.isoformat() if dt else None


def status_payload(db: Session) -> dict:
    """The single `/api/status/full` payload: runs every check fresh, upserts
    `health_result`, and for anything not-ok joins its stored runbook (never
    improvised) and its evidence rows. Contains NO secrets — component names,
    statuses, timestamps, runbooks, and (owner-only, admin-gated) recent failing
    calls."""
    results = run_all_checks(db)
    checks = []
    for r in results:
        item = {
            "component": r.component,
            "status": r.status,
            "fault_code": r.fault_code,
            "detail": r.detail,
            "checked_at": _iso(r.checked_at),
            "expires_at": _iso(r.expires_at),
            "age_days": r.age_days,
            "last_success_at": _iso(r.last_success_at),
            "last_failure_at": _iso(r.last_failure_at),
        }
        if r.status != "ok":
            rem = get_runbook(db, r.component, r.fault_code) if r.fault_code else None
            item["remediation"] = ({"runbook": rem.runbook, "severity": rem.severity}
                                   if rem else None)
            item["evidence"] = _evidence_for(db, r.component)
        checks.append(item)

    summary: dict[str, int] = {"ok": 0, "degraded": 0, "down": 0, "unknown": 0}
    for r in results:
        summary[r.status] = summary.get(r.status, 0) + 1
    return {"generated_at": _iso(_now()), "summary": summary, "checks": checks}
