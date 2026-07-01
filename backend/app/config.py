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

    # ── Google Calendar (read-only, service account) ─────────────────────────
    google_service_account_json: str = ""   # the SA key JSON (paste as a Fly secret)
    google_calendar_id: str = "primary"     # or your calendar's ID/email
    calendar_timezone: str = "America/Los_Angeles"

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
