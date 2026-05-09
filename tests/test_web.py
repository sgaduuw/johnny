"""johnny-web smoke tests.

Verifies the read tier renders without errors against seeded data.
Per-template HTML correctness is intentionally lightweight — these
tests catch wiring breaks (template missing, route 500, filter typo)
not pixel-level UX regressions.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterator
from uuid import uuid4

import pytest
from flask.testing import FlaskClient
from sqlalchemy.engine import Engine
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
from johnny.services.ingest import CallbackIngest
from johnny.web import _play_stats, create_app


@pytest.fixture
def client(engine: Engine) -> Iterator[FlaskClient]:
    app = create_app(engine_factory=lambda: engine)
    with app.test_client() as c:
        yield c


def _seed_full_play(session: Session) -> str:
    """Seed one playbook end-to-end; return the playbook id as str."""
    ingest = CallbackIngest(session)
    start = PlaybookStart(
        id=uuid4(),
        name="deploy.yml",
        inventory_sources=["inventory.yml"],
        started_at=datetime(2026, 5, 8, 12, 0, tzinfo=timezone.utc),
        user="ansible",
    )
    ingest.start_playbook(start)
    ingest.ingest_facts(
        start.id,
        FactsBatch(
            captured_at=datetime(2026, 5, 8, 12, 0, 30, tzinfo=timezone.utc),
            hosts=[
                FactRecord(
                    fqdn="web1.example.com",
                    inventory_hostname="web1",
                    groups=["webservers", "linux"],
                    ansible_facts={
                        "ansible_default_ipv4": {"address": "10.0.0.1"},
                        "ansible_uptime_seconds": 86400,
                        "ansible_virtualization_role": "guest",
                        "ansible_virtualization_type": "kvm",
                        "ansible_memtotal_mb": 4096,
                        "ansible_processor_vcpus": 2,
                    },
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
                    fqdn="web1.example.com",
                    task_name="install nginx",
                    task_action="apt",
                    status=TaskStatus.OK,
                    started_at=datetime(2026, 5, 8, 12, 1, tzinfo=timezone.utc),
                    duration_ms=200,
                )
            ]
        ),
    )
    ingest.finish_playbook(
        start.id,
        PlaybookFinish(
            finished_at=datetime(2026, 5, 8, 12, 5, tzinfo=timezone.utc),
            stats={
                "web1.example.com": HostStatCounts(
                    ok=1, changed=0, failed=0, unreachable=0,
                    skipped=0, rescued=0, ignored=0,
                )
            },
        ),
    )
    session.commit()
    return str(start.id)


class TestHostsList:
    def test_empty_state_renders(self, client: FlaskClient) -> None:
        r = client.get("/")
        assert r.status_code == 200
        assert b"No hosts seen yet" in r.data

    def test_seeded_host_appears(
        self, client: FlaskClient, session: Session
    ) -> None:
        _seed_full_play(session)
        r = client.get("/")
        assert r.status_code == 200
        body = r.data.decode()
        assert "web1.example.com" in body
        assert "10.0.0.1" in body
        assert "4 GB" in body  # memtotal_mb=4096 -> 4 GB via mem_gb filter


class TestHostDetail:
    def test_404_for_unknown_host(self, client: FlaskClient) -> None:
        r = client.get("/hosts/never.seen.com")
        assert r.status_code == 404

    def test_seeded_host_renders(
        self, client: FlaskClient, session: Session
    ) -> None:
        _seed_full_play(session)
        r = client.get("/hosts/web1.example.com")
        assert r.status_code == 200
        body = r.data.decode()
        assert "web1.example.com" in body
        assert "Fact history" in body
        assert "ansible_default_ipv4" in body  # raw facts dump


class TestPlaybooksList:
    def test_empty_state_renders(self, client: FlaskClient) -> None:
        r = client.get("/playbooks")
        assert r.status_code == 200
        assert b"No playbooks recorded yet" in r.data

    def test_seeded_play_appears(
        self, client: FlaskClient, session: Session
    ) -> None:
        _seed_full_play(session)
        r = client.get("/playbooks")
        assert r.status_code == 200
        body = r.data.decode()
        assert "deploy.yml" in body
        assert "finished" in body
        # Outcome column: 1 host, 1 ok task from the seed.
        assert "1 host " in body
        assert "1 ok" in body


class TestPlayStatsFilter:
    """Aggregate-counts filter used in the playbooks list Outcome column."""

    def test_returns_none_for_no_stats(self) -> None:
        assert _play_stats(None) is None
        assert _play_stats({}) is None

    def test_sums_task_counts_across_hosts(self) -> None:
        stats = {
            "a.example.com": {"ok": 5, "changed": 1, "failed": 0,
                              "unreachable": 0, "skipped": 2,
                              "rescued": 0, "ignored": 0},
            "b.example.com": {"ok": 3, "changed": 2, "failed": 1,
                              "unreachable": 0, "skipped": 0,
                              "rescued": 0, "ignored": 0},
        }
        out = _play_stats(stats)
        assert out is not None
        assert out["hosts"] == 2
        assert out["ok"] == 8
        assert out["changed"] == 3
        assert out["failed"] == 1
        assert out["unreachable"] == 0
        assert out["skipped"] == 2

    def test_tolerates_missing_keys_in_host_dict(self) -> None:
        # Older or partial wire payloads may omit some counter keys;
        # treat missing as 0 rather than KeyError.
        stats = {"a": {"ok": 1}}
        out = _play_stats(stats)
        assert out is not None
        assert out["ok"] == 1
        assert out["changed"] == 0

    def test_tolerates_none_values_in_host_dict(self) -> None:
        # Defensive: JSON-deserialised stats can carry None for
        # unset keys depending on serializer choices.
        stats = {"a": {"ok": 2, "failed": None}}
        out = _play_stats(stats)
        assert out is not None
        assert out["ok"] == 2
        assert out["failed"] == 0


class TestPlaybookDetail:
    def test_404_for_unknown_playbook(self, client: FlaskClient) -> None:
        r = client.get(f"/playbooks/{uuid4()}")
        assert r.status_code == 404

    def test_seeded_play_renders_roster_and_events(
        self, client: FlaskClient, session: Session
    ) -> None:
        playbook_id = _seed_full_play(session)
        r = client.get(f"/playbooks/{playbook_id}")
        assert r.status_code == 200
        body = r.data.decode()
        assert "deploy.yml" in body
        assert "web1.example.com" in body  # roster
        assert "install nginx" in body  # event task name
        assert "200ms" in body  # event duration
