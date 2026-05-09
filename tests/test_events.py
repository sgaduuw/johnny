"""EventService tests."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from johnny.contracts.v1 import TaskEvent, TaskStatus
from johnny.persistence import Event, Host, Playbook
from johnny.services.events import EventService


def _event(
    *,
    fqdn: str = "host.example.com",
    task_name: str = "install package",
    task_action: str = "apt",
    status: TaskStatus = TaskStatus.OK,
    started_at: datetime | None = None,
    duration_ms: int = 100,
    stdout: str = "",
    stdout_truncated: bool = False,
    diff: str | None = None,
    diff_truncated: bool = False,
    event_uuid: UUID | None = None,
) -> TaskEvent:
    return TaskEvent(
        event_uuid=event_uuid or uuid4(),
        fqdn=fqdn,
        task_name=task_name,
        task_action=task_action,
        status=status,
        started_at=started_at or datetime.now(timezone.utc),
        duration_ms=duration_ms,
        stdout=stdout,
        stdout_truncated=stdout_truncated,
        diff=diff,
        diff_truncated=diff_truncated,
    )


class TestRecordBatch:
    def test_inserts_all_new_events(
        self, session: Session, playbook: Playbook
    ) -> None:
        svc = EventService(session)
        events = [_event(), _event(), _event()]
        accepted, ignored = svc.record_batch(playbook.id, events)
        assert (accepted, ignored) == (3, 0)
        assert session.query(Event).count() == 3

    def test_creates_hosts_for_unseen_fqdns(
        self, session: Session, playbook: Playbook
    ) -> None:
        svc = EventService(session)
        events = [_event(fqdn="a.example.com"), _event(fqdn="b.example.com")]
        svc.record_batch(playbook.id, events)
        fqdns = {h.fqdn for h in session.scalars(select(Host)).all()}
        assert fqdns == {"a.example.com", "b.example.com"}

    def test_reuses_existing_hosts(
        self, session: Session, playbook: Playbook
    ) -> None:
        existing = Host(fqdn="existing.example.com")
        session.add(existing)
        session.flush()
        existing_id = existing.id

        svc = EventService(session)
        svc.record_batch(playbook.id, [_event(fqdn="existing.example.com")])
        assert session.query(Host).count() == 1
        ev = session.scalars(select(Event)).one()
        assert ev.host_id == existing_id

    def test_idempotent_on_event_uuid(
        self, session: Session, playbook: Playbook
    ) -> None:
        svc = EventService(session)
        uuid = uuid4()
        first = _event(event_uuid=uuid)
        accepted_a, ignored_a = svc.record_batch(playbook.id, [first])
        accepted_b, ignored_b = svc.record_batch(
            playbook.id, [_event(event_uuid=uuid)]
        )
        assert (accepted_a, ignored_a) == (1, 0)
        assert (accepted_b, ignored_b) == (0, 1)
        assert session.query(Event).count() == 1

    def test_partial_overlap_counts_correctly(
        self, session: Session, playbook: Playbook
    ) -> None:
        svc = EventService(session)
        existing_uuid = uuid4()
        svc.record_batch(playbook.id, [_event(event_uuid=existing_uuid)])
        # Mixed batch: 2 new + 1 already-known
        accepted, ignored = svc.record_batch(
            playbook.id,
            [_event(), _event(event_uuid=existing_uuid), _event()],
        )
        assert (accepted, ignored) == (2, 1)
        assert session.query(Event).count() == 3

    def test_preserves_all_fields(
        self, session: Session, playbook: Playbook
    ) -> None:
        svc = EventService(session)
        uuid = uuid4()
        started = datetime(2026, 5, 8, 12, 0, tzinfo=timezone.utc)
        original = _event(
            event_uuid=uuid,
            fqdn="host.example.com",
            task_name="render config",
            task_action="template",
            status=TaskStatus.CHANGED,
            started_at=started,
            duration_ms=512,
            stdout="rendered foo.conf",
            stdout_truncated=True,
            diff="--- a\n+++ b\n@@ -1 +1 @@\n-old\n+new\n",
            diff_truncated=True,
        )
        svc.record_batch(playbook.id, [original])
        ev = session.scalars(select(Event)).one()
        assert ev.event_uuid == uuid
        assert ev.task_name == "render config"
        assert ev.task_action == "template"
        assert ev.status.value == "changed"
        assert ev.started_at == started
        assert ev.duration_ms == 512
        assert ev.stdout == "rendered foo.conf"
        assert ev.stdout_truncated is True
        assert ev.diff is not None
        assert "+new" in ev.diff
        assert ev.diff_truncated is True

    def test_diff_truncated_defaults_false_for_old_plugin_payloads(
        self, session: Session, playbook: Playbook
    ) -> None:
        # Wire-compat assertion: pre-v0.1.6 plugins don't send the
        # diff_truncated key. The contract default of False must
        # apply on parse so the server stays backward-compatible
        # with older controllers under extra="forbid".
        svc = EventService(session)
        # Hand-build the event dict the way an old plugin would
        # (no diff_truncated key) and parse via TaskEvent so the
        # default kicks in at the wire boundary.
        wire_dict = {
            "event_uuid": str(uuid4()),
            "fqdn": "host.example.com",
            "task_name": "old plugin task",
            "task_action": "apt",
            "status": "ok",
            "started_at": "2026-05-08T12:00:00+00:00",
            "duration_ms": 10,
            "stdout": "",
            "stdout_truncated": False,
            "diff": None,
            # No diff_truncated key — the old shape.
        }
        old_payload = TaskEvent.model_validate(wire_dict)
        assert old_payload.diff_truncated is False
        svc.record_batch(playbook.id, [old_payload])
        ev = session.scalars(select(Event)).one()
        assert ev.diff_truncated is False

    def test_round_trips_every_status(
        self, session: Session, playbook: Playbook
    ) -> None:
        # Verifies the contracts.TaskStatus -> persistence.TaskStatus
        # boundary works for every value in the enum.
        svc = EventService(session)
        events = [_event(status=s) for s in TaskStatus]
        svc.record_batch(playbook.id, events)
        stored = {ev.status.value for ev in session.scalars(select(Event))}
        assert stored == {s.value for s in TaskStatus}

    def test_empty_batch_is_noop(
        self, session: Session, playbook: Playbook
    ) -> None:
        svc = EventService(session)
        accepted, ignored = svc.record_batch(playbook.id, [])
        assert (accepted, ignored) == (0, 0)
        assert session.query(Event).count() == 0


class TestForPlay:
    def test_returns_empty_for_unknown(self, session: Session) -> None:
        assert EventService(session).for_play(uuid4()) == []

    def test_returns_chronological_order(
        self, session: Session, playbook: Playbook
    ) -> None:
        svc = EventService(session)
        t0 = datetime(2026, 5, 8, 12, 0, tzinfo=timezone.utc)
        events = [
            _event(started_at=t0 + timedelta(seconds=2)),
            _event(started_at=t0),
            _event(started_at=t0 + timedelta(seconds=1)),
        ]
        svc.record_batch(playbook.id, events)
        out = svc.for_play(playbook.id)
        assert [e.started_at for e in out] == sorted(e.started_at for e in out)

    def test_filters_by_playbook_id(
        self, session: Session, playbook: Playbook
    ) -> None:
        # Create a second playbook and verify cross-isolation
        other = Playbook(
            id=uuid4(),
            name="other.yml",
            inventory_sources=["other.yml"],
            started_at=datetime.now(timezone.utc),
            user="ansible",
        )
        session.add(other)
        session.flush()

        svc = EventService(session)
        svc.record_batch(playbook.id, [_event()])
        svc.record_batch(other.id, [_event(), _event()])
        assert len(svc.for_play(playbook.id)) == 1
        assert len(svc.for_play(other.id)) == 2


class TestPrune:
    def test_deletes_events_older_than_cutoff(
        self, session: Session, playbook: Playbook
    ) -> None:
        svc = EventService(session)
        t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
        events = [_event(started_at=t0 + timedelta(days=i)) for i in range(5)]
        svc.record_batch(playbook.id, events)

        cutoff = t0 + timedelta(days=2, hours=12)
        deleted = svc.prune(older_than=cutoff)
        assert deleted == 3
        assert session.query(Event).count() == 2

    def test_strict_lt(
        self, session: Session, playbook: Playbook
    ) -> None:
        svc = EventService(session)
        t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
        svc.record_batch(playbook.id, [_event(started_at=t0)])
        deleted = svc.prune(older_than=t0)
        assert deleted == 0

    def test_returns_zero_when_nothing_to_prune(self, session: Session) -> None:
        deleted = EventService(session).prune(
            older_than=datetime.now(timezone.utc)
        )
        assert deleted == 0
