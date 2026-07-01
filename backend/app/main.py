"""
FastAPI application entrypoint.

Serves the JSON API under /api and the built React SPA (everything else),
so the whole app deploys as a single Fly.io service on one origin.
"""

import logging
import os
import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.auth import hash_password
from app.config import settings
from app.database import Base, SessionLocal, engine
from app.models import User
from app.routes import router

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)


def _seed_first_user() -> None:
    """Create the seed admin user if the users table is empty."""
    db = SessionLocal()
    try:
        if db.query(User).count() == 0:
            db.add(
                User(
                    username=settings.seed_username,
                    hashed_password=hash_password(settings.seed_password),
                )
            )
            db.commit()
            logger.info("Seeded first user: %s", settings.seed_username)
    finally:
        db.close()


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting %s (%s)", settings.app_name, settings.environment)
    # In dev this bootstraps tables; in prod prefer `alembic upgrade head`.
    Base.metadata.create_all(bind=engine)
    _seed_first_user()
    try:
        from app.database import SessionLocal as _SL
        from app import vectorstore
        _db = _SL()
        try:
            vectorstore.ensure_ready(_db)  # CREATE EXTENSION vector + embedding table (Postgres)
        finally:
            _db.close()
    except Exception as e:  # pragma: no cover
        logger.warning("vectorstore init skipped: %s", e)
    yield
    logger.info("Shutting down")


app = FastAPI(title=settings.app_name, lifespan=lifespan)

# CORS only matters for local dev (Vite dev server on a different port).
# In production the SPA is same-origin, so the list is empty there.
if not settings.is_production:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

app.include_router(router)


# ── Compliance pages (required by SMS carriers for A2P 10DLC registration) ────
from fastapi.responses import HTMLResponse  # noqa: E402

_POLICY_STYLE = (
    "<style>body{font-family:system-ui,Arial,sans-serif;max-width:720px;margin:40px auto;"
    "padding:0 20px;line-height:1.55;color:#222}h1{font-size:1.6rem}h2{font-size:1.1rem;margin-top:1.6em}"
    "footer{margin-top:2em;color:#666;font-size:.9rem}</style>"
)

_PRIVACY_HTML = f"""<!doctype html><html><head><meta charset='utf-8'>
<title>JARVIS — Privacy Policy</title>{_POLICY_STYLE}</head><body>
<h1>JARVIS Assistant — Privacy Policy</h1>
<p>JARVIS is a private, personal assistant operated by its owner as a sole proprietor.
It is used solely by the account owner. This policy describes how information sent to
the service by SMS is handled.</p>
<h2>Information we collect</h2>
<p>When you text the assistant, we process your mobile phone number and the content of
your messages only to generate and return a response to you.</p>
<h2>How we use it</h2>
<p>Your phone number and messages are used exclusively to operate the assistant for the
account owner. Message frequency varies based on your interactions.</p>
<h2>No sharing of mobile information</h2>
<p><strong>We do not sell, rent, or share mobile phone numbers, SMS opt-in, or consent
information with any third parties or affiliates for marketing or promotional purposes.</strong>
Mobile information is used only to provide the messaging service.</p>
<h2>Message and data rates</h2>
<p>Message and data rates may apply. Message frequency varies.</p>
<h2>Opt-out and help</h2>
<p>Reply STOP at any time to stop receiving messages. Reply HELP for assistance.</p>
<h2>Contact</h2>
<p>Questions: mdk32366@gmail.com</p>
<footer>Last updated: 2026-07-01</footer>
</body></html>"""

_TERMS_HTML = f"""<!doctype html><html><head><meta charset='utf-8'>
<title>JARVIS — Terms &amp; Conditions</title>{_POLICY_STYLE}</head><body>
<h1>JARVIS Assistant — SMS Terms &amp; Conditions</h1>
<h2>Program description</h2>
<p>JARVIS is a private personal-assistant messaging service used by its account owner.
By texting the assistant number, the owner opts in to receive SMS replies from the service.</p>
<h2>Message frequency</h2>
<p>Message frequency varies based on your interactions with the assistant.</p>
<h2>Cost</h2>
<p>Message and data rates may apply, per your mobile carrier plan.</p>
<h2>Opt-out</h2>
<p>Reply STOP to cancel at any time. After you send STOP, we will send one confirmation
message and then stop sending messages. Reply HELP for help.</p>
<h2>Carrier disclaimer</h2>
<p>Carriers are not liable for delayed or undelivered messages.</p>
<h2>Privacy</h2>
<p>See our <a href="/privacy">Privacy Policy</a>. We do not share mobile numbers or SMS
consent with third parties for marketing.</p>
<h2>Contact</h2>
<p>Questions: mdk32366@gmail.com</p>
<footer>Last updated: 2026-07-01</footer>
</body></html>"""


@app.get("/privacy", include_in_schema=False)
def privacy_policy():
    return HTMLResponse(_PRIVACY_HTML)


@app.get("/terms", include_in_schema=False)
def terms_and_conditions():
    return HTMLResponse(_TERMS_HTML)


# ── Serve the React SPA ──────────────────────────────────────────────────────
# StaticFiles handles real asset files; the catch-all returns index.html so
# client-side routes (React Router) deep-link correctly. API 404s stay JSON.
_static_dir = settings.static_dir
_index_file = os.path.join(_static_dir, "index.html")

if os.path.isdir(_static_dir):
    app.mount(
        "/assets",
        StaticFiles(directory=os.path.join(_static_dir, "assets")),
        name="assets",
    )

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa_fallback(full_path: str):
        if full_path.startswith("api/"):
            return JSONResponse({"detail": "Not Found"}, status_code=404)
        candidate = os.path.join(_static_dir, full_path)
        if full_path and os.path.isfile(candidate):
            return FileResponse(candidate)
        if os.path.isfile(_index_file):
            return FileResponse(_index_file)
        return JSONResponse({"detail": "Frontend not built"}, status_code=404)
else:
    logger.warning("Static dir '%s' not found — API-only mode.", _static_dir)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host=settings.api_host,
        port=settings.api_port,
        reload=not settings.is_production,
    )
