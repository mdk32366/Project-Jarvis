#!/usr/bin/env python
"""One-off backfill: seed the current live multi-session arcs into project tracking.

Build-order step 6 of docs/TDD-project-tracking.md — the only unbuilt step.

RECONSTRUCTION NOTE. The planner's draft of this script was not available in the
repo, so this was rebuilt from the TDD and the session close-outs. The ARC DATA
below (§ARCS) is grounded in:
  - docs/SESSION-closeout-2026-07-19.md  (self-health loop; Duffel parked note)
  - docs/SESSION-closeout-2026-07-21.md  (location pull inversion; the TDD series)
  - docs/jarvis-flight-booking-status.md (Duffel live-activation steps)
It is my reconstruction, not a transcribed record. **Verify it in the dry-run
output before --commit.** If an arc is wrong, fix it HERE and re-run — do not
hand-correct rows afterwards (§2.3), or the next run diverges from the record.

WHY IT GOES THROUGH THE TOOL PATH (§2.1). Every write is dispatched through
build_registry() -> app.handlers.projects, never a direct INSERT. That way every
guard fires: duplicate-name refusal, parked-requires-a-reason, milestone
ordering, fuzzy-match ambiguity. A direct-INSERT backfill would be the first
writer to the table to bypass exactly the validation the TDD's test plan proves.

IDEMPOTENCY (§2.1). create_project refuses a duplicate name, and _add_milestone
has NO dedupe of its own — so a second run must not re-add milestones. The gate
is project existence: if create_project reports the project already exists, the
arc's milestones and status change are skipped ("exists; moving on").

FINISHED MILESTONES (§2.1). A completed checkpoint is add_milestone THEN
complete_milestone, so progress counts read honestly (done/total) rather than
every arc showing zero.

DUFFEL (§2.1). Created `active`, then moved to `parked` via set_project_status so
the reason travels the guarded path — never smuggled in as a create argument.

EXPECTED CONSEQUENCE, NOT A DEFECT (§2.4). project_hygiene may read `degraded`:
any arc with zero OPEN milestones trips the "active project with no open
milestones" anomaly. That is the check working — if it fires, either the arc has
open work that should be recorded or it is not `active`. Do not suppress it.

Usage:
    python backfill_projects.py            # DRY RUN (default): writes nothing
    python backfill_projects.py --commit   # actually write, through the tools

--commit writes to whatever database DATABASE_URL points at (SessionLocal binds
to it). Running against production writes real rows — dry-run first and read the
output (§2.3).
"""

from __future__ import annotations

import os
import re
import sys

# Milestone titles carry em-dashes and §; don't let a legacy Windows console
# codepage crash a real --commit run mid-write. Degrade unencodable glyphs in the
# PRINTED plan only — the strings written to the DB are the originals, untouched.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
except Exception:  # noqa: BLE001 — older/oddly-wrapped stdout: just print as-is
    pass

# The `app` package lives under backend/; make it importable no matter where this
# one-off is run from (repo root or docs/operational/).
_HERE = os.path.dirname(os.path.abspath(__file__))
for _cand in (os.path.join(_HERE, "backend"), _HERE, os.path.join(_HERE, "..", "backend")):
    if os.path.isdir(os.path.join(_cand, "app")):
        sys.path.insert(0, os.path.abspath(_cand))
        break


# ── ARC DATA — reconstructed; verify in dry-run before --commit ───────────────
# Each milestone is (title, "done" | "open", detail-or-None). "done" milestones
# are added and then completed. `final_status` moves the project off `active`
# after seeding (only Duffel uses it, so the park reason goes through the guard).

ARCS: list[dict] = [
    {
        "name": "Location Pull Inversion",
        "summary": (
            "JARVIS pulls location on a schedule she controls; the phone answers "
            "when asked, instead of Tasker firing timed pushes it cannot reliably "
            "schedule on this device."
        ),
        "milestones": [
            ("Server-side pull on the worker tick (migration 0021, LocationRequest, "
             "autoremote provider)", "done", "PR #36"),
            ("Health-check split: location_pull_scheduler + location_responsiveness",
             "done", "PR #36"),
            ("Retain a manual push fallback (migration 0022 trigger column)", "done", "PR #37"),
            ("Read the AutoRemote relay body, not its status code "
             "(relay_accepted/relay_error, migration 0023)", "done", "PR #38"),
            ("Bare-nonce message format + phone-side regex pinned by test", "done", "PR #39"),
            ("Phone: set Tasker message filter to ^[A-Za-z0-9_-]{22}$ and confirm the "
             "first request reaches 'fulfilled'", "open",
             "Owner action — the one thing between here and a working system"),
            ("Phone: §8.1 diagnostic reverts, then export/scrub/commit "
             "devices/jarvis-location-pull.prj.xml", "open", "Owner action"),
            ("Build step 6 request_location_fix() on-demand pull", "open",
             "Deferred until the phone answers — nothing to call before then"),
        ],
    },
    {
        "name": "JARVIS Self-Health Loop",
        "summary": (
            "Detect -> diagnose -> surface -> talk about it: JARVIS reads her own "
            "audit and health state, joins faults to stored runbooks and real "
            "evidence, and reports exception-first on the status page, in chat, and "
            "on voice. Live and has already caught a real production fault."
        ),
        "milestones": [
            ("Audit status derivation — stop hardcoding status=ok", "done", "PR #27"),
            ("Runtime settings overlay + get_effective (migration 0017)", "done", "PR #28"),
            ("Scheduler hardening: heartbeat, catch-up, minute-tick enqueuer "
             "(migration 0018)", "done", "PR #29"),
            ("Relational health model: component/remediation/health_result, seeded "
             "and reconciled on startup", "done", "PR #30"),
            ("Check set: liveness, heartbeat, freshness, app-up", "done", "PR #31"),
            ("GET /api/status/full — parallel checks, runbook + evidence join, "
             "auth-gated, no secrets", "done", "PR #32"),
            ("Exception-first status page + Admin settings panel", "done", "PR #33"),
            ("self_whoami + provenance + request log (migration 0020)", "done", "PR #34"),
            ("Secret-age check (180d degraded / 365d down)", "open",
             "Needs a Fly API token in-container; threshold already decided"),
            ("Morning-brief health section, exception-only (R7)", "open",
             "Brief consumes the same check state the page does; silent when green"),
            ("Real network / tailnet checks (PR-C)", "open",
             "Needs an on-LAN session; which nodes are load-bearing vs informational"),
        ],
    },
    {
        # KEEL is a build-first curriculum/doctrine that lives OUTSIDE Project-Jarvis
        # (docs/Keel/ holds only a slide deck and a diagram). These milestones were
        # supplied by the owner from THIS session's conversation — they were never in
        # the repo, which is why they could not be reconstructed from close-outs like
        # the other four arcs. That gap is itself a Principle-8 (evidence) smell: this
        # arc's source is a chat, not a committed document, so it should become a KEEL
        # close-out in the repo or the gap reopens next week.
        #
        # No documents are attached to this arc (or any arc here): KEEL's artifacts
        # live outside this repo, so an attach_document path would point at files that
        # aren't there. A document record pointing at the wrong place is worse than
        # none — an arc is fine tracked without documents.
        "name": "KEEL Curriculum",
        "summary": (
            "KEEL build-first curriculum/doctrine (external to Project-Jarvis; the "
            "docs/Keel/ deck). The pattern, checklist, and demo artifacts that encode "
            "how projects are built decision-first and in public."
        ),
        "milestones": [
            ("V3 of all seven artifacts", "done", "Public-repo doctrine as imperative"),
            ("V5 core artifacts — Pattern, Checklist, Quick Card, Demo Repo Setup "
             "Guide, Live Demo Script", "done",
             "Principles 8 (evidence) + 9 (binding checks) added; principle 10 (build "
             "public, go private when production-stable); decision-log D-NNN convention "
             "with rejected-options and evidence fields; Planner-will-try-to-build "
             "warning in the two-AI section"),
            ("Update deck to V5", "open",
             "Still at V3: 9 principles, 14 steps, no override moment, no decision "
             "log — materially understates the framework"),
            ("Update How-It-Works SVG to V5", "open", None),
            ("FFIS retrofit as a real work session -> Migration Guide", "open",
             "Also the evidence test for decision-ordered vs TDD-ordered work on a "
             "third project shape (retrofit)"),
            ("Sentinel — prove the Migration Guide twice", "open", None),
            ("gate-demo repo as unsinkable fallback demo", "open", None),
        ],
    },
    {
        "name": "TDD Series (planning + scaffolding)",
        "summary": (
            "The three-part TDD series — project tracking (built), planning "
            "sessions, and repo scaffolding — that gives the next builds durable "
            "design records before code."
        ),
        "milestones": [
            ("TDD #1 project-tracking drafted and built (steps 1-5, migration 0024)",
             "done", "PR #40"),
            ("TDD #2 planning-sessions drafted (completeness-gate design)", "done", None),
            ("TDD #3 repo-scaffolding drafted", "done", None),
            ("Backfill current arcs into project tracking (TDD #1 step 6)", "open",
             "This script — mark done after a confirmed --commit run"),
            ("Build TDD #2 planning-sessions (migration ~0025)", "open", None),
            ("Build TDD #3 repo-scaffolding (migration ~0026)", "open",
             "Check the create_project_from_idea overlap first — §6.2 may be a refactor"),
        ],
    },
    {
        "name": "Duffel Live-Mode Activation",
        "summary": (
            "Flight booking is built, tested, and merged (book_flight, TOTP second "
            "factor, two-stage confirmation gate). Live activation is the only "
            "remaining blocker — Duffel-side plus owner action, gated."
        ),
        "milestones": [
            ("Flight-booking TDD implemented and merged (book_flight, TOTP, "
             "FlightOffer retention, two-stage gate, migration 0010)", "done", None),
            ("Secrets set: TOTP_SECRET, OWNER_DOB, OWNER_GENDER", "done", None),
            ("Request Duffel live-mode access", "open", "Duffel dashboard; unusual profile"),
            ("Top up the Duffel balance", "open",
             "Funds the practical ceiling on top of MAX_BOOKING_USD"),
            ("Set DUFFEL_LIVE_API_KEY (fly secret)", "open", None),
            ("Sanity-check the live token/balance manually before use", "open", None),
            ("Flip BOOKING_ENABLED=true — the single arming switch", "open", None),
        ],
        "final_status": {
            "status": "parked",
            "reason": (
                "Until Duffel grants live-mode access and the balance is funded; then "
                "set DUFFEL_LIVE_API_KEY and flip BOOKING_ENABLED=true. Owner + "
                "Duffel-side, gated."
            ),
        },
    },
]


# ── Runner ────────────────────────────────────────────────────────────────────

TOOLS_USED = ("create_project", "add_milestone", "complete_milestone", "set_project_status")

# add_milestone returns "Milestone #<id> added to <name>: <title>." — completing
# by that exact id beats re-matching the title (complete_milestone fuzzy-matches
# substrings and ASKS on ambiguity, which in a script would silently skip).
_MILESTONE_ID = re.compile(r"Milestone #(\d+)")


def _fmt(args: dict) -> str:
    def _short(v):
        s = str(v)
        return s if len(s) <= 70 else s[:67] + "..."
    return ", ".join(f"{k}={_short(v)!r}" for k, v in args.items())


def _call(reg, ctx, tool: str, args: dict, commit: bool) -> tuple[str, str]:
    """Dispatch one tool through the registry, or print it in dry-run."""
    if not commit:
        print(f"    would {tool}({_fmt(args)})")
        return "", "ok"
    res, status = reg.run_tool(tool, args, ctx)
    marker = "  <-- ERROR" if status != "ok" else ""
    print(f"    {tool}: {res}{marker}")
    return res, status


def _seed(reg, ctx, arc: dict, commit: bool) -> None:
    name = arc["name"]
    print(f"\n=== {name} ===")

    res, status = _call(reg, ctx, "create_project",
                         {"name": name, "summary": arc["summary"]}, commit)
    if commit:
        if status != "ok":
            print("    -> create failed; skipping this arc.")
            return
        if "already a project" in res.lower():
            print("    -> exists; skipping milestones and status (idempotent).")
            return

    for title, state, detail in arc["milestones"]:
        margs = {"project": name, "title": title}
        if detail:
            margs["detail"] = detail
        res, _ = _call(reg, ctx, "add_milestone", margs, commit)
        if state == "done":
            # Complete by the id we just got back (exact); fall back to the title
            # for the dry run, where there is no response to parse.
            ref: object = title
            if commit:
                hit = _MILESTONE_ID.search(res or "")
                if hit:
                    ref = int(hit.group(1))
            _call(reg, ctx, "complete_milestone",
                  {"project": name, "milestone": ref}, commit)

    final = arc.get("final_status")
    if final:
        sargs = {"project": name, "status": final["status"]}
        if final.get("reason"):
            sargs["reason"] = final["reason"]
        _call(reg, ctx, "set_project_status", sargs, commit)


def main() -> int:
    commit = "--commit" in sys.argv[1:]

    from app.handlers.base import Context, build_registry

    # Build the real registry and verify every tool we use is actually on it —
    # this is the "verify each call signature against the live registry" step
    # (§2.1). Name drift fails loudly here rather than mid-write.
    reg = build_registry(include_delegate=False)
    missing = [t for t in TOOLS_USED if not reg.has(t)]
    if missing:
        print(f"ABORT: tools not found on the registry: {missing}")
        return 2

    mode = "COMMIT — writing real rows" if commit else "DRY RUN — nothing is written"
    print(f"backfill_projects: {mode}")
    print(f"tools verified on registry: {', '.join(TOOLS_USED)}")

    if commit:
        from app.database import SessionLocal
        db = SessionLocal()
        try:
            ctx = Context(db=db, channel="backfill", actor="backfill",
                          thread_key="backfill-projects")
            for arc in ARCS:
                _seed(reg, ctx, arc, commit=True)
        finally:
            db.close()
    else:
        # Dry run needs no DB session — it only prints the plan.
        for arc in ARCS:
            _seed(reg, None, arc, commit=False)
        print("\n(dry run) Re-run with --commit to write. Review the arcs above")
        print("first — especially each done/open split, where a close-out summary can")
        print("drift from what actually shipped.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
