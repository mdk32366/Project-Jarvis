from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import field_validator

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", case_sensitive=False, extra="ignore")
    app_name: str = "JARVIS"
    environment: str = "development"
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    log_level: str = "INFO"
    static_dir: str = "static"
    database_url: str = "postgresql+psycopg2://postgres:postgres@localhost:5432/app"
    jwt_secret: str = "dev-only-change-me"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 60 * 24
    seed_username: str = "admin"
    seed_password: str = "changeme"
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"
    anthropic_api_key: str = ""
    jarvis_model: str = "claude-sonnet-5"          # was claude-sonnet-4-20250514
    jarvis_router_model: str = "claude-haiku-4-5-20251001"
    max_tokens: int = 2048

    # ── Email channel ────────────────────────────────────────────────────────
    imap_host: str = "imap.gmail.com"
    imap_port: int = 993
    imap_folder: str = "INBOX"
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 587
    gmail_address: str = ""
    gmail_app_password: str = ""
    ingest_poll_seconds: int = 120
    allowed_senders: str = ""

    # ── SMS channel (Phase 1) ────────────────────────────────────────────────
    # Provider: "twilio" for real texting, "stub" for local/dev/tests (no account).
    sms_provider: str = "stub"
    twilio_account_sid: str = ""
    twilio_auth_token: str = ""
    twilio_from_number: str = ""            # your Twilio number, E.164 e.g. +14155550123
    # Validate the X-Twilio-Signature header on inbound webhooks. Keep True in prod.
    twilio_validate_signature: bool = True
    # Public URL Twilio posts to (used for signature validation). e.g.
    # https://jarvis-mdk.fly.dev/api/sms/inbound
    sms_public_url: str = ""
    # Whitelisted phone numbers allowed to command JARVIS (comma-separated, E.164).
    allowed_numbers: str = ""
    # Email a copy of every SMS reply to the owner (bidirectional: text in ->
    # text back + email copy). Owner defaults to the first ALLOWED_SENDERS entry.
    sms_email_copy: bool = True
    owner_email: str = ""

    # ── Public compliance pages (Twilio / carrier review) ────────────────────
    # Carrier reviewers visit the ROOT domain to verify the messaging program.
    # A bare SPA login screen fails review (30919 / 30921).
    # MUST match the business name on your Twilio Brand Registration exactly —
    # reviewers cross-reference the site against the registered brand. For a sole
    # proprietor this is normally your legal name, e.g. "Matthew Kelly".
    business_name: str = "JARVIS Assistant"
    # Do not use a personal address here — this is published publicly. Gmail
    # plus-addressing works (you+compliance@gmail.com); a real domain is better.
    compliance_email: str = "jarvismajorus+compliance@gmail.com"

    # ── Voice channel (Phase 1) ──────────────────────────────────────────────
    voice_enabled: bool = False
    voice_tts_voice: str = "Polly.Matthew-Neural"
    # Public base URL Twilio posts to, e.g. https://jarvis-mdk.fly.dev
    # Behind Fly's proxy str(request.url) can report http://, which will NOT
    # match the base string Twilio signed. Set this in prod.
    voice_public_url_base: str = ""

    # ── Action safety ────────────────────────────────────────────────────────
    confirm_threshold_usd: float = 50.0
    # Master switch for real-money trading. Kept OFF until the dashboard has
    # proper auth/security. When False, place_stock_order is a hard-disabled stub.
    enable_trading: bool = False

    # ── Finance (Alpaca) ─────────────────────────────────────────────────────
    alpaca_api_key: str = ""
    alpaca_secret_key: str = ""
    alpaca_paper: bool = True

    # ── Memory reflector (Phase 1) ───────────────────────────────────────────
    # Auto-extract durable facts from conversations after each exchange.
    enable_reflector: bool = True
    # Embedding backend: "voyage" (needs VOYAGE_API_KEY) or "local" (offline hash).
    embedding_provider: str = "local"
    voyage_api_key: str = ""
    voyage_model: str = "voyage-3"
    embedding_dim: int = 1024
    # Use a pgvector-backed store when on Postgres AND the extension is available.
    # Default OFF: stock Fly Postgres has no pgvector; the portable JSON + in-Python
    # cosine store works fine at personal scale. Flip on only if you install pgvector.
    use_pgvector: bool = False
    # Cosine similarity at/above which two facts are considered duplicates.
    memory_dedup_threshold: float = 0.92
    # How many semantically-relevant facts to inject into the system preamble.
    memory_recall_k: int = 8

    # ── Morning briefing (Phase 2) ───────────────────────────────────────────
    briefing_enabled: bool = False          # turn on to send the daily email
    briefing_hour: int = 6                  # local hour (24h) to send
    briefing_minute: int = 30

    # ── Google Calendar (read-only, service account) ─────────────────────────
    google_service_account_json: str = ""   # the SA key JSON (paste as a Fly secret)
    google_calendar_id: str = "primary"     # or your calendar's ID/email
    calendar_timezone: str = "America/Los_Angeles"

    # ── Outbound calling (JARVIS rings the owner) ────────────────────────────
    outbound_calls_enabled: bool = False
    # She does not ring at 3am. Briefings and alerts respect this; a callback the
    # user explicitly ASKED for does not — they asked.
    quiet_hours_start: int = 21        # 9pm
    quiet_hours_end: int = 7           # 7am
    # Backstop against a bug that dials in a loop. The person on the other end
    # cannot easily make that stop.
    max_outbound_calls_per_hour: int = 6
    # Morning brief as a CALL instead of an email. Uses briefing_hour/minute.
    briefing_by_phone: bool = False

    # ── Maps (traffic + places) ──────────────────────────────────────────────
    # Same Google Cloud project. Enable: Directions API, Places API.
    google_maps_api_key: str = ""

    # ── Tailscale ────────────────────────────────────────────────────────────
    tailscale_api_key: str = ""
    tailscale_tailnet: str = ""        # e.g. "you@gmail.com" or "your-org.github"

    # ── Watches ──────────────────────────────────────────────────────────────
    # Floor on how often a recurring watch may ring, however often the condition
    # is true. A watch that calls every five minutes is worse than no watch — the
    # user will disable the whole feature.
    watch_min_interval_minutes: int = 60

    # ── Google OAuth (acts AS the owner) ─────────────────────────────────────
    # REQUIRED for Contacts and Tasks. A service account cannot reach a consumer
    # Google account's contacts or task list at all — no scope, no setting, and
    # no delegation makes that work for @gmail.com. Only OAuth can.
    #
    # Additive: the service account keeps handling Calendar. Mint a token with
    #     python -m app.google_oauth --client-secrets client.json
    google_oauth_client_id: str = ""
    google_oauth_client_secret: str = ""
    google_oauth_refresh_token: str = ""

    # ── Owner identity ───────────────────────────────────────────────────────
    # Facts about the owner that never change. JARVIS was emailing the owner a
    # transcript after every call while still ASKING for their email address —
    # the address lived in the job queue, where the model couldn't see it. The
    # `whoami` tool exposes these as knowledge.
    owner_name: str = ""
    owner_phone: str = ""
    owner_home_airport: str = ""          # e.g. SEA — so "find me a flight to SFO" just works
    owner_home_address: str = ""          # origin for "how's traffic to work"
    owner_work_address: str = ""          # the default destination
    owner_frequent_flyer: str = ""        # e.g. "Alaska MP 12345678, Delta SM 987654"

    # Vehicles, vessels, and anything else with a number you'd otherwise have to
    # go look up. The rule for this whole block: if it never changes and you've
    # ever had to hunt for it, it belongs here.
    owner_vehicle: str = ""               # "2021 Ford F-150, plate ABC1234"
    owner_boat: str = ""                  # "Serenity, hull WN1234AB, Skyline Marina, Anacortes"
    owner_notes: str = ""                 # free-form: anything else JARVIS should just KNOW

    # Named places, so you can say "how long to work" instead of reciting an
    # address. Format: "name=address; name=address"
    #   "work=10777 Willows Rd NE, Redmond WA; boat=Skyline Marina, Anacortes WA"
    owner_places: str = ""

    # ── Ideas repo (separate private GitHub repo) ────────────────────────────
    # PAT with `repo` scope. Ideas are captured to the DB first and committed
    # out-of-band, so a bad token can never lose a thought.
    github_token: str = ""
    ideas_repo: str = ""                    # "owner/jarvis-ideas"
    ideas_branch: str = "main"

    # ── Travel ───────────────────────────────────────────────────────────────
    # Trips are learned from confirmation emails — no airline credentials, no
    # scraping. These keys are for flight SEARCH only (research, not booking).
    duffel_api_key: str = ""
    amadeus_api_key: str = ""

    # ── Infra / hosted-app monitoring (Phase 2) ──────────────────────────────
    # Fly API token (read-only or deploy token) for fleet health + credit balance.
    fly_api_token_read: str = ""
    # Comma-separated Fly app names to report on in the briefing / infra tools.
    watched_fly_apps: str = "jarvis-mdk"
    # Per-app expected RUNNING machine count, e.g.
    # "jarvis-mdk:3,ffis-scrubber:1". Health flags DEGRADED only when started
    # drops below this. Apps not listed default to 1 (at least one must be up).
    fleet_expected: str = ""

    # ── Job queue / worker (Phase 1) ─────────────────────────────────────────
    worker_poll_seconds: int = 5
    job_max_attempts: int = 3

    @field_validator("database_url")
    @classmethod
    def _normalize_db_url(cls, v: str) -> str:
        if v.startswith("postgres://"):
            return v.replace("postgres://", "postgresql+psycopg2://", 1)
        if v.startswith("postgresql://"):
            return v.replace("postgresql://", "postgresql+psycopg2://", 1)
        return v

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def allowed_sender_list(self) -> list[str]:
        return [s.strip().lower() for s in self.allowed_senders.split(",") if s.strip()]

    @property
    def allowed_number_list(self) -> list[str]:
        return [normalize_number(s) for s in self.allowed_numbers.split(",") if s.strip()]

    @property
    def fleet_expected_map(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for pair in self.fleet_expected.split(","):
            if ":" in pair:
                name, _, num = pair.partition(":")
                name = name.strip()
                try:
                    if name:
                        out[name] = int(num.strip())
                except ValueError:
                    continue
        return out

    @property
    def watched_fly_app_list(self) -> list[str]:
        return [a.strip() for a in self.watched_fly_apps.split(",") if a.strip()]

    @property
    def owner_email_resolved(self) -> str:
        return self.owner_email or (self.allowed_sender_list[0] if self.allowed_sender_list else "")

    @property
    def is_production(self) -> bool:
        return self.environment.lower() == "production"

    @property
    def is_postgres(self) -> bool:
        return self.database_url.startswith("postgresql")


def normalize_number(raw: str) -> str:
    """Best-effort E.164 normalization for phone-number comparison.

    Strips spaces, dashes, parens; keeps a leading '+'. Not a full libphonenumber
    implementation — good enough for whitelist matching of your own numbers.
    """
    s = (raw or "").strip()
    if not s:
        return ""
    plus = s.startswith("+")
    digits = "".join(ch for ch in s if ch.isdigit())
    return ("+" + digits) if plus else digits


@lru_cache
def get_settings() -> Settings:
    return Settings()

settings = get_settings()
