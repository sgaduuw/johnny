"""Application settings, loaded from environment / .env via pydantic-settings.

Settings are read at import time of `get_settings()`, cached for the
process lifetime. Tests override via `app.dependency_overrides[get_settings]`
or by clearing the cache after monkey-patching env vars.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    database_url: str = "sqlite:///./johnny.db"

    # Required only for johnny-api (the callback ingest tier). The web
    # tier and the tasks sidecar can run without it. The api auth
    # dependency raises 503 at request time if it isn't configured, so
    # a misconfigured api container fails loudly on first call rather
    # than silently accepting requests.
    johnny_api_token: str | None = None


@lru_cache
def get_settings() -> Settings:
    return Settings()
