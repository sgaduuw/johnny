"""HostService tests."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from johnny.contracts.v1 import FactRecord
from johnny.persistence import Event, Host, HostFactsHistory, Playbook, PlaybookHost
from johnny.persistence.models import TaskStatus
from johnny.services.hosts import HostService


def _facts(ip: str = "10.0.0.5", uptime: int = 86400) -> dict[str, Any]:
    return {
        "ansible_default_ipv4": {"address": ip},
        "ansible_uptime_seconds": uptime,
        "ansible_virtualization_role": "host",
        "ansible_virtualization_type": "kvm",
        "ansible_memtotal_mb": 65536,
        "ansible_processor_vcpus": 16,
        "ansible_distribution": "Debian",
        "ansible_distribution_version": "12.5",
        "ansible_kernel": "6.1.0-26-amd64",
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
        assert h.distribution == "Debian"
        assert h.distribution_version == "12.5"
        assert h.kernel == "6.1.0-26-amd64"

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


def _seed_split_pair(
    session: Session,
    playbook: Playbook,
    *,
    short_fqdn: str = "web1",
    canonical_fqdn: str = "web1.example.com",
    facts: dict[str, Any] | None = None,
) -> tuple[Host, Host]:
    """Build two Host rows that resolve to the same canonical FQDN.

    Returns (short_form, fqdn_form). Both have history rows; their
    latest snapshot has identical fact content, mirroring what would
    happen if the same physical host posted under two names from
    different fact-arrival paths.
    """
    svc = HostService(session)
    captured = datetime(2026, 5, 1, tzinfo=timezone.utc)
    f = facts or {
        "ansible_fqdn": canonical_fqdn,
        "ansible_hostname": short_fqdn,
        "ansible_domain": "example.com",
    }
    short = svc.upsert_from_record(
        playbook.id,
        captured,
        FactRecord(
            fqdn=short_fqdn,
            inventory_hostname=short_fqdn,
            groups=[],
            ansible_facts=f,
        ),
    )
    fqdn_form = svc.upsert_from_record(
        playbook.id,
        captured,
        FactRecord(
            fqdn=canonical_fqdn,
            inventory_hostname=canonical_fqdn,
            groups=[],
            ansible_facts=f,
        ),
    )
    session.flush()
    return short, fqdn_form


def _add_event(
    session: Session, playbook_id: UUID, host_id: int
) -> Event:
    e = Event(
        event_uuid=uuid4(),
        playbook_id=playbook_id,
        host_id=host_id,
        task_name="probe",
        task_action="command",
        status=TaskStatus.OK,
        started_at=datetime(2026, 5, 1, tzinfo=timezone.utc),
        duration_ms=10,
    )
    session.add(e)
    session.flush()
    return e


def _add_playbook_host(
    session: Session, playbook_id: UUID, host_id: int
) -> PlaybookHost:
    ph = PlaybookHost(
        playbook_id=playbook_id,
        host_id=host_id,
        inventory_hostname=f"host-{host_id}",
        groups=[],
    )
    session.add(ph)
    session.flush()
    return ph


class TestFindMergeCandidates:
    def test_no_candidates_when_all_unique(
        self, session: Session, playbook: Playbook
    ) -> None:
        svc = HostService(session)
        svc.upsert_from_record(
            playbook.id,
            datetime.now(timezone.utc),
            FactRecord(
                fqdn="lonely.example.com",
                inventory_hostname="lonely",
                groups=[],
                ansible_facts={"ansible_fqdn": "lonely.example.com"},
            ),
        )
        assert svc.find_merge_candidates() == []

    def test_groups_split_pair_with_canonical_survivor(
        self, session: Session, playbook: Playbook
    ) -> None:
        short, fqdn_form = _seed_split_pair(session, playbook)
        groups = HostService(session).find_merge_candidates()
        assert len(groups) == 1
        g = groups[0]
        assert g.canonical_fqdn == "web1.example.com"
        assert g.survivor.id == fqdn_form.id
        assert [o.id for o in g.orphans] == [short.id]

    def test_skips_hosts_with_no_history(
        self, session: Session
    ) -> None:
        # Two empty-history hosts share a name pattern but can't be
        # canonicalised without facts; they're left alone.
        HostService(session).get_or_create_by_fqdn("ghost1")
        HostService(session).get_or_create_by_fqdn("ghost2")
        assert HostService(session).find_merge_candidates() == []

    def test_groups_split_pair_when_short_row_has_unprefixed_facts(
        self, session: Session, playbook: Playbook
    ) -> None:
        # Regression: johnny-callback v0.1.2..v0.1.4 stored facts from
        # the variable_manager with the ansible_ prefix stripped.
        # Before the fix, find_merge_candidates ran resolve_fqdn over
        # the raw shape and the short row canonicalised to its own
        # short fqdn (no ansible_fqdn key found), never grouping with
        # the FQDN-form duplicate.
        svc = HostService(session)
        captured = datetime(2026, 5, 1, tzinfo=timezone.utc)
        # Short row: unprefixed (the buggy plugin's output shape).
        short = svc.upsert_from_record(
            playbook.id,
            captured,
            FactRecord(
                fqdn="bespin",
                inventory_hostname="bespin",
                groups=[],
                ansible_facts={
                    "fqdn": "bespin.ntv.ts18.eu",
                    "hostname": "bespin",
                    "domain": "ntv.ts18.eu",
                    "default_ipv4": {"address": "192.168.1.6"},
                },
            ),
        )
        # FQDN row: prefixed (from _record_facts, which always
        # returned the right shape).
        fqdn_form = svc.upsert_from_record(
            playbook.id,
            captured,
            FactRecord(
                fqdn="bespin.ntv.ts18.eu",
                inventory_hostname="bespin",
                groups=[],
                ansible_facts={
                    "ansible_fqdn": "bespin.ntv.ts18.eu",
                    "ansible_hostname": "bespin",
                    "ansible_domain": "ntv.ts18.eu",
                    "ansible_default_ipv4": {"address": "192.168.1.6"},
                },
            ),
        )
        groups = HostService(session).find_merge_candidates()
        assert len(groups) == 1
        g = groups[0]
        assert g.canonical_fqdn == "bespin.ntv.ts18.eu"
        assert g.survivor.id == fqdn_form.id
        assert [o.id for o in g.orphans] == [short.id]

    def test_fk_counts_reflect_actual_orphan_rows(
        self, session: Session, playbook: Playbook
    ) -> None:
        short, _ = _seed_split_pair(session, playbook)
        # Add some events and a playbook_hosts row pointing at short.
        _add_event(session, playbook.id, short.id)
        _add_event(session, playbook.id, short.id)
        _add_playbook_host(session, playbook.id, short.id)
        groups = HostService(session).find_merge_candidates()
        assert groups[0].fk_counts == {
            "host_facts_history": 1,  # one snapshot per upsert
            "events": 2,
            "playbook_hosts": 1,
        }


class TestMergeInto:
    def test_re_points_history_events_and_playbook_hosts(
        self, session: Session, playbook: Playbook
    ) -> None:
        short, fqdn_form = _seed_split_pair(session, playbook)
        _add_event(session, playbook.id, short.id)
        _add_event(session, playbook.id, short.id)
        _add_playbook_host(session, playbook.id, short.id)

        counts = HostService(session).merge_into(
            survivor_id=fqdn_form.id,
            orphan_ids=[short.id],
            canonical_fqdn="web1.example.com",
        )
        session.flush()

        assert counts["events"] == 2
        assert counts["host_facts_history"] == 1
        assert counts["playbook_hosts"] == 1
        assert counts["hosts_deleted"] == 1

        assert session.query(Event).filter_by(host_id=short.id).count() == 0
        assert session.query(Event).filter_by(host_id=fqdn_form.id).count() == 2
        assert session.get(Host, short.id) is None
        assert session.get(Host, fqdn_form.id) is not None

    def test_handles_playbook_hosts_pk_collision(
        self, session: Session, playbook: Playbook
    ) -> None:
        # Both survivor and orphan have a playbook_hosts row for the
        # same playbook. The merge must drop the orphan's row, not
        # IntegrityError trying to UPDATE into the existing PK.
        short, fqdn_form = _seed_split_pair(session, playbook)
        _add_playbook_host(session, playbook.id, short.id)
        _add_playbook_host(session, playbook.id, fqdn_form.id)
        assert session.query(PlaybookHost).count() == 2

        HostService(session).merge_into(
            survivor_id=fqdn_form.id,
            orphan_ids=[short.id],
            canonical_fqdn="web1.example.com",
        )
        session.flush()

        rows = session.query(PlaybookHost).all()
        assert len(rows) == 1
        assert rows[0].host_id == fqdn_form.id

    def test_updates_survivor_fqdn_when_differs(
        self, session: Session, playbook: Playbook
    ) -> None:
        # Both rows are off-canonical; merge promotes the survivor
        # to the canonical form once the orphan is gone.
        svc = HostService(session)
        captured = datetime(2026, 5, 1, tzinfo=timezone.utc)
        facts = {
            "ansible_hostname": "web1",
            "ansible_domain": "example.com",
        }
        a = svc.upsert_from_record(
            playbook.id,
            captured,
            FactRecord(
                fqdn="web1-a",
                inventory_hostname="web1-a",
                groups=[],
                ansible_facts=facts,
            ),
        )
        b = svc.upsert_from_record(
            playbook.id,
            captured,
            FactRecord(
                fqdn="web1-bbb",
                inventory_hostname="web1-bbb",
                groups=[],
                ansible_facts=facts,
            ),
        )
        # Neither row is at canonical; pick_survivor takes longest
        # fqdn (b), then promotes it.
        groups = svc.find_merge_candidates()
        assert len(groups) == 1
        assert groups[0].canonical_fqdn == "web1.example.com"
        assert groups[0].survivor.id == b.id

        svc.merge_into(b.id, [a.id], "web1.example.com")
        session.flush()
        session.refresh(b)
        assert b.fqdn == "web1.example.com"

    def test_idempotent_no_op_after_merge(
        self, session: Session, playbook: Playbook
    ) -> None:
        short, fqdn_form = _seed_split_pair(session, playbook)
        svc = HostService(session)
        groups = svc.find_merge_candidates()
        assert len(groups) == 1
        svc.merge_into(
            groups[0].survivor.id,
            [o.id for o in groups[0].orphans],
            groups[0].canonical_fqdn,
        )
        session.flush()
        # Subsequent run sees a single canonical host; no candidates.
        assert svc.find_merge_candidates() == []

    def test_empty_orphan_list_is_noop(
        self, session: Session
    ) -> None:
        # Defensive: callers should never pass empty, but if they do
        # the method returns zero counts and writes nothing.
        counts = HostService(session).merge_into(
            survivor_id=42, orphan_ids=[], canonical_fqdn="x"
        )
        assert counts["hosts_deleted"] == 0
        assert counts["events"] == 0
