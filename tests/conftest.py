"""Shared pytest fixtures for johnny.

Every test gets a fresh in-memory SQLite engine with the full schema
applied via Base.metadata.create_all. The engine factory enables FK
enforcement, so tests catch FK violations the same way production
SQLite would.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterator
from uuid import uuid4

import pytest
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from johnny.persistence import Base, Playbook, make_engine


@pytest.fixture
def engine() -> Iterator[Engine]:
    # `:memory:` SQLite databases are per-connection. FastAPI's TestClient
    # runs sync routes in a worker thread, which would otherwise check out
    # a separate connection (and thus a separate empty DB). StaticPool +
    # check_same_thread=False reuses one connection across all threads.
    e = make_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(e)
    yield e
    e.dispose()


@pytest.fixture
def session(engine: Engine) -> Iterator[Session]:
    with Session(engine) as s:
        yield s


@pytest.fixture
def playbook(session: Session) -> Playbook:
    """Minimal Playbook row for tests that need a valid playbook_id FK."""
    pb = Playbook(
        id=uuid4(),
        name="test-play",
        inventory_sources=["inventory.yml"],
        started_at=datetime.now(timezone.utc),
        user="test-user",
    )
    session.add(pb)
    session.flush()
    return pb
