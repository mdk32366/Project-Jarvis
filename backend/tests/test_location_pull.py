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
from app.providers import autoremote as _autoremote

# Captured at import, BEFORE the autouse stub below replaces the module
# attribute. Tests that need to assert what `request_location` actually puts on
# the wire must call this, or they assert the behaviour of the stub instead.
_REAL_REQUEST_LOCATION = _autoremote.request_location


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


def test_manual_push_is_recorded_and_creates_no_request(client, db, _token):
    """The retained fallback (§6.6): a task with no profile, run by hand. It posts
    no nonce and claims nothing, which is exactly why it survived the cull that
    removed the timed profile."""
    r = client.post("/api/location",
                    json={"lat": 48.5, "lon": -122.6, "source": "tasker", "trigger": "manual"},
                    headers={"X-Jarvis-Token": "s3cret"})
    assert r.status_code == 200

    ping = db.get(LocationPing, r.json()["id"])
    assert ping.request_id is None
    assert ping.trigger == "manual"
    assert db.query(LocationRequest).count() == 0      # a manual push asks nothing


def test_manual_push_cannot_mask_an_unresponsive_phone(client, db, _token):
    """THE containment property, and the whole basis on which the fallback was
    retained (§6.6). Responsiveness scores REQUEST FULFILMENT, never ping recency —
    so a fresh manual push, however recent, cannot paint a phone green while it is
    ignoring every pull. This is the payoff of retiring the freshness-only check
    rather than merely supplementing it."""
    from app.health import seed_health_topology
    from app.health_checks import check_location_responsiveness
    from app.models import Component

    seed_health_topology(db)
    for i in range(6):
        db.add(LocationRequest(nonce=f"ignored-{i}", trigger="scheduled", status="timeout"))
    db.commit()

    c = db.get(Component, "location_responsiveness")
    assert check_location_responsiveness(db, c).status == "down"

    r = client.post("/api/location",
                    json={"lat": 48.5, "lon": -122.6, "trigger": "manual"},
                    headers={"X-Jarvis-Token": "s3cret"})
    assert r.status_code == 200                        # the fix is accepted...

    db.expire_all()
    assert check_location_responsiveness(db, c).status == "down"   # ...and changes nothing


def test_legacy_ping_without_trigger_is_still_accepted(client, db, _token):
    """A client that has never heard of `trigger` still reports real positions."""
    r = client.post("/api/location", json={"lat": 48.5, "lon": -122.6},
                    headers={"X-Jarvis-Token": "s3cret"})
    assert r.status_code == 200
    assert db.get(LocationPing, r.json()["id"]).trigger is None


def test_pull_answer_records_its_trigger(client, db, _token):
    req = new_request(db)
    r = client.post("/api/location",
                    json={"lat": 48.5, "lon": -122.6, "nonce": req.nonce, "trigger": "pull"},
                    headers={"X-Jarvis-Token": "s3cret"})
    assert r.status_code == 200
    assert db.get(LocationPing, r.json()["id"]).trigger == "pull"


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
    assert req.relay_accepted is False
    assert "401" in req.relay_error
    assert req.status == "pending"                          # still sweeps normally


# ── the relay answers 200 to everything; the BODY is the outcome ─────────────

def _fake_transport(monkeypatch, *, status=200, body="OK"):
    """Stand in for the relay. Returns whatever body the test asks for."""
    import httpx

    class _Resp:
        status_code = status
        text = body

    class _Client:
        def __init__(self, **kw): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def post(self, url, data=None):
            _Client.sent = data
            return _Resp()

    monkeypatch.setattr(httpx, "Client", _Client)
    return _Client


def test_not_registered_body_is_a_failure_despite_http_200(db, monkeypatch):
    """THE regression. From the PR #36 deploy until 2026-07-21 the relay answered
    `NotRegistered` to every single send while the code recorded success, because
    it read the status code and not the body. A total delivery failure that read
    green for the whole life of the feature."""
    from app.providers import autoremote

    monkeypatch.setattr(settings, "autoremote_key", "tok")
    _fake_transport(monkeypatch, status=200, body="NotRegistered")

    ok, err = autoremote.send("jarvis_locreq")
    assert ok is False
    assert "NotRegistered" in err

    monkeypatch.setattr("app.providers.autoremote.request_location",
                        lambda nonce: autoremote.send("x"))
    req = new_request(db)
    assert req.relay_accepted is False
    assert "NotRegistered" in req.relay_error


def test_ok_body_is_the_only_success(monkeypatch):
    from app.providers import autoremote

    monkeypatch.setattr(settings, "autoremote_key", "tok")
    _fake_transport(monkeypatch, body="OK")
    assert autoremote.send("m") == (True, None)

    for body in ("NotRegistered", "", "error", "Bad Request"):
        _fake_transport(monkeypatch, body=body)
        ok, err = autoremote.send("m")
        assert ok is False, body
        assert err


def test_leading_key_prefix_is_stripped_before_sending(monkeypatch):
    """The AutoRemote web page shows the key inside a URL, so `key=<token>` is the
    natural thing to copy — and it is what was stored in Fly, producing
    `key=key%3D<token>` on the wire and a NotRegistered on every send. A config
    typo must not be able to silently disable the feature."""
    from app.providers import autoremote

    monkeypatch.setattr(settings, "autoremote_key", "key=abc123")
    client = _fake_transport(monkeypatch, body="OK")

    ok, _ = autoremote.send("m")
    assert ok is True
    assert client.sent["key"] == "abc123"            # prefix gone


def test_bare_key_is_left_alone(monkeypatch):
    from app.providers import autoremote

    monkeypatch.setattr(settings, "autoremote_key", "abc123")
    client = _fake_transport(monkeypatch, body="OK")
    autoremote.send("m")
    assert client.sent["key"] == "abc123"


def test_the_message_is_the_bare_nonce(monkeypatch):
    """No command word, no `=:=` separator — the message IS the nonce.

    Ground truth from the device (2026-07-21): with `jarvis_locreq=:=<nonce>` the
    phone showed `%arpar1 = jarvis_locreq` and `%arpar2` unresolved. The split
    produced ONE field, so the nonce never reached a variable the task could read.
    `%arpar1` populates with whatever is in first position, so the nonce goes
    there alone.
    """
    from app.providers import autoremote

    monkeypatch.setattr(settings, "autoremote_key", "tok")
    client = _fake_transport(monkeypatch, body="OK")
    _REAL_REQUEST_LOCATION("sZKSkt03goMfmcX5si2suQ")   # not the autouse stub

    assert client.sent["message"] == "sZKSkt03goMfmcX5si2suQ"
    assert "=:=" not in client.sent["message"]
    assert "jarvis_locreq" not in client.sent["message"]


def test_minted_nonces_match_the_phone_side_filter(db):
    """The Event profile matches a nonce PATTERN now, not a command word, so the
    shape of what we mint is load-bearing on the phone. If this ever drifts, the
    filter silently stops matching and every request times out — the exact failure
    we just spent an evening on."""
    import re

    from app.providers.autoremote import NONCE_PATTERN

    for _ in range(20):
        req = new_request(db)
        assert re.match(NONCE_PATTERN, req.nonce), req.nonce


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

def test_scrubber_catches_the_percent_encoded_key(monkeypatch):
    """THE leak that actually happened. The key travels in a form-encoded body, so
    a scrubber that only knows the literal passes `key%3Dej3j...` straight through.
    On 2026-07-21 that put the real key in a transcript. Scrub every encoding it
    can appear in, and assert it — this is not something to leave to care."""
    from urllib.parse import quote, quote_plus

    from app.providers.autoremote import _scrub

    secret = "ej3jRj4Ss_c:APA91bG-xY_z/test+value"
    monkeypatch.setattr(settings, "autoremote_key", secret)

    for form in (secret, quote(secret, safe=""), quote_plus(secret)):
        body = f"key={form}&message=jarvis_locreq"
        cleaned = _scrub(body)
        assert secret not in cleaned
        assert form not in cleaned
        assert "***" in cleaned


def test_key_never_reaches_the_relay_error(db, monkeypatch, caplog):
    """`relay_error` is stored in the database AND rendered on the status page,
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
        ok, err = autoremote.send("abc")

    assert ok is False
    assert secret not in (err or "")
    assert secret not in caplog.text

    # ...and the same failure travelling the real path into the database. Restore
    # the genuine request_location so this exercises `send` rather than the stub.
    monkeypatch.setattr("app.providers.autoremote.request_location",
                        lambda nonce: autoremote.send(nonce))
    req = new_request(db)
    assert req.relay_accepted is False
    assert req.relay_error and secret not in req.relay_error


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
