"""Tests for johnny.persistence._types.UtcDateTime.

The decorator is load-bearing on SQLite (which silently strips tzinfo
on round-trip) and a no-op on Postgres. Lock the contract in both
halves — bind-side rejection of naive datetimes and result-side
re-attachment of UTC — so a future "drop the decorator" refactor
fails here rather than as confusing timestamp-mismatch in some
service test.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy.exc import StatementError
from sqlalchemy.orm import Session

from johnny.persistence import Host, Playbook
from johnny.persistence._types import UtcDateTime


class TestBind:
    def test_rejects_naive_datetime(self) -> None:
        deco = UtcDateTime()
        with pytest.raises(ValueError, match="naive datetime"):
            deco.process_bind_param(datetime(2026, 5, 8, 12, 0), dialect=None)  # type: ignore[arg-type]

    def test_normalises_non_utc_to_utc(self) -> None:
        deco = UtcDateTime()
        cest = timezone(timedelta(hours=2))
        out = deco.process_bind_param(
            datetime(2026, 5, 8, 14, 0, tzinfo=cest), dialect=None,  # type: ignore[arg-type]
        )
        assert out == datetime(2026, 5, 8, 12, 0, tzinfo=timezone.utc)

    def test_passes_none_through(self) -> None:
        assert UtcDateTime().process_bind_param(None, dialect=None) is None  # type: ignore[arg-type]


class TestResult:
    def test_attaches_utc_to_naive_value(self) -> None:
        # SQLite returns a naive datetime; the decorator must re-attach
        # UTC tzinfo so the caller sees the same tz-aware contract.
        deco = UtcDateTime()
        got = deco.process_result_value(
            datetime(2026, 5, 8, 12, 0), dialect=None,  # type: ignore[arg-type]
        )
        assert got == datetime(2026, 5, 8, 12, 0, tzinfo=timezone.utc)

    def test_converts_non_utc_aware_to_utc(self) -> None:
        deco = UtcDateTime()
        cest = timezone(timedelta(hours=2))
        got = deco.process_result_value(
            datetime(2026, 5, 8, 14, 0, tzinfo=cest), dialect=None,  # type: ignore[arg-type]
        )
        assert got == datetime(2026, 5, 8, 12, 0, tzinfo=timezone.utc)
        assert got.tzinfo == timezone.utc

    def test_passes_none_through(self) -> None:
        assert UtcDateTime().process_result_value(None, dialect=None) is None  # type: ignore[arg-type]


class TestRoundTripViaOrm:
    """Integration: a tz-aware UTC datetime survives an ORM flush/read
    against the in-memory SQLite engine without losing its tzinfo."""

    def test_playbook_started_at_round_trips_utc(self, session: Session) -> None:
        started = datetime(2026, 5, 8, 12, 0, tzinfo=timezone.utc)
        pb = Playbook(
            id=uuid4(),
            name="tz-roundtrip",
            inventory_sources=["inv.yml"],
            started_at=started,
            user="t",
        )
        session.add(pb)
        session.commit()
        session.expire_all()
        got = session.get(Playbook, pb.id)
        assert got is not None
        assert got.started_at == started
        assert got.started_at.tzinfo is not None

    def test_naive_datetime_on_flush_raises(self, session: Session) -> None:
        # The decorator must reject naive datetimes at bind time —
        # earlier than the DB write, so the transaction stays clean.
        # SQLAlchemy wraps the underlying ValueError in StatementError.
        h = Host(fqdn="naive.example.com", last_seen_at=datetime(2026, 5, 8, 12, 0))
        session.add(h)
        with pytest.raises(StatementError, match="naive datetime"):
            session.flush()
