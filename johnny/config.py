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

    # Width budget (in `ch` units) for table cells that opt into the
    # `.truncate` CSS class. Beyond this, the cell shows an ellipsis
    # and the full value is available via the cell's title attribute.
    # Read by johnny-web at request time via a context processor.
    johnny_truncate_chars: int = 15

    # Threshold for the stale-RUNNING detector. A Playbook in RUNNING
    # state whose last_event_at is older than this is transitioned to
    # ABANDONED by the `johnny mark-abandoned` sweeper. Read by the
    # tasks sidecar; web/api don't reference it.
    johnny_playbook_stale_after_seconds: int = 3600


@lru_cache
def get_settings() -> Settings:
    return Settings()
