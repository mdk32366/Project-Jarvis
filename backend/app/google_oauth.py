"""Google OAuth — acting AS the owner, not as a robot.

WHY THIS EXISTS. The service account (see scheduling.py) works for Calendar
because a calendar can be *shared* with it. Google Contacts and Google Tasks have
no sharing mechanism at all: a service account gets its own empty contact list
and its own empty task list, and there is no scope, no setting, and no amount of
domain-wide-delegation fiddling that changes this for a consumer @gmail.com
account. The API simply does not offer that path.

So for anything that lives *inside* the owner's personal Google account, JARVIS
must act AS the owner. That means OAuth: a one-time consent, a long-lived refresh
token, and thereafter JARVIS mints access tokens as needed.

ADDITIVE, NOT A REPLACEMENT. The service account keeps doing Calendar. This layer
handles what it cannot reach. Both can coexist; nothing breaks if only one is
configured. If OAuth is later preferred for Calendar too, `calendar_service()`
here is ready — but that is a separate decision, not a prerequisite.

SETUP (once):
    1. Google Cloud Console -> APIs & Services -> Credentials
       -> Create OAuth client ID -> Desktop app
       Enable: People API, Tasks API (Calendar API is already on)
    2. Download the client JSON.
    3. Run:  python -m app.google_oauth --client-secrets ~/Downloads/client.json
       A browser opens. Consent. The script prints a refresh token.
    4. fly secrets set GOOGLE_OAUTH_CLIENT_ID=... \\
                       GOOGLE_OAUTH_CLIENT_SECRET=... \\
                       GOOGLE_OAUTH_REFRESH_TOKEN=...

The refresh token is long-lived but NOT immortal: it dies if you revoke access,
change your password, or leave it unused for six months. Everything here degrades
to a clear "reconnect Google" message rather than an exception.
"""

from __future__ import annotations

import logging

from app.config import settings

log = logging.getLogger(__name__)

# Request everything we might want up front — re-consenting later is friction,
# and an unused scope costs nothing.
#
# NOTE: adding new scopes here does NOT update a token that was minted without
# them. The next time Matt runs `python -m app.google_oauth` the consent screen
# will include the new scopes and the refreshed token will cover all of them.
# Until then, Docs/Sheets calls will fail with ACCESS_TOKEN_SCOPE_INSUFFICIENT;
# explain() converts that to a legible re-auth prompt (see below).
SCOPES = [
    "https://www.googleapis.com/auth/contacts.readonly",
    "https://www.googleapis.com/auth/tasks",
    "https://www.googleapis.com/auth/calendar",
    # TDD #13: Google Docs & Sheets creation
    "https://www.googleapis.com/auth/documents",
    "https://www.googleapis.com/auth/spreadsheets",
]

_TOKEN_URI = "https://oauth2.googleapis.com/token"


def is_configured() -> bool:
    return bool(
        settings.google_oauth_client_id
        and settings.google_oauth_client_secret
        and settings.google_oauth_refresh_token
    )


def credentials():
    """Build Credentials from the stored refresh token, or None if unconfigured.

    google-auth refreshes the access token automatically on first use, so there
    is no token cache to manage and nothing to expire mid-call.
    """
    if not is_configured():
        return None

    from google.oauth2.credentials import Credentials

    return Credentials(
        token=None,                       # forces an immediate refresh
        refresh_token=settings.google_oauth_refresh_token,
        client_id=settings.google_oauth_client_id,
        client_secret=settings.google_oauth_client_secret,
        token_uri=_TOKEN_URI,
        scopes=SCOPES,
    )


def _service(name: str, version: str):
    creds = credentials()
    if creds is None:
        return None
    from googleapiclient.discovery import build

    return build(name, version, credentials=creds, cache_discovery=False)


def people_service():
    """Google Contacts. Service accounts CANNOT reach a consumer contact list."""
    return _service("people", "v1")


def tasks_service():
    """Google Tasks. Same story — no service-account path for @gmail.com."""
    return _service("tasks", "v1")


def calendar_service():
    """Available, but Calendar currently goes through the service account
    (scheduling.py), which already works. Here for a future migration."""
    return _service("calendar", "v3")


def docs_service():
    """Google Docs — create and edit documents. Requires the documents scope.

    Returns None if OAuth is not configured. The caller must handle None and
    return a legible 'reconnect Google' message rather than letting it crash.
    """
    return _service("docs", "v1")


def sheets_service():
    """Google Sheets — create and edit spreadsheets. Requires the spreadsheets scope.

    Returns None if OAuth is not configured. Same contract as docs_service().
    """
    return _service("sheets", "v4")


NOT_CONNECTED = (
    "[Google not connected] JARVIS can't reach your Google contacts or tasks yet. "
    "That needs a one-time OAuth consent — a service account can't access a personal "
    "Google account's contacts or tasks at all. Run `python -m app.google_oauth` to "
    "connect."
)


# ── One-time token minting (run locally, never in prod) ──────────────────────
def _mint() -> int:
    """Do the consent dance and print a refresh token. Run this on your laptop."""
    import argparse
    import json

    ap = argparse.ArgumentParser(description="Mint a Google OAuth refresh token for JARVIS")
    ap.add_argument("--client-secrets", required=True,
                    help="Path to the OAuth client JSON from Google Cloud Console")
    ap.add_argument("--port", type=int, default=8765)
    args = ap.parse_args()

    from google_auth_oauthlib.flow import InstalledAppFlow

    flow = InstalledAppFlow.from_client_secrets_file(args.client_secrets, SCOPES)
    # access_type=offline + prompt=consent is what actually GETS you a refresh
    # token. Without prompt=consent, Google will happily return only an access
    # token on a repeat authorization, and you'll wonder where the refresh token
    # went. This is the single most common way to get this wrong.
    creds = flow.run_local_server(
        port=args.port, access_type="offline", prompt="consent",
        authorization_prompt_message="Opening a browser to connect JARVIS to Google...",
        success_message="Connected. You can close this tab and return to the terminal.",
    )

    with open(args.client_secrets) as f:
        client = json.load(f)
    inst = client.get("installed") or client.get("web") or {}

    if not creds.refresh_token:
        print("\nNO REFRESH TOKEN RETURNED.")
        print("Revoke JARVIS at https://myaccount.google.com/permissions and rerun.")
        return 1

    cid = inst.get("client_id", "?")
    csec = inst.get("client_secret", "?")

    print("\n" + "=" * 78)
    print("SUCCESS. Set these as Fly secrets, from the REPO ROOT (not backend/).")
    print("Do NOT commit them, and do not paste them into a chat.\n")
    # One line, no continuations: backslash is bash, backtick is PowerShell, and
    # getting it wrong means the shell tries to execute the token as a command.
    print(f'fly secrets set GOOGLE_OAUTH_CLIENT_ID="{cid}" '
          f'GOOGLE_OAUTH_CLIENT_SECRET="{csec}" '
          f'GOOGLE_OAUTH_REFRESH_TOKEN="{creds.refresh_token}"')
    print("\n" + "=" * 78)
    print("Then rotate nothing — but if these ever leak, reset the client secret at")
    print("https://console.cloud.google.com/apis/credentials and rerun this script.")
    return 0


if __name__ == "__main__":
    raise SystemExit(_mint())


# ── Error interpretation ────────────────────────────────────────────────────
def explain(err: Exception) -> str | None:
    """Turn an opaque Google 403 into something actionable, or None if we can't.

    These errors are USEFUL — Google tells you exactly what's wrong and gives you
    the link to fix it — but they arrive wrapped in an HttpError repr that nobody
    reads, buried in a job row nobody looks at. So JARVIS silently did nothing and
    the user had to go spelunking in Postgres to find out why.

    Real examples, both hit on the first live run:
      * "People API has not been used in project N before or it is disabled."
      * "Service accounts cannot invite attendees without Domain-Wide Delegation."

    Neither is a bug, and neither will fix itself on retry.
    """
    msg = str(err)

    if "SERVICE_DISABLED" in msg or "has not been used in project" in msg:
        api = ("People API" if "people.googleapis" in msg
               else "Google Tasks API" if "tasks.googleapis" in msg
               else "Google Docs API" if "docs.googleapis" in msg
               else "Google Sheets API" if "sheets.googleapis" in msg
               else "Google Calendar API" if "calendar" in msg
               else "a Google API")
        return (f"The {api} isn't enabled in your Google Cloud project. Enable it in the "
                f"console, wait a minute, and try again — nothing else is wrong.")

    if "forbiddenForServiceAccounts" in msg or "Domain-Wide Delegation" in msg:
        return ("A service account can't invite attendees on a personal Google account — "
                "Google forbids it, and re-sharing the calendar won't help. Connect Google "
                "via OAuth, or create the event without attendees.")

    if "invalid_grant" in msg or "Token has been expired or revoked" in msg:
        return ("The Google connection has expired or been revoked. Reconnect by running "
                "`python -m app.google_oauth` and updating the secrets.")

    if "insufficientPermissions" in msg or "ACCESS_TOKEN_SCOPE_INSUFFICIENT" in msg:
        return ("The Google connection is missing a required scope. Reconnect by running "
                "`python -m app.google_oauth` — the consent screen will ask for it.")

    return None


def is_permanent(err: Exception) -> bool:
    """Will retrying ever help? A disabled API or a revoked token will not fix
    itself, so burning three attempts on it just delays the honest answer."""
    msg = str(err)
    return any(k in msg for k in (
        "SERVICE_DISABLED", "has not been used in project",
        "forbiddenForServiceAccounts", "Domain-Wide Delegation",
        "invalid_grant", "Token has been expired or revoked",
        "insufficientPermissions", "ACCESS_TOKEN_SCOPE_INSUFFICIENT",
    ))
