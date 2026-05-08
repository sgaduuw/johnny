"""Custom SQLAlchemy types.

UtcDateTime preserves the contract "all datetimes are tz-aware UTC"
across SQLite (which stores naive timestamps) and Postgres (which
stores tz-aware natively). Without this, SQLite round-trips strip
the tzinfo silently and equality checks against the original input
fail in subtle ways.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import DateTime, Dialect, TypeDecorator


class UtcDateTime(TypeDecorator[datetime]):
    """Tz-aware UTC datetime, dialect-portable.

    Bind: rejects naive datetimes (they're ambiguous), normalizes any
    tz-aware datetime to UTC.
    Result: attaches UTC tzinfo to whatever the DB returned, so callers
    always see a tz-aware datetime regardless of dialect.
    """

    impl = DateTime
    cache_ok = True

    def __init__(self) -> None:
        super().__init__(timezone=True)

    def process_bind_param(
        self, value: datetime | None, dialect: Dialect
    ) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError(
                f"naive datetime not allowed: {value!r}; UTC tz-aware required"
            )
        return value.astimezone(timezone.utc)

    def process_result_value(
        self, value: datetime | None, dialect: Dialect
    ) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
