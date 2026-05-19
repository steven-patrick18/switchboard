from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", "../../.env"), env_file_encoding="utf-8", extra="ignore"
    )

    environment: str = "development"
    app_secret_key: str = "change-me-generate-a-random-32-byte-string"

    database_url: str = "postgresql://switchboard:switchboard@localhost:5432/switchboard"
    redis_url: str = "redis://localhost:6379/0"

    anthropic_api_key: str = ""
    # Default model for agents. Per-agent specs may override. Opus 4.7 is the
    # capable default; the brief's Haiku/Sonnet cost tiers are a deliberate
    # per-agent config choice, not a silent downgrade.
    agent_model: str = "claude-opus-4-7"
    agent_effort: str = "high"
    agent_max_tokens: int = 16000
    agent_max_iterations: int = 12
    # Hard cap on tasks run per bulk sweep — bounds API spend.
    bulk_run_max: int = 10

    # Browser origins allowed to call the API (comma-separated).
    cors_origins: str = "http://localhost:3000,http://127.0.0.1:3000"

    # Portal-integration backend. 'demo' uses the in-process simulator
    # (deterministic, no external I/O, safe for dev/CI). 'playwright' is
    # the real-portal backend and is wired separately when ready.
    portal_integration_backend: str = "demo"

    # Dedicated key for credential-vault encryption. Falls back to
    # app_secret_key if unset; back with a real KMS/Vault in production.
    credential_enc_key: str = ""

    # Content-addressed document storage root (local disk in dev/single-box;
    # swap for S3 in prod by replacing app.storage). Path is resolved on
    # first write so test fixtures can override it before any I/O.
    documents_dir: str = ".documents"

    # Best-effort SMTP notifications. All empty → email is disabled and
    # the platform never blocks on delivery. Pending-approval emails go
    # to the client's owning operator; the in-app badge stays the source
    # of truth either way.
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = ""
    smtp_use_tls: bool = True
    # Public URL used in email bodies (e.g. https://switchboard.example.com).
    # Empty falls back to relative paths.
    app_base_url: str = ""

    # Auth
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 60 * 24

    @property
    def async_database_url(self) -> str:
        # Normalize a plain Postgres URL to the asyncpg driver.
        if self.database_url.startswith("postgresql+asyncpg://"):
            return self.database_url
        if self.database_url.startswith("postgresql://"):
            return self.database_url.replace("postgresql://", "postgresql+asyncpg://", 1)
        return self.database_url

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
