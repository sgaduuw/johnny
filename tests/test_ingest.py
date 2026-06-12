"""CallbackIngest integration tests.

These tests exercise the orchestrator that wires HostService,
EventService, and PlayService together. Unit-level behaviour for each
sub-service lives in test_hosts.py / test_events.py / test_plays.py;
this file focuses on the wiring contract: that a happy-path callback
sequence (start -> facts -> events -> finish) produces the expected
end state.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy.orm import Session

from johnny.contracts.v1 import (
    EventsBatch,
    FactRecord,
    FactsBatch,
    HostStatCounts,
    PlaybookFinish,
    PlaybookStart,
    TaskEvent,
    TaskStatus,
)
from johnny.persistence import (
    Event,
    GroupParent,
    Host,
    HostFactsHistory,
    Playbook,
    PlaybookHost,
    PlaybookStatus,
)
from johnny.services.ingest import CallbackIngest


def _facts_dict(ip: str = "10.0.0.1") -> dict[str, Any]:
    return {
        "ansible_default_ipv4": {"address": ip},
        "ansible_uptime_seconds": 3600,
        "ansible_virtualization_role": "guest",
        "ansible_virtualization_type": "kvm",
        "ansible_memtotal_mb": 4096,
        "ansible_processor_vcpus": 2,
    }


def _start_payload(playbook_id: UUID | None = None) -> PlaybookStart:
    return PlaybookStart(
        id=playbook_id or uuid4(),
        name="deploy.yml",
        inventory_sources=["inventory.yml"],
        started_at=datetime(2026, 5, 8, 12, 0, tzinfo=timezone.utc),
        user="ansible",
    )


def _facts_batch(*records: FactRecord) -> FactsBatch:
    return FactsBatch(
        captured_at=datetime(2026, 5, 8, 12, 0, 30, tzinfo=timezone.utc),
        hosts=list(records),
    )


def _record(
    fqdn: str,
    inv: str | None = None,
    groups: list[str] | None = None,
    facts: dict[str, Any] | None = None,
) -> FactRecord:
    return FactRecord(
        fqdn=fqdn,
        inventory_hostname=inv or fqdn.split(".")[0],
        groups=groups or [],
        ansible_facts=facts or _facts_dict(),
    )


def _event_payload(fqdn: str = "web1.example.com", **kwargs: Any) -> TaskEvent:
    base: dict[str, Any] = dict(
        event_uuid=uuid4(),
        fqdn=fqdn,
        task_name="install nginx",
        task_action="apt",
        status=TaskStatus.OK,
        started_at=datetime(2026, 5, 8, 12, 1, tzinfo=timezone.utc),
        duration_ms=200,
    )
    base.update(kwargs)
    return TaskEvent(**base)


def _stats_clean() -> dict[str, HostStatCounts]:
    counts = HostStatCounts(
        ok=1, changed=0, failed=0, unreachable=0,
        skipped=0, rescued=0, ignored=0,
    )
    return {"web1.example.com": counts}


class TestFullLifecycle:
    def test_start_facts_events_finish_happy_path(self, session: Session) -> None:
        ingest = CallbackIngest(session)
        start = _start_payload()
        playbook_id = start.id

        # 1. Start
        ingest.start_playbook(start)
        pb = session.get(Playbook, playbook_id)
        assert pb is not None
        assert pb.status == PlaybookStatus.RUNNING

        # 2. Facts (one host, with groups)
        accepted, ignored = ingest.ingest_facts(
            playbook_id,
            _facts_batch(
                _record("web1.example.com", groups=["webservers", "linux"])
            ),
        )
        assert (accepted, ignored) == (1, 0)
        host = session.scalars(
            session.query(Host).filter(Host.fqdn == "web1.example.com").statement
        ).one()
        assert host.last_facts == _facts_dict()
        # Membership row created
        m = session.get(PlaybookHost, (playbook_id, host.id))
        assert m is not None
        assert m.groups == ["webservers", "linux"]
        # History row appended
        assert session.query(HostFactsHistory).filter_by(host_id=host.id).count() == 1

        # 3. Events
        accepted, ignored = ingest.ingest_events(
            playbook_id,
            EventsBatch(events=[_event_payload(), _event_payload()]),
        )
        assert (accepted, ignored) == (2, 0)
        assert session.query(Event).filter_by(playbook_id=playbook_id).count() == 2

        # 4. Finish
        ingest.finish_playbook(
            playbook_id,
            PlaybookFinish(
                finished_at=datetime(2026, 5, 8, 12, 5, tzinfo=timezone.utc),
                stats=_stats_clean(),
            ),
        )
        session.refresh(pb)
        assert pb.status == PlaybookStatus.FINISHED
        assert pb.finished_at is not None


class TestIngestFacts:
    def test_creates_host_history_and_membership_per_record(
        self, session: Session
    ) -> None:
        ingest = CallbackIngest(session)
        start = _start_payload()
        ingest.start_playbook(start)

        ingest.ingest_facts(
            start.id,
            _facts_batch(
                _record("a.example.com", groups=["webservers"]),
                _record("b.example.com", groups=["dbservers"]),
            ),
        )
        assert session.query(Host).count() == 2
        assert session.query(HostFactsHistory).count() == 2
        assert session.query(PlaybookHost).count() == 2

    def test_repeated_facts_for_same_fqdn_appends_history_only(
        self, session: Session
    ) -> None:
        ingest = CallbackIngest(session)
        start = _start_payload()
        ingest.start_playbook(start)

        # Two batches, same host, different facts
        ingest.ingest_facts(
            start.id,
            FactsBatch(
                captured_at=datetime(2026, 5, 8, 12, 0, tzinfo=timezone.utc),
                hosts=[_record("a.example.com", facts=_facts_dict(ip="10.0.0.1"))],
            ),
        )
        ingest.ingest_facts(
            start.id,
            FactsBatch(
                captured_at=datetime(2026, 5, 8, 13, 0, tzinfo=timezone.utc),
                hosts=[_record("a.example.com", facts=_facts_dict(ip="10.0.0.2"))],
            ),
        )
        # Single host, two history rows
        assert session.query(Host).count() == 1
        assert session.query(HostFactsHistory).count() == 2
        host = session.query(Host).one()
        assert host.last_facts["ansible_default_ipv4"]["address"] == "10.0.0.2"


class TestIngestLiveness:
    """Verify that both ingest paths stamp last_event_at and revive ABANDONED."""

    def test_ingest_facts_ticks_last_event_at(
        self, session: Session, playbook: Playbook
    ) -> None:
        from datetime import timedelta

        playbook.last_event_at = datetime.now(timezone.utc) - timedelta(hours=2)
        session.flush()

        CallbackIngest(session).ingest_facts(
            playbook.id,
            _facts_batch(_record("host-a.example.com")),
        )
        session.refresh(playbook)
        assert playbook.last_event_at > datetime.now(timezone.utc) - timedelta(
            minutes=1
        )

    def test_ingest_facts_revives_abandoned(
        self, session: Session, playbook: Playbook
    ) -> None:
        from datetime import timedelta

        playbook.status = PlaybookStatus.ABANDONED
        playbook.last_event_at = datetime.now(timezone.utc) - timedelta(hours=2)
        session.flush()

        CallbackIngest(session).ingest_facts(
            playbook.id,
            _facts_batch(_record("host-a.example.com")),
        )
        session.refresh(playbook)
        assert playbook.status == PlaybookStatus.RUNNING

    def test_ingest_events_ticks_last_event_at(
        self, session: Session, playbook: Playbook
    ) -> None:
        from datetime import timedelta

        playbook.last_event_at = datetime.now(timezone.utc) - timedelta(hours=2)
        session.flush()

        CallbackIngest(session).ingest_events(
            playbook.id,
            EventsBatch(events=[_event_payload()]),
        )
        session.refresh(playbook)
        assert playbook.last_event_at > datetime.now(timezone.utc) - timedelta(
            minutes=1
        )

    def test_ingest_events_revives_abandoned(
        self, session: Session, playbook: Playbook
    ) -> None:
        from datetime import timedelta

        playbook.status = PlaybookStatus.ABANDONED
        playbook.last_event_at = datetime.now(timezone.utc) - timedelta(hours=2)
        session.flush()

        CallbackIngest(session).ingest_events(
            playbook.id,
            EventsBatch(events=[_event_payload()]),
        )
        session.refresh(playbook)
        assert playbook.status == PlaybookStatus.RUNNING


class TestIngestEvents:
    def test_events_use_existing_hosts_from_facts_batch(
        self, session: Session
    ) -> None:
        ingest = CallbackIngest(session)
        start = _start_payload()
        ingest.start_playbook(start)
        ingest.ingest_facts(
            start.id,
            _facts_batch(_record("web1.example.com")),
        )
        host_id_before = session.query(Host).filter_by(fqdn="web1.example.com").one().id

        ingest.ingest_events(
            start.id,
            EventsBatch(events=[_event_payload(fqdn="web1.example.com")]),
        )
        # No new host created — the events linked to the existing one
        assert session.query(Host).count() == 1
        ev = session.query(Event).one()
        assert ev.host_id == host_id_before

    def test_events_create_hosts_for_unseen_fqdns(
        self, session: Session
    ) -> None:
        # Edge case: a host that errored during setup has no facts batch,
        # but its failure event still arrives. EventService auto-creates
        # the Host with empty facts.
        ingest = CallbackIngest(session)
        start = _start_payload()
        ingest.start_playbook(start)
        ingest.ingest_events(
            start.id,
            EventsBatch(
                events=[_event_payload(fqdn="setup-failed.example.com",
                                       status=TaskStatus.FAILED)]
            ),
        )
        host = session.query(Host).one()
        assert host.fqdn == "setup-failed.example.com"
        assert host.last_facts == {}
        assert host.last_seen_at is None


class TestFinishPlaybook:
    def test_marks_playbook_complete(self, session: Session) -> None:
        ingest = CallbackIngest(session)
        start = _start_payload()
        ingest.start_playbook(start)
        finished = datetime(2026, 5, 8, 12, 30, tzinfo=timezone.utc)
        ingest.finish_playbook(
            start.id,
            PlaybookFinish(finished_at=finished, stats=_stats_clean()),
        )
        pb = session.get(Playbook, start.id)
        assert pb is not None
        assert pb.status == PlaybookStatus.FINISHED
        assert pb.finished_at == finished


class TestStartPlaybookTopology:
    def test_start_playbook_ingests_topology(self, session: Session) -> None:
        ingest = CallbackIngest(session)
        start = PlaybookStart(
            id=uuid4(),
            name="deploy.yml",
            inventory_sources=["inventory.yml"],
            started_at=datetime(2026, 5, 8, 12, 0, tzinfo=timezone.utc),
            user="ansible",
            groups_topology={"linux": ["debian"]},
        )
        ingest.start_playbook(start)
        edges = session.query(GroupParent).all()
        assert len(edges) == 1
