from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict

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
    jarvis_model: str = "claude-sonnet-4-20250514"
    jarvis_router_model: str = "claude-haiku-4-5-20251001"
    max_tokens: int = 2048
    imap_host: str = "imap.gmail.com"
    imap_port: int = 993
    imap_folder: str = "INBOX"
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 587
    gmail_address: str = ""
    gmail_app_password: str = ""
    ingest_poll_seconds: int = 120
    allowed_senders: str = ""
    confirm_threshold_usd: float = 50.0
    alpaca_api_key: str = ""
    alpaca_secret_key: str = ""
    alpaca_paper: bool = True

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def allowed_sender_list(self) -> list[str]:
        return [s.strip().lower() for s in self.allowed_senders.split(",") if s.strip()]

    @property
    def is_production(self) -> bool:
        return self.environment.lower() == "production"

@lru_cache
def get_settings() -> Settings:
    return Settings()

settings = get_settings()
