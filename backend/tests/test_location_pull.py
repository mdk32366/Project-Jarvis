"""Location schedule inversion — JARVIS asks, the phone answers.

Test plan from docs/TDD-location-pull-inversion.md §11.

The through-line: a missing position fix must be ATTRIBUTABLE. The push design
gave one signal ("no pings") for two faults, and establishing which one it was
cost a day of phone-side blind debugging. Most of what's asserted here is that
server-fault and phone-fault stay distinguishable in stored state.
"""

from datetime import datetime, timedelta, timezone

import pytest

from app.config import settings
from app.handlers.location import (
    close_request, due_for_pull, new_request, sweep_timeouts,
)
from app.models import LocationPing, LocationRequest


def _now():
    return datetime.now(timezone.utc)


@pytest.fixture(autouse=True)
def _stub_dispatch(monkeypatch):
    """Never touch the network. Steps 1-4 are fully testable without the phone —
    that is deliberate, since the previous round's cost was phone-side guesswork.

    Patches `request_location`, NOT `send`: the scrubbing tests below need the real
    `send` to run against a fake transport, and a stub one layer too low would make
    those tests silently vacuous.
    """
    sent: list[str] = []
    monkeypatch.setattr(settings, "autoremote_key", "test-key")
    monkeypatch.setattr("app.providers.autoremote.request_location",
                        lambda nonce: (sent.append(nonce), (True, None))[1])
    return sent


@pytest.fixture
def _always_active(monkeypatch):
    monkeypatch.setattr(settings, "location_active_start_hour", 0)
    monkeypatch.setattr(settings, "location_active_end_hour", 24)


def _post_ping(client, body):
    return client.post("/api/location", json=body, headers={"X-Jarvis-Token": "s3cret"})


@pytest.fixture
def _token(monkeypatch):
    monkeypatch.setattr(settings, "location_token", "s3cret")


# ── nonce close-out ──────────────────────────────────────────────────────────

def test_nonce_closes_the_request_and_links_the_ping(client, db, _token):
    req = new_request(db, trigger="scheduled")
    assert req.status == "pending"

    r = _post_ping(client, {"lat": 48.5, "lon": -122.6, "nonce": req.nonce})
    assert r.status_code == 200

    db.expire_all()
    req = db.query(LocationRequest).filter(LocationRequest.nonce == req.nonce).first()
    ping = db.get(LocationPing, r.json()["id"])
    assert req.status == "fulfilled"
    assert req.responded_at is not None
    assert ping.request_id == req.id


def test_late_answer_is_recorded_but_does_not_un_timeout(client, db, _token):
    """A late fix is a real fix. Record it, link it, and leave the request marked
    `timeout` — a chronically-late phone must still read as unresponsive even
    though its positions remain usable."""
    req = new_request(db)
    req.status = "timeout"
    db.commit()

    r = _post_ping(client, {"lat": 48.5, "lon": -122.6, "nonce": req.nonce})
    assert r.status_code == 200                      # NOT a 4xx

    db.expire_all()
    req = db.query(LocationRequest).filter(LocationRequest.nonce == req.nonce).first()
    assert req.status == "timeout"
    assert db.get(LocationPing, r.json()["id"]).request_id == req.id


def test_unsolicited_ping_is_data_not_an_error(client, db, _token):
    """A manual force-run carries no nonce. It is still a real position."""
    r = _post_ping(client, {"lat": 48.5, "lon": -122.6})
    assert r.status_code == 200
    assert db.get(LocationPing, r.json()["id"]).request_id is None


def test_unknown_nonce_loses_the_link_never_the_fix(client, db, _token):
    r = _post_ping(client, {"lat": 48.5, "lon": -122.6, "nonce": "no-such-nonce"})
    assert r.status_code == 200
    assert db.get(LocationPing, r.json()["id"]).request_id is None


def test_unresolved_tasker_variable_is_not_treated_as_a_nonce(client, db, _token):
    """Tasker sends the literal '%arpar1' when the variable never got set. That is
    not a nonce and must not be looked up as one."""
    r = _post_ping(client, {"lat": 48.5, "lon": -122.6, "nonce": "%arpar1"})
    assert r.status_code == 200
    assert db.get(LocationPing, r.json()["id"]).request_id is None


# ── scheduling: due, catch-up, active hours ──────────────────────────────────

def test_first_tick_is_due(db, _always_active):
    assert due_for_pull(db) is True


def test_not_due_again_inside_the_interval(db, _always_active):
    new_request(db)
    assert due_for_pull(db) is False


def test_due_again_after_the_interval(db, _always_active):
    req = new_request(db)
    req.requested_at = _now() - timedelta(minutes=20)      # interval 15
    db.commit()
    assert due_for_pull(db) is True


def test_catch_up_is_one_make_up_request_not_a_burst(db, _always_active):
    """A deploy across several slots must produce ONE make-up pull. A burst is a
    battery event on the owner's phone — exactly the kind of side effect that
    erodes trust in the system that caused it."""
    req = new_request(db)
    req.requested_at = _now() - timedelta(minutes=60)      # four slots missed
    db.commit()

    made = 0
    for _ in range(5):                                     # five worker ticks
        if due_for_pull(db):
            new_request(db)
            made += 1
    assert made == 1


def test_no_scheduled_pull_outside_active_hours(db, monkeypatch):
    monkeypatch.setattr(settings, "location_active_start_hour", 3)
    monkeypatch.setattr(settings, "location_active_end_hour", 3)   # empty -> always outside
    assert due_for_pull(db) is False


def test_disabled_means_no_pull(db, _always_active, monkeypatch):
    monkeypatch.setattr(settings, "location_pull_enabled", False)
    assert due_for_pull(db) is False


# ── the timeout sweep ────────────────────────────────────────────────────────

def test_sweep_ages_out_unanswered_requests(db):
    req = new_request(db)
    req.requested_at = _now() - timedelta(seconds=300)     # timeout 120
    db.commit()
    assert sweep_timeouts(db) == 1
    db.expire_all()
    assert db.get(LocationRequest, req.id).status == "timeout"


def test_sweep_leaves_a_request_still_in_flight_alone(db):
    new_request(db)
    assert sweep_timeouts(db) == 0


def test_sweep_does_not_reopen_a_fulfilled_request(db):
    req = new_request(db)
    close_request(db, req.nonce)
    db.commit()
    req.requested_at = _now() - timedelta(seconds=300)
    db.commit()
    assert sweep_timeouts(db) == 0
    assert db.get(LocationRequest, req.id).status == "fulfilled"


# ── dispatch failure is the SERVER's fault and must be recorded as such ───────

def test_dispatch_failure_is_recorded_on_the_request(db, monkeypatch):
    monkeypatch.setattr("app.providers.autoremote.request_location",
                        lambda nonce: (False, "HTTP 401: bad key"))
    req = new_request(db)
    assert req.dispatch_ok is False
    assert "401" in req.dispatch_error
    assert req.status == "pending"                          # still sweeps normally


def test_failed_dispatch_still_sweeps_to_timeout(db, monkeypatch):
    monkeypatch.setattr("app.providers.autoremote.request_location",
                        lambda nonce: (False, "boom"))
    req = new_request(db)
    req.requested_at = _now() - timedelta(seconds=300)
    db.commit()
    assert sweep_timeouts(db) == 1


def test_request_row_exists_even_if_dispatch_raises(db, monkeypatch):
    """The ask is committed BEFORE dispatch. An un-recorded dispatch would be
    indistinguishable from a scheduler that never ran."""
    def _boom(nonce):
        raise RuntimeError("network gone")
    monkeypatch.setattr("app.providers.autoremote.request_location", _boom)

    with pytest.raises(RuntimeError):
        new_request(db)
    assert db.query(LocationRequest).count() == 1


# ── the key is a secret ──────────────────────────────────────────────────────

def test_key_never_reaches_the_dispatch_error(db, monkeypatch, caplog):
    """`dispatch_error` is stored in the database AND rendered on the status page,
    so a key that leaked into it would leak twice. Asserted, not assumed."""
    import httpx

    from app.providers import autoremote

    secret = "super-secret-key-value"
    monkeypatch.setattr(settings, "autoremote_key", secret)

    class _Resp:
        status_code = 401
        text = f"unauthorized for key={secret}"

    class _Client:
        def __init__(self, **kw): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def post(self, url, data=None): return _Resp()

    monkeypatch.setattr(httpx, "Client", _Client)

    with caplog.at_level("DEBUG"):
        ok, err = autoremote.send("jarvis_locreq=:=abc")

    assert ok is False
    assert secret not in (err or "")
    assert secret not in caplog.text

    # ...and the same failure travelling the real path into the database. Restore
    # the genuine request_location so this exercises `send` rather than the stub.
    monkeypatch.setattr("app.providers.autoremote.request_location",
                        lambda nonce: autoremote.send(f"{autoremote.MESSAGE_PREFIX}=:={nonce}"))
    req = new_request(db)
    assert req.dispatch_ok is False
    assert req.dispatch_error and secret not in req.dispatch_error


def test_transport_error_message_is_scrubbed(monkeypatch):
    import httpx

    from app.providers import autoremote

    secret = "another-secret"
    monkeypatch.setattr(settings, "autoremote_key", secret)

    class _Client:
        def __init__(self, **kw): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def post(self, url, data=None):
            raise httpx.ConnectError(f"failed connecting with key={secret}")

    monkeypatch.setattr(httpx, "Client", _Client)
    ok, err = autoremote.send("m")
    assert ok is False and secret not in (err or "")


def test_unconfigured_key_fails_closed(monkeypatch):
    from app.providers import autoremote

    monkeypatch.setattr(settings, "autoremote_key", "")
    ok, err = autoremote.send("m")
    assert ok is False and "not configured" in err


def test_autoremote_key_is_not_runtime_overridable():
    """The runtime-settings allow-list is the enforcement boundary for secrets."""
    from app.runtime_settings import ALLOWED_KEYS

    assert "autoremote_key" not in ALLOWED_KEYS
    assert "location_pull_enabled" in ALLOWED_KEYS
