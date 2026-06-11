"""PlayService tests."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from johnny.contracts.v1 import HostStatCounts, PlaybookFinish, PlaybookStart
from johnny.persistence import (
    Host,
    Playbook,
    PlaybookHost,
    PlaybookStatus,
)
from johnny.services.plays import PlayService


def _start_payload(**overrides: Any) -> PlaybookStart:
    base: dict[str, Any] = dict(
        id=uuid4(),
        name="deploy.yml",
        inventory_sources=["inventory.yml"],
        started_at=datetime.now(timezone.utc),
        user="ansible",
    )
    base.update(overrides)
    return PlaybookStart(**base)


def _stat(**counts: int) -> HostStatCounts:
    base = dict(ok=0, changed=0, failed=0, unreachable=0, skipped=0, rescued=0, ignored=0)
    base.update(counts)
    return HostStatCounts(**base)


class TestStart:
    def test_creates_new_playbook(self, session: Session) -> None:
        svc = PlayService(session)
        payload = _start_payload()
        pb = svc.start(payload)
        assert pb.id == payload.id
        assert pb.name == "deploy.yml"
        assert pb.inventory_sources == ["inventory.yml"]
        assert pb.started_at == payload.started_at
        assert pb.user == "ansible"
        assert pb.status == PlaybookStatus.RUNNING
        assert pb.finished_at is None

    def test_persists_optional_fields(self, session: Session) -> None:
        svc = PlayService(session)
        payload = _start_payload(
            limit="webservers",
            tags=["deploy"],
            skip_tags=["debug"],
            check_mode=True,
        )
        pb = svc.start(payload)
        assert pb.limit == "webservers"
        assert pb.tags == ["deploy"]
        assert pb.skip_tags == ["debug"]
        assert pb.check_mode is True

    def test_idempotent_returns_existing_row(self, session: Session) -> None:
        svc = PlayService(session)
        payload = _start_payload()
        first = svc.start(payload)
        second = svc.start(payload)
        assert first is second
        assert session.query(Playbook).count() == 1

    def test_idempotent_does_not_clobber_existing_metadata(
        self, session: Session
    ) -> None:
        # Plugin retry semantics: a re-POST with the same id but different
        # metadata should NOT overwrite the original — first-write-wins.
        svc = PlayService(session)
        original = _start_payload(name="original.yml")
        svc.start(original)
        retry = _start_payload(id=original.id, name="changed.yml")
        result = svc.start(retry)
        assert result.name == "original.yml"


class TestFinish:
    def test_sets_finished_at_and_stats(
        self, session: Session, playbook: Playbook
    ) -> None:
        svc = PlayService(session)
        finished = datetime.now(timezone.utc)
        payload = PlaybookFinish(
            finished_at=finished,
            stats={"host1.example.com": _stat(ok=5, changed=2)},
        )
        pb = svc.finish(playbook.id, payload)
        assert pb.finished_at == finished
        assert pb.stats == {
            "host1.example.com": {
                "ok": 5,
                "changed": 2,
                "failed": 0,
                "unreachable": 0,
                "skipped": 0,
                "rescued": 0,
                "ignored": 0,
            }
        }

    def test_status_finished_when_all_hosts_clean(
        self, session: Session, playbook: Playbook
    ) -> None:
        svc = PlayService(session)
        payload = PlaybookFinish(
            finished_at=datetime.now(timezone.utc),
            stats={
                "h1": _stat(ok=5, changed=2),
                "h2": _stat(ok=10, skipped=1),
            },
        )
        pb = svc.finish(playbook.id, payload)
        assert pb.status == PlaybookStatus.FINISHED

    def test_status_failed_when_any_host_has_failures(
        self, session: Session, playbook: Playbook
    ) -> None:
        svc = PlayService(session)
        payload = PlaybookFinish(
            finished_at=datetime.now(timezone.utc),
            stats={
                "h1": _stat(ok=5),
                "h2": _stat(ok=2, failed=1),
            },
        )
        pb = svc.finish(playbook.id, payload)
        assert pb.status == PlaybookStatus.FAILED

    def test_status_failed_when_any_host_unreachable(
        self, session: Session, playbook: Playbook
    ) -> None:
        svc = PlayService(session)
        payload = PlaybookFinish(
            finished_at=datetime.now(timezone.utc),
            stats={
                "h1": _stat(ok=5),
                "h2": _stat(unreachable=1),
            },
        )
        pb = svc.finish(playbook.id, payload)
        assert pb.status == PlaybookStatus.FAILED

    def test_status_finished_with_empty_stats(
        self, session: Session, playbook: Playbook
    ) -> None:
        # A play that hit no hosts (e.g. limit matched nothing) is not failed.
        svc = PlayService(session)
        payload = PlaybookFinish(
            finished_at=datetime.now(timezone.utc),
            stats={},
        )
        pb = svc.finish(playbook.id, payload)
        assert pb.status == PlaybookStatus.FINISHED

    def test_raises_on_unknown_playbook(self, session: Session) -> None:
        svc = PlayService(session)
        payload = PlaybookFinish(
            finished_at=datetime.now(timezone.utc),
            stats={},
        )
        with pytest.raises(ValueError, match="playbook not found"):
            svc.finish(uuid4(), payload)

    def test_idempotent_last_write_wins(
        self, session: Session, playbook: Playbook
    ) -> None:
        # A second finish() POST supersedes the first — useful when the
        # plugin retries with corrected stats.
        svc = PlayService(session)
        t1 = datetime(2026, 5, 8, 12, 0, tzinfo=timezone.utc)
        t2 = t1 + timedelta(seconds=30)
        svc.finish(
            playbook.id,
            PlaybookFinish(finished_at=t1, stats={"h": _stat(ok=1)}),
        )
        pb = svc.finish(
            playbook.id,
            PlaybookFinish(finished_at=t2, stats={"h": _stat(ok=2, failed=1)}),
        )
        assert pb.finished_at == t2
        assert pb.stats["h"]["ok"] == 2
        assert pb.status == PlaybookStatus.FAILED


class TestTouch:
    def test_touch_stamps_last_event_at(
        self, session: Session, playbook: Playbook
    ) -> None:
        # Force last_event_at to a known old value so the assertion is
        # robust against same-millisecond clock reads.
        old = datetime.now(timezone.utc) - timedelta(hours=2)
        playbook.last_event_at = old
        session.flush()

        PlayService(session).touch(playbook.id)
        session.refresh(playbook)
        assert playbook.last_event_at > old

    def test_touch_revives_abandoned_to_running(
        self, session: Session, playbook: Playbook
    ) -> None:
        playbook.status = PlaybookStatus.ABANDONED
        session.flush()

        PlayService(session).touch(playbook.id)
        session.refresh(playbook)
        assert playbook.status == PlaybookStatus.RUNNING

    def test_touch_leaves_finished_status_alone(
        self, session: Session, playbook: Playbook
    ) -> None:
        # touch stamps liveness on any status, but only revives ABANDONED.
        # FINISHED and FAILED are terminal states set by the controller's
        # explicit finish POST; a stray late ingest shouldn't undo them.
        playbook.status = PlaybookStatus.FINISHED
        session.flush()
        before_stamp = playbook.last_event_at

        PlayService(session).touch(playbook.id)
        session.refresh(playbook)
        assert playbook.status == PlaybookStatus.FINISHED
        assert playbook.last_event_at >= before_stamp

    def test_touch_silent_on_unknown_playbook(self, session: Session) -> None:
        # No raise. Consistent with the facts/events ingest paths, which
        # let FK violations surface at commit rather than pre-validating.
        PlayService(session).touch(uuid4())


class TestUpsertMembership:
    def test_creates_new_membership(
        self, session: Session, playbook: Playbook
    ) -> None:
        host = Host(fqdn="db1.example.com")
        session.add(host)
        session.flush()
        svc = PlayService(session)
        m = svc.upsert_membership(
            playbook.id, host.id, "db1", ["dbservers", "linux"]
        )
        assert m.playbook_id == playbook.id
        assert m.host_id == host.id
        assert m.inventory_hostname == "db1"
        assert m.groups == ["dbservers", "linux"]

    def test_updates_existing_membership(
        self, session: Session, playbook: Playbook
    ) -> None:
        host = Host(fqdn="db1.example.com")
        session.add(host)
        session.flush()
        svc = PlayService(session)
        svc.upsert_membership(playbook.id, host.id, "db1", ["dbservers"])
        m2 = svc.upsert_membership(
            playbook.id, host.id, "db1.example.com", ["dbservers", "primary"]
        )
        assert session.query(PlaybookHost).count() == 1
        assert m2.inventory_hostname == "db1.example.com"
        assert m2.groups == ["dbservers", "primary"]

    def test_multiple_hosts_create_separate_rows(
        self, session: Session, playbook: Playbook
    ) -> None:
        h1 = Host(fqdn="db1.example.com")
        h2 = Host(fqdn="db2.example.com")
        session.add_all([h1, h2])
        session.flush()
        svc = PlayService(session)
        svc.upsert_membership(playbook.id, h1.id, "db1", ["dbservers"])
        svc.upsert_membership(playbook.id, h2.id, "db2", ["dbservers"])
        assert session.query(PlaybookHost).count() == 2
