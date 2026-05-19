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


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
