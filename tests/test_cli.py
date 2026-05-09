"""CLI smoke tests."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from click.testing import CliRunner
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from johnny.cli import cli
from johnny.contracts.v1 import (
    EventsBatch,
    FactRecord,
    FactsBatch,
    PlaybookStart,
    TaskEvent,
    TaskStatus,
)
from johnny.persistence import Event, Host, HostFactsHistory, Playbook
from johnny.services.hosts import HostService
from johnny.services.ingest import CallbackIngest


def _seed_old_data(session: Session, days_ago: int) -> None:
    captured = datetime.now(timezone.utc) - timedelta(days=days_ago)
    ingest = CallbackIngest(session)
    start = PlaybookStart(
        id=uuid4(),
        name="seed.yml",
        inventory_sources=["inventory.yml"],
        started_at=captured,
        user="ansible",
    )
    ingest.start_playbook(start)
    ingest.ingest_facts(
        start.id,
        FactsBatch(
            captured_at=captured,
            hosts=[
                FactRecord(
                    fqdn="old.example.com",
                    inventory_hostname="old",
                    groups=[],
                    ansible_facts={"ansible_uptime_seconds": 1},
                )
            ],
        ),
    )
    ingest.ingest_events(
        start.id,
        EventsBatch(
            events=[
                TaskEvent(
                    event_uuid=uuid4(),
                    fqdn="old.example.com",
                    task_name="probe",
                    task_action="command",
                    status=TaskStatus.OK,
                    started_at=captured,
                    duration_ms=10,
                )
            ]
        ),
    )
    session.commit()


class TestPrune:
    def test_prune_deletes_old_history_and_events(
        self,
        engine: Engine,
        session: Session,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _seed_old_data(session, days_ago=60)
        assert session.query(HostFactsHistory).count() == 1
        assert session.query(Event).count() == 1

        # Point the CLI at the test engine via env override + clearing
        # the get_settings lru_cache so it re-reads.
        from johnny.config import get_settings
        monkeypatch.setattr(
            "johnny.cli.make_engine", lambda _url: engine
        )
        get_settings.cache_clear()

        result = CliRunner().invoke(cli, ["prune", "--older-than-days", "30"])
        assert result.exit_code == 0
        assert "1 fact-history rows" in result.output
        assert "1 events" in result.output

        # Re-read the test session (the CLI committed in its own session;
        # ours needs an expire to see the deletes).
        session.expire_all()
        assert session.query(HostFactsHistory).count() == 0
        assert session.query(Event).count() == 0

    def test_prune_leaves_recent_data(
        self,
        engine: Engine,
        session: Session,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _seed_old_data(session, days_ago=1)
        assert session.query(Event).count() == 1

        from johnny.config import get_settings
        monkeypatch.setattr("johnny.cli.make_engine", lambda _url: engine)
        get_settings.cache_clear()

        result = CliRunner().invoke(cli, ["prune", "--older-than-days", "30"])
        assert result.exit_code == 0
        assert "0 fact-history rows" in result.output
        assert "0 events" in result.output

        session.expire_all()
        assert session.query(HostFactsHistory).count() == 1
        assert session.query(Event).count() == 1


def _seed_split_pair_for_cli(session: Session, playbook: Playbook) -> None:
    """Inline copy of test_hosts._seed_split_pair, kept here so the
    two test files don't import from each other."""
    svc = HostService(session)
    captured = datetime(2026, 5, 1, tzinfo=timezone.utc)
    facts = {
        "ansible_fqdn": "web1.example.com",
        "ansible_hostname": "web1",
        "ansible_domain": "example.com",
    }
    svc.upsert_from_record(
        playbook.id,
        captured,
        FactRecord(
            fqdn="web1",
            inventory_hostname="web1",
            groups=[],
            ansible_facts=facts,
        ),
    )
    svc.upsert_from_record(
        playbook.id,
        captured,
        FactRecord(
            fqdn="web1.example.com",
            inventory_hostname="web1.example.com",
            groups=[],
            ansible_facts=facts,
        ),
    )
    session.commit()


class TestDedupeHosts:
    def test_dry_run_prints_plan_without_writing(
        self,
        engine: Engine,
        session: Session,
        playbook: Playbook,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _seed_split_pair_for_cli(session, playbook)
        assert session.query(Host).count() == 2

        from johnny.config import get_settings
        monkeypatch.setattr("johnny.cli.make_engine", lambda _url: engine)
        get_settings.cache_clear()

        result = CliRunner().invoke(cli, ["dedupe-hosts", "--dry-run"])
        assert result.exit_code == 0, result.output
        assert "canonical: web1.example.com" in result.output
        assert "dry run" in result.output

        session.expire_all()
        assert session.query(Host).count() == 2  # untouched

    def test_live_merges_and_is_idempotent(
        self,
        engine: Engine,
        session: Session,
        playbook: Playbook,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _seed_split_pair_for_cli(session, playbook)
        assert session.query(Host).count() == 2

        from johnny.config import get_settings
        monkeypatch.setattr("johnny.cli.make_engine", lambda _url: engine)
        get_settings.cache_clear()

        result = CliRunner().invoke(cli, ["dedupe-hosts"])
        assert result.exit_code == 0, result.output
        assert "merged: 1 groups" in result.output

        session.expire_all()
        assert session.query(Host).count() == 1
        survivor = session.scalars(select(Host)).one()
        assert survivor.fqdn == "web1.example.com"

        # Rerun: nothing left to merge.
        result2 = CliRunner().invoke(cli, ["dedupe-hosts"])
        assert result2.exit_code == 0
        assert "nothing to merge" in result2.output

    def test_no_op_when_no_candidates(
        self,
        engine: Engine,
        session: Session,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from johnny.config import get_settings
        monkeypatch.setattr("johnny.cli.make_engine", lambda _url: engine)
        get_settings.cache_clear()
        result = CliRunner().invoke(cli, ["dedupe-hosts"])
        assert result.exit_code == 0
        assert "nothing to merge" in result.output
