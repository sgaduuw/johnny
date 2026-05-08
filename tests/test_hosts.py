"""HostService tests."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from johnny.contracts.v1 import FactRecord
from johnny.persistence import Host, HostFactsHistory, Playbook
from johnny.services.hosts import HostService


def _facts(ip: str = "10.0.0.5", uptime: int = 86400) -> dict[str, Any]:
    return {
        "ansible_default_ipv4": {"address": ip},
        "ansible_uptime_seconds": uptime,
        "ansible_virtualization_role": "host",
        "ansible_virtualization_type": "kvm",
        "ansible_memtotal_mb": 65536,
        "ansible_processor_vcpus": 16,
    }


def _record(
    fqdn: str = "nas.example.com",
    inv: str = "nas",
    groups: list[str] | None = None,
    facts: dict[str, Any] | None = None,
) -> FactRecord:
    return FactRecord(
        fqdn=fqdn,
        inventory_hostname=inv,
        groups=groups or [],
        ansible_facts=facts or _facts(),
    )


class TestUpsertFromRecord:
    def test_creates_new_host(self, session: Session, playbook: Playbook) -> None:
        svc = HostService(session)
        captured = datetime.now(timezone.utc)
        h = svc.upsert_from_record(playbook.id, captured, _record())
        assert h.id is not None
        assert h.fqdn == "nas.example.com"
        assert h.last_facts == _facts()
        assert h.last_seen_at == captured

    def test_materializes_projection_columns(
        self, session: Session, playbook: Playbook
    ) -> None:
        svc = HostService(session)
        captured = datetime.now(timezone.utc)
        h = svc.upsert_from_record(playbook.id, captured, _record())
        session.refresh(h)  # generated columns populate after the DB sees the row
        assert h.default_ipv4 == "10.0.0.5"
        assert h.uptime_seconds == 86400
        assert h.virt_role == "host"
        assert h.virt_type == "kvm"
        assert h.memtotal_mb == 65536
        assert h.vcpus == 16

    def test_updates_existing_host_in_place(
        self, session: Session, playbook: Playbook
    ) -> None:
        svc = HostService(session)
        t1 = datetime(2026, 1, 1, tzinfo=timezone.utc)
        t2 = t1 + timedelta(days=1)
        svc.upsert_from_record(playbook.id, t1, _record(facts=_facts(uptime=100)))
        h2 = svc.upsert_from_record(playbook.id, t2, _record(facts=_facts(uptime=200)))
        assert session.scalar(select(Host).where(Host.fqdn == "nas.example.com")) is h2
        assert session.query(Host).count() == 1
        assert h2.last_facts["ansible_uptime_seconds"] == 200
        assert h2.last_seen_at == t2

    def test_appends_history_row_each_call(
        self, session: Session, playbook: Playbook
    ) -> None:
        svc = HostService(session)
        t1 = datetime(2026, 1, 1, tzinfo=timezone.utc)
        t2 = t1 + timedelta(days=1)
        svc.upsert_from_record(playbook.id, t1, _record(facts=_facts(uptime=100)))
        svc.upsert_from_record(playbook.id, t2, _record(facts=_facts(uptime=200)))
        rows = session.scalars(
            select(HostFactsHistory).order_by(HostFactsHistory.captured_at)
        ).all()
        assert len(rows) == 2
        assert rows[0].facts["ansible_uptime_seconds"] == 100
        assert rows[1].facts["ansible_uptime_seconds"] == 200
        assert rows[0].host_id == rows[1].host_id


class TestGetOrCreateByFqdn:
    def test_creates_with_empty_facts(self, session: Session) -> None:
        h = HostService(session).get_or_create_by_fqdn("appliance.local")
        assert h.id is not None
        assert h.fqdn == "appliance.local"
        assert h.last_facts == {}
        assert h.last_seen_at is None

    def test_returns_existing_row(self, session: Session) -> None:
        svc = HostService(session)
        h1 = svc.get_or_create_by_fqdn("nas.example.com")
        h2 = svc.get_or_create_by_fqdn("nas.example.com")
        assert h1.id == h2.id
        assert session.query(Host).count() == 1


class TestLatest:
    def test_returns_none_for_unknown(self, session: Session) -> None:
        assert HostService(session).latest("never.seen.com") is None

    def test_returns_existing(self, session: Session, playbook: Playbook) -> None:
        svc = HostService(session)
        svc.upsert_from_record(playbook.id, datetime.now(timezone.utc), _record())
        h = svc.latest("nas.example.com")
        assert h is not None
        assert h.fqdn == "nas.example.com"


class TestHistory:
    def test_empty_for_unknown(self, session: Session) -> None:
        assert HostService(session).history("never.seen.com") == []

    def test_returns_all_when_since_is_none(
        self, session: Session, playbook: Playbook
    ) -> None:
        svc = HostService(session)
        t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
        for i in range(3):
            svc.upsert_from_record(playbook.id, t0 + timedelta(days=i), _record())
        rows = svc.history("nas.example.com")
        assert len(rows) == 3

    def test_returned_in_descending_order(
        self, session: Session, playbook: Playbook
    ) -> None:
        svc = HostService(session)
        t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
        for i in range(3):
            svc.upsert_from_record(playbook.id, t0 + timedelta(days=i), _record())
        rows = svc.history("nas.example.com")
        assert [r.captured_at for r in rows] == sorted(
            [r.captured_at for r in rows], reverse=True
        )

    def test_filters_by_since_inclusive(
        self, session: Session, playbook: Playbook
    ) -> None:
        svc = HostService(session)
        t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
        for i in range(3):
            svc.upsert_from_record(playbook.id, t0 + timedelta(days=i), _record())
        # since matches day 1's exact captured_at
        rows = svc.history("nas.example.com", since=t0 + timedelta(days=1))
        assert len(rows) == 2  # days 1 and 2


class TestPruneHistory:
    def test_deletes_rows_older_than_cutoff(
        self, session: Session, playbook: Playbook
    ) -> None:
        svc = HostService(session)
        t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
        for i in range(5):
            svc.upsert_from_record(playbook.id, t0 + timedelta(days=i), _record())
        cutoff = t0 + timedelta(days=2, hours=12)
        deleted = svc.prune_history(older_than=cutoff)
        assert deleted == 3  # days 0, 1, 2
        assert session.query(HostFactsHistory).count() == 2

    def test_prune_is_strict_lt(
        self, session: Session, playbook: Playbook
    ) -> None:
        # A row captured at exactly the cutoff is kept, not deleted.
        svc = HostService(session)
        t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
        svc.upsert_from_record(playbook.id, t0, _record())
        deleted = svc.prune_history(older_than=t0)
        assert deleted == 0
        assert session.query(HostFactsHistory).count() == 1

    def test_returns_zero_when_nothing_to_prune(
        self, session: Session
    ) -> None:
        deleted = HostService(session).prune_history(
            older_than=datetime.now(timezone.utc)
        )
        assert deleted == 0
