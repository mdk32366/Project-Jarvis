"""The `github_writes` health component — TDD #3 §7, the last step of the arc.

The load-bearing test here is the substrate one. This check was designed
substrate-first at #53 — the table landed ahead of its writers precisely so the
check could not be built blind against `actions_audit` and be starved from
birth. That decision is only worth anything if something holds it in place.
"""

from datetime import datetime, timedelta, timezone

import pytest

from app.health import _COMPONENTS, get_runbook, seed_health_topology
from app.health_checks import check_github_writes
from app.models import ActionAudit, Component, GithubWriteLog, Remediation


def _spec():
    return next(c for c in _COMPONENTS if c["name"] == "github_writes")


@pytest.fixture
def component(db):
    seed_health_topology(db)
    return db.query(Component).filter_by(name="github_writes").one()


def _write(db, *, ok=True, operation="commit_doc", target="mdk32366/Project-Jarvis:docs/x.md",
           error="", days_ago=0):
    row = GithubWriteLog(operation=operation, target=target, ok=ok, error=error)
    db.add(row)
    db.commit()
    if days_ago:
        row.created_at = datetime.now(timezone.utc) - timedelta(days=days_ago)
        db.commit()
    return row


# ── 1. The three tiers ───────────────────────────────────────────────────────
def test_clean_window_is_ok(db, component):
    _write(db, ok=True)
    _write(db, ok=True, operation="open_pr")

    r = check_github_writes(db, component)
    assert r.status == "ok"
    assert r.fault_code is None
    assert r.last_success_at is not None


def test_a_failed_write_is_degraded_never_down(db, component):
    """§7 is explicit: inability to commit a document is not a system fault.
    Capped at degraded, for the same reason project_hygiene is — inflating
    bookkeeping to `down` trains the eye to ignore the page."""
    _write(db, ok=True)
    _write(db, ok=False, operation="create_repo", target="mdk32366/thing",
           error="422: name already exists")

    r = check_github_writes(db, component)
    assert r.status == "degraded", "a failed write must not escalate to down"
    assert r.fault_code == "write_failed"
    assert r.last_failure_at is not None


def test_many_failures_still_never_reach_down(db, component):
    """The ceiling holds under load, not just for one row."""
    for _ in range(12):
        _write(db, ok=False, error="500")

    r = check_github_writes(db, component)
    assert r.status == "degraded"


def test_no_writes_is_unknown_not_green(db, component):
    """No evidence is not health — the standing rule. A fresh system that has
    written nothing must not read green."""
    r = check_github_writes(db, component)
    assert r.status == "unknown"
    assert r.fault_code == "no_evidence"


def test_writes_outside_the_window_do_not_count(db, component):
    """A failure from a month ago is not today's health — and equally, an old
    success must not hold the check green."""
    _write(db, ok=False, days_ago=30)

    r = check_github_writes(db, component)
    assert r.status == "unknown", "a stale row is no evidence, not evidence of ok"


# ── 2. THE PINNED SUBSTRATE ──────────────────────────────────────────────────
def test_reads_github_write_log_not_actions_audit(db, component):
    """THE DECISION THIS CHECK EXISTS TO HONOUR (#53, restated #56).

    The routine thing exercising GitHub is the `commit_idea` JOB, and jobs write
    no `actions_audit` rows. A check keyed to the audit table would be starved
    from birth — `unknown` forever, or latched on its first failure with nothing
    able to clear it. That is the calendar latch, and it was designed out rather
    than discovered.

    Asserted both ways: the write log drives the verdict, and a contradicting
    `actions_audit` cannot move it.
    """
    # A failed write in the log, and a *successful* audit row that would flip the
    # verdict green if the check were reading the wrong table.
    _write(db, ok=False, error="422")
    db.add(ActionAudit(channel="web", actor="admin", tool="commit_document",
                       arguments="{}", result="fine", status="ok"))
    db.commit()

    r = check_github_writes(db, component)
    assert r.status == "degraded", "the write log must drive the verdict"

    # And the converse: a failing audit row with a clean log must not red it.
    db.query(GithubWriteLog).delete()
    db.commit()
    _write(db, ok=True)
    db.add(ActionAudit(channel="web", actor="admin", tool="commit_document",
                       arguments="{}", result="boom", status="error"))
    db.commit()

    assert check_github_writes(db, component).status == "ok"


def test_the_component_declares_the_bespoke_check_type():
    """Not `liveness`. Reading `actions_audit` is precisely the starvation this
    component was shaped to avoid, so the declaration is pinned."""
    assert _spec()["check_type"] == "github_writes"
    assert _spec()["depends_on"] == "GITHUB_TOKEN"


# ── 3. Non-echo carries to the surface ───────────────────────────────────────
def test_the_check_does_not_round_trip_raw_error_text(db, component):
    """`github_write_log.error` is value-free by #54's invariant, but this result
    renders on the status page. A check that dumps stored error text is one
    refactor away from republishing a secret somebody's scanner caught."""
    _write(db, ok=False, operation="commit_doc", target="mdk32366/repo:docs/a.md",
           error="blocked by secret scan: anthropic_key AND-SOME-RAW-TAIL")

    r = check_github_writes(db, component)
    assert "AND-SOME-RAW-TAIL" not in r.detail
    assert "blocked by secret scan" not in r.detail
    # It still says enough to act on.
    assert "commit_doc" in r.detail and "1 of 1" in r.detail


# ── 4. The join guards, at this component ────────────────────────────────────
def test_every_fault_this_check_emits_has_a_runbook(db, component):
    """The join the two enforcement guards protect globally, asserted locally so
    a failure here names the component rather than a list."""
    seed_health_topology(db)
    for code in ("write_failed", "no_evidence"):
        rb = get_runbook(db, "github_writes", code)
        assert rb is not None, f"no runbook for github_writes/{code}"
        assert len(rb.runbook) > 80, "a runbook that says nothing is not a runbook"


def test_the_runbook_names_the_four_starting_points(db, component):
    """§7: token validity and scope, rate limit, repo existence, branch conflict.
    A runbook is a place to START, not a root-cause claim."""
    rb = get_runbook(db, "github_writes", "write_failed")
    low = rb.runbook.lower()
    for phrase in ("token", "rate limit", "repo", "branch"):
        assert phrase in low, f"runbook omits {phrase!r}"


def test_component_seeds_idempotently(db):
    seed_health_topology(db)
    seed_health_topology(db)
    assert db.query(Component).filter_by(name="github_writes").count() == 1
    assert db.query(Remediation).filter_by(
        component="github_writes", fault_code="write_failed").count() == 1


def test_it_runs_through_the_dispatch_loop(db, component):
    """Registered in `_CHECKS`, so `run_check` finds it — a component whose
    check_type has no function silently reports 'no check for type'."""
    from app.health_checks import run_check
    _write(db, ok=True)
    r = run_check(db, component)
    assert r.status == "ok" and r.component == "github_writes"
