"""Engine factory: SQLite PRAGMAs applied on every connect.

For SQLite: foreign_keys=ON (enforce FK constraints), journal_mode=WAL
(readers don't block writers), synchronous=NORMAL, busy_timeout=5000
(rides out brief contention without surfacing transient lock errors).
WAL is a no-op on `:memory:` databases — those stay in MEMORY journal
mode regardless. PRAGMAs land at connect time so every checked-out
connection has them, including the ones alembic uses.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import event
from sqlalchemy.engine import Engine, create_engine


def make_engine(url: str, **kwargs: Any) -> Engine:
    engine = create_engine(url, **kwargs)
    if engine.dialect.name == "sqlite":
        event.listen(engine, "connect", _set_sqlite_pragmas)
    return engine


def _set_sqlite_pragmas(dbapi_connection: Any, _connection_record: Any) -> None:
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.execute("PRAGMA busy_timeout=5000")
    cursor.close()
