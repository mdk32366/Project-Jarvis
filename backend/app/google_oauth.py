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
SCOPES = [
    "https://www.googleapis.com/auth/contacts.readonly",
    "https://www.googleapis.com/auth/tasks",
    "https://www.googleapis.com/auth/calendar",
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
