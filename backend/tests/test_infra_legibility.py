"""Infra report legibility — the balance model, and the fleet report's own scope.

Both halves are the same lesson pointed at the infra report: **a health surface
must say what it is measuring and under what assumptions**, so a normal state
does not read as alarming and a real gap does not read as clean.

The B0 read that preceded this is recorded in the PR: the missing app was
neither cross-org nor an enumeration bug — the report iterates the
`WATCHED_FLY_APPS` allowlist and never enumerated an org at all.
"""

import httpx
import pytest

from app.config import settings
from app.handlers.base import Context
from app.handlers.infra import _balance_line, _scope_line
from app.runtime_settings import ALLOWED_KEYS, get_effective, set_effective


@pytest.fixture
def ctx(db):
    return Context(db=db, channel="web", actor="admin", thread_key="t1")


# ── Change A: the balance only escalates under a stated prepaid model ────────
def test_autopay_default_never_escalates(db):
    """`$0.00` under autopay is the normal resting state between charges. Daily
    noise in a health surface trains the reader to skim it."""
    line = _balance_line(db, "$0.00", 0, "Matt Kelly")

    assert "$0.00" in line, "the number is still reported as context"
    assert "⚠" not in line
    assert "floor" not in line
    assert "autopay" in line, "the surface states the model it is assuming"


def test_a_prepaid_threshold_fires_below_the_floor(db):
    set_effective(db, "fly_balance_alert_threshold", 50, confirm=True)

    line = _balance_line(db, "$12.40", 1240, "Matt Kelly")

    assert "⚠" in line
    assert "$12.40" in line and "$50" in line
    assert "approaching cutoff" in line


def test_a_prepaid_balance_above_the_floor_is_quiet(db):
    set_effective(db, "fly_balance_alert_threshold", 50, confirm=True)

    line = _balance_line(db, "$300.00", 30000, "Matt Kelly")

    assert "⚠" not in line
    assert "above your $50 floor" in line


def test_the_boundary_is_at_or_below(db):
    """'At or below' — a balance exactly at the floor is already the cutoff."""
    set_effective(db, "fly_balance_alert_threshold", 50, confirm=True)
    assert "⚠" in _balance_line(db, "$50.00", 5000, "org")
    assert "⚠" not in _balance_line(db, "$50.01", 5001, "org")


def test_the_threshold_is_runtime_tunable_not_a_constant(db):
    """The BILLING MODEL is state. Changing tenancy must not need a redeploy."""
    assert "fly_balance_alert_threshold" in ALLOWED_KEYS
    assert settings.fly_balance_alert_threshold is None, "default is autopay"
    assert get_effective(db, "fly_balance_alert_threshold") is None

    set_effective(db, "fly_balance_alert_threshold", 25, confirm=True)
    assert get_effective(db, "fly_balance_alert_threshold") == 25
    assert "⚠" in _balance_line(db, "$10.00", 1000, "org")


def test_the_zero_case_is_not_hardcoded_away(db):
    """The regression this guards: suppressing `$0.00` outright would look like a
    fix and would silently hide a REAL cutoff the next time a prepaid project
    runs. Same number, opposite meaning."""
    set_effective(db, "fly_balance_alert_threshold", 50, confirm=True)
    line = _balance_line(db, "$0.00", 0, "org")
    assert "⚠" in line, "$0.00 under a prepaid model is the cutoff itself"


def test_a_broken_setting_read_does_not_break_the_report(db):
    """A reporting tool must never fail on a settings lookup — it degrades to the
    quiet default rather than taking the panel down."""
    line = _balance_line(None, "$0.00", 0, "org")     # None db -> lookup raises
    assert "$0.00" in line and "⚠" not in line


# ── Change B: the fleet report states its own scope ──────────────────────────
class _Resp:
    def __init__(self, payload, status=200):
        self._p, self.status_code = payload, status

    def json(self):
        return self._p

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("boom", request=None, response=None)


class _FakeClient:
    def __init__(self, apps=None, fail=False):
        self._apps, self._fail = apps or [], fail

    def post(self, *a, **k):
        if self._fail:
            raise RuntimeError("org query unavailable")
        return _Resp({"data": {"personalOrganization": {
            "apps": {"nodes": [{"name": n} for n in self._apps]}}}})


def _install(monkeypatch, client):
    monkeypatch.setattr("app.handlers.infra._request",
                        lambda c, method, url, **kw: c.post(url, **kw))
    return client


def test_the_report_names_an_app_it_is_not_watching(monkeypatch):
    """THE DEFECT THIS CLOSES. A live app the owner knew about was absent and the
    report gave nothing to reconcile against, so he doubted his own memory. It
    must read as 'exists, not watched' — never as a silent omission."""
    client = _install(monkeypatch, _FakeClient(
        apps=["jarvis-mdk", "jarvis-db2", "ffis-scrubber",
              "sentinel-holy-rain-4562", "pharmfoldmdk"]))
    watched = ["jarvis-mdk", "jarvis-db2", "ffis-scrubber", "sentinel-holy-rain-4562"]

    line = _scope_line(client, watched)

    assert "Watching 4 of 5" in line
    assert "pharmfoldmdk" in line
    assert "WATCHED_FLY_APPS" in line, "it says how to fix the gap"


def test_a_fully_watched_org_says_so_without_noise(monkeypatch):
    client = _install(monkeypatch, _FakeClient(apps=["jarvis-mdk", "jarvis-db2"]))

    line = _scope_line(client, ["jarvis-mdk", "jarvis-db2"])

    assert "Watching 2 of 2" in line
    assert "Not on the watchlist" not in line


def test_an_unavailable_org_listing_degrades_honestly(monkeypatch):
    """It must never imply completeness it did not check. Silence about the
    boundary is the original defect; a false 'all watched' would be worse."""
    client = _install(monkeypatch, _FakeClient(fail=True))

    line = _scope_line(client, ["jarvis-mdk"])

    assert "Watching 1 configured app" in line
    assert "Couldn't list the org" in line
    assert "of 1 app(s) in the org" not in line, "must not claim a count it lacks"


def test_the_scope_line_never_claims_to_have_enumerated_an_org_it_did_not(monkeypatch):
    """B0's correction, pinned. The report iterates WATCHED_FLY_APPS; it does not
    enumerate an org to build the list. Saying '4 apps in org Matt Kelly' — the
    fix as originally specified — would have been a NEW false statement."""
    client = _install(monkeypatch, _FakeClient(apps=["a", "b", "c"]))

    line = _scope_line(client, ["a"])

    assert line.startswith("Watching"), "framed as a watchlist, not an org listing"
    assert "Watching 1 of 3" in line
