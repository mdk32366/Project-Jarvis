"""Capability rollup — what JARVIS can DO right now (capability TDD §4.3).

Components answer "is this part working". This answers "can she still tell me
where I am", which is the question that was actually asked. Both are needed:
`google_calendar_svcacct: down` is a true sentence that nobody reads as "your
calendar is broken", and the whole point of a rollup is to say the second thing.

THE RULES, and where each came from:

  red      primary member is `down`
  amber    primary is `degraded`, OR any non-primary is `down`/`degraded`
  unknown  primary is `unknown` — no basis to judge the capability at all
  ok       primary ok, no non-primary fault
  gated    lifecycle=gated — reported as not-configured, never counted

`primary down -> red, primary degraded -> amber` is DERIVED, not dictated. The
orders say "the member whose non-ok forces red"; taken literally that would red
project tracking on a `degraded` project_hygiene, contradicting the amber ceiling
ratified in the same document. Degraded means impaired, not broken, and a
capability whose worst honest state is amber must be allowed to say so.

A non-primary `unknown` deliberately does NOT move the rollup. For a SECONDARY,
absence of evidence is weaker than evidence of failure — and treating it as amber
would paint the morning brief permanently amber off `gmail`/`nws` simply having no
recent calls, which is exactly how a status surface trains people to ignore it.
Unknown members are still carried in the payload; they are reported, not counted.
"""

from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from app.health import get_runbook
from app.models import Capability, CapabilityMember, Component, HealthResult

log = logging.getLogger(__name__)

# Worst-first. Used to pick the DRIVING member — the one whose fault is the reason
# the capability reads the way it does, and therefore whose runbook to show.
_SEVERITY = {"down": 3, "degraded": 2, "unknown": 1, "ok": 0}


def _member_rows(db: Session, cap: str) -> list[CapabilityMember]:
    return (
        db.query(CapabilityMember)
        .filter(CapabilityMember.capability == cap)
        .order_by(CapabilityMember.is_primary.desc(), CapabilityMember.component)
        .all()
    )


def _is_gated(db: Session, cap: Capability) -> bool:
    """A gated capability is excluded from the live rollup. `gated_by` names a
    Settings flag; when it is set and TRUE the capability has been turned on and
    stops being gated. An empty `gated_by` means permanently gated (the LAN stubs:
    no flag turns them on — being on the LAN does)."""
    if cap.lifecycle != "gated":
        return False
    if not cap.gated_by:
        return True
    from app.config import settings

    return not bool(getattr(settings, cap.gated_by, False))


def evaluate(db: Session) -> list[dict]:
    """Roll every enabled capability up from the CURRENT `health_result` rows.

    Reads stored results rather than re-running checks: `run_all_checks` is what
    produces them, and running checks twice per request would double the DB cost
    and let the two answers disagree within one page.
    """
    out: list[dict] = []
    caps = (
        db.query(Capability)
        .filter(Capability.enabled.is_(True))
        .order_by(Capability.lifecycle, Capability.name)
        .all()
    )
    for cap in caps:
        members = _member_rows(db, cap.name)
        detail_members = []
        primary_status = None
        worst_non_primary = None

        for m in members:
            hr = db.get(HealthResult, m.component)
            status = hr.status if hr else "unknown"
            fault = hr.fault_code if hr else None
            detail_members.append({
                "component": m.component,
                "is_primary": bool(m.is_primary),
                "status": status,
                "fault_code": fault,
                "detail": (hr.detail if hr else "no health result recorded"),
            })
            if m.is_primary:
                primary_status = status
            elif status in ("down", "degraded"):
                if (worst_non_primary is None
                        or _SEVERITY[status] > _SEVERITY[worst_non_primary["status"]]):
                    worst_non_primary = detail_members[-1]

        if _is_gated(db, cap):
            status = "gated"
            driver = None
        elif primary_status is None:
            # A capability with no primary cannot be judged. The meta-check reports
            # this as incoherent; here it must not silently read green.
            status, driver = "unknown", None
        elif primary_status == "down":
            status = "red"
            driver = next((d for d in detail_members if d["is_primary"]), None)
        elif primary_status == "unknown":
            status = "unknown"
            driver = next((d for d in detail_members if d["is_primary"]), None)
        elif primary_status == "degraded":
            status = "amber"
            driver = next((d for d in detail_members if d["is_primary"]), None)
        elif worst_non_primary is not None:
            status = "amber"
            driver = worst_non_primary
        else:
            status, driver = "ok", None

        item = {
            "name": cap.name,
            "label": cap.label or cap.name,
            "status": status,
            "lifecycle": cap.lifecycle,
            "description": cap.description,
            "members": detail_members,
        }
        if cap.notes:
            item["notes"] = cap.notes
        if cap.lifecycle == "gated":
            item["gated_by"] = cap.gated_by or "not reachable from this host"
        if driver is not None:
            item["driving_member"] = driver["component"]
            # The runbook belongs to the member actually responsible — never
            # improvised, and never the capability's own generic text. If no
            # runbook exists for that (component, fault_code) the caller shows the
            # fault alone rather than inventing guidance.
            rem = (get_runbook(db, driver["component"], driver["fault_code"])
                   if driver["fault_code"] else None)
            item["remediation"] = ({"runbook": rem.runbook, "severity": rem.severity}
                                   if rem else None)
        out.append(item)
    return out


def summary(rollup: list[dict]) -> dict:
    """Counts for the live (non-gated) capabilities, plus the gated tally."""
    live = [c for c in rollup if c["status"] != "gated"]
    counts = {"ok": 0, "amber": 0, "red": 0, "unknown": 0}
    for c in live:
        counts[c["status"]] = counts.get(c["status"], 0) + 1
    return {
        "live_total": len(live),
        "gated_total": len(rollup) - len(live),
        **counts,
    }


def coherence_problems(db: Session) -> list[str]:
    """The meta-check's substance: is the rollup built on parts that exist?

    A capability whose member was renamed or disabled keeps producing an answer —
    a confident one — assembled from a component nobody is checking. That failure
    is invisible from the rollup itself, which is why it is checked separately and
    reported against `health_evaluator`.
    """
    problems: list[str] = []
    caps = db.query(Capability).filter(Capability.enabled.is_(True)).all()
    for cap in caps:
        members = _member_rows(db, cap.name)
        if not members:
            problems.append(f"{cap.name}: no members")
            continue
        if not any(m.is_primary for m in members):
            problems.append(f"{cap.name}: no primary member")
        if sum(1 for m in members if m.is_primary) > 1:
            problems.append(f"{cap.name}: more than one primary")
        for m in members:
            comp = db.get(Component, m.component)
            if comp is None:
                problems.append(f"{cap.name}: member {m.component!r} is not a component")
            elif not comp.enabled:
                problems.append(f"{cap.name}: member {m.component!r} is disabled")
    return problems


def brief_line(db: Session) -> str:
    """The morning-brief systems line (orders: count only, ambers/reds named
    inline, detail stays in the systems section).

    THE SCOPED EXCEPTION to exception-first: an all-green rollup still says so, out
    loud, every day. Everywhere else silence means fine — but a monitor that only
    ever speaks up when something is wrong is indistinguishable from a monitor that
    has stopped running, and this is the one line whose job is to prove otherwise.
    """
    rollup = evaluate(db)
    s = summary(rollup)
    if not s["live_total"]:
        return ""

    bad = [c for c in rollup if c["status"] in ("red", "amber")]
    unknown = [c for c in rollup if c["status"] == "unknown"]

    if not bad and not unknown:
        return f"All {s['live_total']} capabilities green."

    parts = []
    for c in sorted(bad, key=lambda c: 0 if c["status"] == "red" else 1):
        parts.append(f"{c['label']} {c['status']}")
    for c in unknown:
        parts.append(f"{c['label']} unknown")
    return f"{s['ok']} of {s['live_total']} capabilities green — " + ", ".join(parts) + "."
