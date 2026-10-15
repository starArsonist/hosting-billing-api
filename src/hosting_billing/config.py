"""Runtime configuration, read from environment variables / .env."""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # SQLite works out of the box; use postgresql+asyncpg://... in production.
    database_url: str = "sqlite+aiosqlite:///./hosting_billing.db"
    cors_origins: str = "http://localhost:3000,http://localhost:5173"
    # When set, every endpoint except /health requires the header ``X-API-Key``.
    api_key: str | None = None
    # Create tables on startup (handy for SQLite demos). Use Alembic migrations for Postgres.
    auto_create_tables: bool = False

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
