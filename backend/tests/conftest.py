"""Pytest fixtures — isolated SQLite DB + FastAPI TestClient, no external services.

Environment MUST be configured before any `app.*` import, because app.config
reads it at import time and app.database builds the engine from it.
"""

import os
import tempfile

# ── configure environment BEFORE importing the app ───────────────────────────
_DB_FD, _DB_PATH = tempfile.mkstemp(suffix=".db", prefix="jarvis_test_")
os.environ.update(
    ENVIRONMENT="development",
    DATABASE_URL=f"sqlite+pysqlite:///{_DB_PATH}",
    JWT_SECRET="test-secret",
    SEED_USERNAME="admin",
    SEED_PASSWORD="testpass",
    ANTHROPIC_API_KEY="test-key",
    ALLOWED_SENDERS="me@example.com",
    ALLOWED_NUMBERS="+15551230000",
    SMS_PROVIDER="stub",
    TWILIO_VALIDATE_SIGNATURE="false",
    ENABLE_TRADING="false",
    ENABLE_REFLECTOR="true",
    EMBEDDING_PROVIDER="local",
    EMBEDDING_DIM="64",
    USE_PGVECTOR="false",
)

import pytest  # noqa: E402

from app.database import Base, SessionLocal, engine  # noqa: E402
import app.models  # noqa: E402,F401  (register tables)


@pytest.fixture(autouse=True)
def _fresh_db():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)


@pytest.fixture
def db():
    s = SessionLocal()
    try:
        yield s
    finally:
        s.close()


@pytest.fixture
def client():
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app) as c:
        yield c


@pytest.fixture
def auth_headers(client):
    r = client.post("/api/auth/login", data={"username": "admin", "password": "testpass"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


@pytest.fixture
def stub_sms():
    """Reset the SMS provider to a fresh stub and return it."""
    from app.providers.sms import StubProvider, set_sms_provider
    p = StubProvider()
    set_sms_provider(p)
    return p
