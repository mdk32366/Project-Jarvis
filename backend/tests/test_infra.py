"""Offline tests for the infra (Fly fleet) handler — no network.

Health/run-rate: monkeypatch ``infra._list_machines`` to return sample machines.
Credit balance: monkeypatch ``httpx.Client`` with a fake that returns canned
GraphQL JSON. Everything must degrade to a clear string, never raise.
"""

from app.config import settings
from app.handlers import infra
from app.handlers.base import Context


def _ctx(db):
    return Context(db=db, channel="admin", actor="admin", thread_key="infra")


def _machine(state="started", cpu_kind="shared", cpus=1, memory_mb=256):
    return {"state": state, "config": {"guest": {"cpu_kind": cpu_kind, "cpus": cpus, "memory_mb": memory_mb}}}


class _FakeResp:
    def __init__(self, payload):
        self._payload = payload
        self.status_code = 200
    def json(self):
        return self._payload
    def raise_for_status(self):
        pass


class _FakeClient:
    """Stands in for httpx.Client(); returns a fixed GraphQL payload on post."""
    def __init__(self, payload):
        self._payload = payload
    def __call__(self, *a, **k):
        return self
    def __enter__(self):
        return self
    def __exit__(self, *a):
        return False
    def request(self, method, url, **kw):
        # GraphQL POST returns the payload; machine GETs return [] (patched in tests).
        return _FakeResp(self._payload if method.upper() == "POST" else [])
    def post(self, url, **kw):
        return _FakeResp(self._payload)
    def get(self, url, **kw):
        return _FakeResp([])


# ── configuration gating ──────────────────────────────────────────────────────

def test_health_unconfigured(db, monkeypatch):
    monkeypatch.setattr(settings, "fly_api_token_read", "")
    out = infra._fleet_health({}, _ctx(db))
    assert out.startswith("[infra not configured]")


def test_spend_unconfigured(db, monkeypatch):
    monkeypatch.setattr(settings, "fly_api_token_read", "")
    out = infra._fleet_spend({}, _ctx(db))
    assert out.startswith("[infra not configured]")


def test_health_no_apps(db, monkeypatch):
    monkeypatch.setattr(settings, "fly_api_token_read", "tok")
    monkeypatch.setattr(settings, "watched_fly_apps", "")
    out = infra._fleet_health({}, _ctx(db))
    assert "No apps to watch" in out


# ── health parsing ─────────────────────────────────────────────────────────────

def test_health_all_started_is_ok(db, monkeypatch):
    monkeypatch.setattr(settings, "fly_api_token_read", "tok")
    monkeypatch.setattr(settings, "watched_fly_apps", "jarvis-mdk")
    monkeypatch.setattr(infra, "_list_machines", lambda c, app: [_machine(), _machine()])
    out = infra._fleet_health({}, _ctx(db))
    assert "jarvis-mdk: OK" in out and "2 machine(s)" in out


def test_health_flags_degraded(db, monkeypatch):
    monkeypatch.setattr(settings, "fly_api_token_read", "tok")
    monkeypatch.setattr(settings, "watched_fly_apps", "app1")
    monkeypatch.setattr(infra, "_list_machines", lambda c, app: [_machine("started"), _machine("stopped")])
    out = infra._fleet_health({}, _ctx(db))
    assert "DEGRADED" in out and "1 started" in out and "1 stopped" in out


def test_health_per_app_error_isolated(db, monkeypatch):
    monkeypatch.setattr(settings, "fly_api_token_read", "tok")
    monkeypatch.setattr(settings, "watched_fly_apps", "good,bad")
    def lister(c, app):
        if app == "bad":
            raise RuntimeError("boom")
        return [_machine()]
    monkeypatch.setattr(infra, "_list_machines", lister)
    out = infra._fleet_health({}, _ctx(db))
    assert "good: OK" in out and "bad: error" in out  # one bad app can't sink the rest


# ── spend parsing ────────────────────────────────────────────────────────────

def test_spend_reports_credit_and_runrate(db, monkeypatch):
    monkeypatch.setattr(settings, "fly_api_token_read", "tok")
    monkeypatch.setattr(settings, "watched_fly_apps", "jarvis-mdk")
    payload = {"data": {"personalOrganization": {"name": "personal", "creditBalance": 500,
                                                 "creditBalanceFormatted": "$5.00"}}}
    monkeypatch.setattr("httpx.Client", _FakeClient(payload))
    # 2 running shared-cpu-1x@256 machines -> ~$1.94 each
    monkeypatch.setattr(infra, "_list_machines", lambda c, app: [_machine(), _machine()])
    out = infra._fleet_spend({}, _ctx(db))
    assert "$5.00" in out and "personal" in out
    assert "run-rate" in out and "2 running machine(s)" in out
    assert "estimate" in out.lower()


def test_spend_graphql_errors_degrade(db, monkeypatch):
    monkeypatch.setattr(settings, "fly_api_token_read", "tok")
    monkeypatch.setattr(settings, "watched_fly_apps", "")
    monkeypatch.setattr("httpx.Client", _FakeClient({"errors": [{"message": "nope"}]}))
    out = infra._fleet_spend({}, _ctx(db))
    assert "credit balance unavailable" in out


# ── cost estimate unit ─────────────────────────────────────────────────────────

def test_estimate_cost_presets_and_extra_ram():
    assert infra._estimate_machine_cost("shared", 1, 256) == 1.94  # base preset
    # 1x preset (256 incl) with 1024MB -> +0.75GB * $5 = +3.75
    assert round(infra._estimate_machine_cost("shared", 1, 1024), 2) == 5.69
    assert infra._estimate_machine_cost("performance", 2, 4096) is None  # unpriced


def test_machine_size_extraction():
    assert infra._machine_size(_machine("started", "shared", 2, 512)) == ("shared", 2, 512)
    assert infra._machine_size({}) == ("shared", 1, 256)  # defaults


# ── diagnostic endpoint ────────────────────────────────────────────────────────

def test_infra_health_endpoint(client, auth_headers, monkeypatch):
    monkeypatch.setattr(settings, "fly_api_token_read", "")
    r = client.get("/api/infra/health", headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    assert "health" in body and "spend" in body
    assert body["health"].startswith("[infra not configured]")


def test_infra_health_endpoint_requires_auth(client):
    r = client.get("/api/infra/health")
    assert r.status_code == 401


# ── briefing integration: section omitted when unconfigured ─────────────────────

def test_briefing_omits_hosted_apps_when_unconfigured(db, monkeypatch):
    monkeypatch.setattr(settings, "fly_api_token_read", "")
    from app import briefing
    ctx_text = briefing.gather_context(db)
    assert "## Hosted apps" not in ctx_text


def test_briefing_includes_hosted_apps_when_configured(db, monkeypatch):
    monkeypatch.setattr(settings, "fly_api_token_read", "tok")
    monkeypatch.setattr(settings, "watched_fly_apps", "jarvis-mdk")
    monkeypatch.setattr(infra, "_list_machines", lambda c, app: [_machine()])
    payload = {"data": {"personalOrganization": {"name": "personal", "creditBalanceFormatted": "$5.00"}}}
    monkeypatch.setattr("httpx.Client", _FakeClient(payload))
    from app import briefing
    text = briefing.gather_context(db)
    assert "## Hosted apps" in text and "jarvis-mdk: OK" in text



def test_auth_variants_both_schemes(monkeypatch):
    # bare token -> try Bearer first, then FlyV1 fallback
    monkeypatch.setattr(settings, "fly_api_token_read", "fm2_bare")
    assert infra._auth_variants() == ["Bearer fm2_bare", "FlyV1 fm2_bare"]
    # FlyV1-prefixed -> try verbatim first, then Bearer of the core
    monkeypatch.setattr(settings, "fly_api_token_read", "FlyV1 fm2_org")
    assert infra._auth_variants() == ["FlyV1 fm2_org", "Bearer fm2_org"]
    # stray "Bearer " prefix + whitespace tolerated
    monkeypatch.setattr(settings, "fly_api_token_read", "  Bearer FlyV1 fm2_x  ")
    assert infra._auth_variants() == ["FlyV1 fm2_x", "Bearer fm2_x"]


class _SchemeClient:
    """Rejects (401) every scheme except the one it declares acceptable."""
    def __init__(self, ok_auth):
        self.ok_auth = ok_auth
        self.tried = []
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def request(self, method, url, headers=None, **kw):
        auth = (headers or {}).get("Authorization")
        self.tried.append(auth)
        code = 200 if auth == self.ok_auth else 401
        class R:
            status_code = code
            def json(self): return []
            def raise_for_status(self): pass
        return R()


def test_request_falls_back_to_second_scheme(monkeypatch):
    monkeypatch.setattr(settings, "fly_api_token_read", "fm2_bare")
    c = _SchemeClient(ok_auth="FlyV1 fm2_bare")  # only FlyV1 works
    r = infra._request(c, "GET", "https://x/y")
    assert r.status_code == 200
    assert c.tried == ["Bearer fm2_bare", "FlyV1 fm2_bare"]  # tried Bearer, then FlyV1
