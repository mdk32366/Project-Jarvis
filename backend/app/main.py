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


def safe_static_file(static_root: str, full_path: str) -> str | None:
    """Return the real file for `full_path` under `static_root`, or None.

    SECURITY (audit H1): the SPA catch-all serves arbitrary path segments. Without
    containment, a non-normalizing request like `/..%2fapp%2fconfig.py` resolves
    through os.path.join to a real backend source file and FileResponse would
    serve it. realpath + commonpath is the same protection StaticFiles gives
    /assets; this hand-rolled catch-all must not skip it. Returns None for an
    empty path, an escaping path, or a non-file so the caller falls back to the
    SPA index.
    """
    if not full_path:
        return None
    root = os.path.realpath(static_root)
    candidate = os.path.realpath(os.path.join(root, full_path))
    if candidate != root and os.path.commonpath([candidate, root]) != root:
        return None
    return candidate if os.path.isfile(candidate) else None


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


def _seed_agents() -> None:
    """Seed the default specialist roster if the agent_configs table is empty."""
    from app.agents import seed_agents
    db = SessionLocal()
    try:
        seed_agents(db)
    except Exception as e:  # pragma: no cover
        logger.warning("agent seeding skipped: %s", e)
    finally:
        db.close()


def _seed_health_topology() -> None:
    """Seed + reconcile the component/remediation health inventory (TDD §4)."""
    from app.health import seed_health_topology
    db = SessionLocal()
    try:
        seed_health_topology(db)
    except Exception as e:  # pragma: no cover
        logger.warning("health topology seeding skipped: %s", e)
    finally:
        db.close()


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting %s (%s)", settings.app_name, settings.environment)
    # In dev this bootstraps tables; in prod prefer `alembic upgrade head`.
    Base.metadata.create_all(bind=engine)
    _seed_first_user()
    _seed_agents()
    _seed_health_topology()
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
<h2>How consent is obtained</h2>
<p>There is one opt-in path and it is user-initiated: the account owner — the sole
recipient, and the same individual who operates this service — sends a text message from
their own mobile phone to the assistant's number. That affirmative act of initiating
contact is the consent to receive automated replies. There is no public sign-up form, no
web opt-in, no purchased list, and no third-party lead source. No other person receives
messages from this service. Consent is not a condition of any purchase.</p>
<h2>No sharing of mobile information</h2>
<p><strong>No mobile information will be shared with third parties or affiliates for
marketing or promotional purposes.</strong></p>
<p><strong>All the above categories exclude text messaging originator opt-in data and
consent; this information will not be shared with any third parties.</strong></p>
<p>We do not sell, rent, or share mobile phone numbers, SMS opt-in, or consent information
with any third parties or affiliates for any purpose. Mobile information is used only to
operate the assistant for its sole user, the account owner.</p>
<h2>Message and data rates</h2>
<p>Message and data rates may apply. Message frequency varies.</p>
<h2>Opt-out and help</h2>
<p>Reply STOP at any time to stop receiving messages. Reply HELP for assistance.</p>
<h2>Contact</h2>
<p>Questions: {settings.compliance_email}</p>
<footer>Last updated: 2026-07-01</footer>
</body></html>"""

_TERMS_HTML = f"""<!doctype html><html><head><meta charset='utf-8'>
<title>JARVIS — Terms &amp; Conditions</title>{_POLICY_STYLE}</head><body>
<h1>JARVIS Assistant — SMS Terms &amp; Conditions</h1>
<h2>Program description</h2>
<p><strong>Sender:</strong> JARVIS Assistant, an automated personal assistant operated by
{settings.business_name} (sole proprietor).</p>
<p><strong>Recipient:</strong> The account owner only — a single individual. No other
person receives messages from this service.</p>
<p><strong>Purpose:</strong> The account owner texts the assistant a question or request;
the assistant replies. Every message sent is a direct automated reply to a message the
owner sent first. This is not marketing, not a mailing list, and not a promotional
program.</p>
<p>By texting the assistant number from their own phone, the owner opts in to receive
those replies. See the <a href="/">full program description</a>.</p>
<h2>Message frequency</h2>
<p>Message frequency varies based on your interactions with the assistant.</p>
<h2>Cost</h2>
<p>Message and data rates may apply, per your mobile carrier plan.</p>
<h2>Opt-out</h2>
<p>Reply STOP to cancel at any time. After you send STOP, we will send one confirmation
message and then stop sending messages. Reply HELP for help.</p>
<h2>Carrier disclaimer</h2>
<p>Carriers are not liable for delayed or undelivered messages.</p>
<h2>No third-party marketing</h2>
<p>This messaging program sends <strong>no marketing or promotional content of any
kind</strong>, and carries no third-party or affiliate content. Every message is an
automated reply to a message the account owner sent first.</p>
<h2>Privacy</h2>
<p>See our <a href="/privacy">Privacy Policy</a>. <strong>No mobile information will be
shared with third parties or affiliates for marketing or promotional purposes.</strong>
All the above categories exclude text messaging originator opt-in data and consent; this
information will not be shared with any third parties.</p>
<h2>Contact</h2>
<p>Questions: {settings.compliance_email}</p>
<footer>Last updated: 2026-07-01</footer>
</body></html>"""


_LANDING_HTML = f"""<!doctype html><html><head><meta charset='utf-8'>
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>JARVIS Assistant — Personal AI Assistant (SMS &amp; Voice)</title>
<meta name="description" content="JARVIS is a private personal AI assistant operated by
{settings.business_name}. It replies by SMS and voice to its sole user, the account owner.">
{_POLICY_STYLE}</head><body>

<h1>JARVIS Assistant</h1>
<p><strong>A private personal AI assistant operated by {settings.business_name}
(sole proprietor).</strong></p>

<h2>What this messaging program is</h2>
<p><strong>Who sends the messages:</strong> JARVIS Assistant, an automated personal
assistant operated by {settings.business_name}.</p>
<p><strong>Who receives the messages:</strong> Only the account owner — a single
individual, the operator of this service. There are no other recipients. This is not a
marketing program, not a mailing list, and no messages are ever sent to anyone else.</p>
<p><strong>Why the messages are sent:</strong> The account owner texts or calls the
assistant with a question or request (for example, asking for a stock price, a calendar
summary, or the status of their own computers). The assistant replies to that request.
Every message JARVIS sends is a direct, automated reply to a message the account owner
sent first.</p>
<p><strong>Message content:</strong> Conversational replies and status information
requested by the account owner. No marketing, no promotions, no offers, no third-party
content of any kind.</p>

<h2>How consent is obtained (opt-in)</h2>
<p>There is exactly <strong>one</strong> opt-in path, and it is user-initiated:</p>
<ol>
<li>The account owner — the same individual who owns and operates this service — sends a
text message from their own personal mobile phone to the assistant's number.</li>
<li>That affirmative act of initiating contact <em>is</em> the consent. The owner is
consenting to receive automated replies to their own messages.</li>
<li>The assistant then replies to that message.</li>
</ol>
<p>There is <strong>no public sign-up form, no web opt-in, no purchased list, no
third-party lead source, and no other person who can be messaged.</strong> The account
owner's number is the only number on file, and it was entered by the owner into their own
system configuration. Consent is not a condition of any purchase or service.</p>

<h3>Opt-in disclosure</h3>
<blockquote style="border-left:3px solid #ccc;padding-left:1em;margin-left:0">
<p>JARVIS Personal Assistant: You have opted in to receive automated text replies from
your personal AI assistant by texting this number. Message frequency varies.
<strong>Message and data rates may apply.</strong> Reply STOP to unsubscribe, HELP for
help. See our <a href="/terms">Terms &amp; Conditions</a> and
<a href="/privacy">Privacy Policy</a>.</p>
</blockquote>

<h2>Message frequency</h2>
<p>Message frequency varies and depends entirely on how often the account owner chooses to
message the assistant. Typically a few messages per week. JARVIS does not initiate
unsolicited messages.</p>

<h2>Cost</h2>
<p><strong>Message and data rates may apply</strong>, according to the account owner's
mobile carrier plan.</p>

<h2>Opt-out and help</h2>
<p>Reply <strong>STOP</strong> at any time to stop receiving messages. One confirmation
message will be sent, after which no further messages will be sent. Reply
<strong>START</strong> to resubscribe. Reply <strong>HELP</strong> for assistance, or
email <a href="mailto:{settings.compliance_email}">{settings.compliance_email}</a>.</p>

<h2>Mobile information is never shared</h2>
<p><strong>No mobile information will be shared with third parties or affiliates for
marketing or promotional purposes.</strong> All the above categories exclude text
messaging originator opt-in data and consent; this information will not be shared with any
third parties.</p>

<h2>Sample messages</h2>
<ul>
<li>"JARVIS: AAPL last traded at $210.35. Want anything else?"</li>
<li>"JARVIS: Noted — I'll remind you about the 9am call tomorrow."</li>
<li>"JARVIS: All three hosts are online. Full details emailed to you."</li>
</ul>

<h2>Legal</h2>
<p><a href="/privacy">Privacy Policy</a> &middot;
<a href="/terms">Terms &amp; Conditions</a></p>

<h2>Contact</h2>
<p>{settings.business_name}<br>
<a href="mailto:{settings.compliance_email}">{settings.compliance_email}</a></p>

<footer>
<p>JARVIS Assistant is a private, single-user personal assistant. It is not a commercial
messaging service and does not send marketing messages.</p>
<p>Last updated: 2026-07-11 &middot; <a href="/login">Owner sign-in</a></p>
</footer>
</body></html>"""


# NOTE ON ROUTE ORDER: the SPA catch-all below is @app.get("/{full_path:path}"),
# which would otherwise swallow "/" and serve the React login screen. FastAPI
# matches in definition order, so "/" MUST be declared here, before the mount.
#
# This page exists because Twilio/carrier reviewers visit the root domain to
# verify the messaging program. A bare SPA login screen fails review (errors
# 30919 "site lacks business/use-case info" and 30921 "site requires
# authentication"), which is what sank the last submission.
@app.get("/", include_in_schema=False)
def landing():
    return HTMLResponse(_LANDING_HTML)


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

    _static_root = os.path.realpath(_static_dir)

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa_fallback(full_path: str):
        if full_path.startswith("api/"):
            return JSONResponse({"detail": "Not Found"}, status_code=404)
        # "/" is the public compliance landing page (declared above); the SPA
        # lives at /app and its client-side routes below it.
        safe = safe_static_file(_static_root, full_path)
        if safe is not None:
            return FileResponse(safe)
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
