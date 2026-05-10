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
                    groups=["all", "webservers", "linux"],
                    ansible_facts={
                        "ansible_default_ipv4": {"address": "10.0.0.1"},
                        "ansible_uptime_seconds": 86400,
                        "ansible_virtualization_role": "guest",
                        "ansible_virtualization_type": "kvm",
                        "ansible_memtotal_mb": 4096,
                        "ansible_processor_vcpus": 2,
                        "ansible_distribution": "Debian",
                        "ansible_distribution_version": "12.5",
                        "ansible_kernel": "6.1.0-26-amd64",
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


class TestGroupsIndex:
    def test_empty_state_renders(self, client: FlaskClient) -> None:
        r = client.get("/")
        assert r.status_code == 200
        assert b"No groups seen yet" in r.data

    def test_seeded_groups_appear_with_all_pinned(
        self, client: FlaskClient, session: Session
    ) -> None:
        _seed_full_play(session)
        r = client.get("/")
        assert r.status_code == 200
        body = r.data.decode()
        # Three groups observed: all, webservers, linux. `all` is pinned
        # first; the other two follow alphabetically.
        assert body.index("all") < body.index("linux") < body.index("webservers")
        # Each card has an href to /g/<name>/.
        assert 'href="/g/all/"' in body
        assert 'href="/g/webservers/"' in body


class TestGroupDetail:
    def test_404_for_unknown_group(self, client: FlaskClient) -> None:
        r = client.get("/g/no-such-group/")
        assert r.status_code == 404

    def test_seeded_group_lists_member_host(
        self, client: FlaskClient, session: Session
    ) -> None:
        _seed_full_play(session)
        r = client.get("/g/all/")
        assert r.status_code == 200
        body = r.data.decode()
        assert "web1.example.com" in body
        assert "10.0.0.1" in body
        assert "4 GB" in body  # memtotal_mb=4096 -> 4 GB via mem_gb filter
        assert "Debian 12.5" in body
        assert "6.1.0-26-amd64" in body
        # Member host link points at /h/<fqdn>/ in the new scheme.
        assert 'href="/h/web1.example.com/"' in body


class TestHostDetail:
    def test_404_for_unknown_host(self, client: FlaskClient) -> None:
        r = client.get("/h/never.seen.com/")
        assert r.status_code == 404

    def test_seeded_host_renders(
        self, client: FlaskClient, session: Session
    ) -> None:
        _seed_full_play(session)
        r = client.get("/h/web1.example.com/")
        assert r.status_code == 200
        body = r.data.decode()
        assert "web1.example.com" in body
        assert "Fact history" in body
        assert "ansible_default_ipv4" in body  # raw facts dump

    def test_bare_host_with_no_history_renders(
        self, client: FlaskClient, session: Session
    ) -> None:
        # A Host row can exist with empty last_facts + no history rows
        # (e.g. EventService.get_or_create_by_fqdn manufactured one for
        # an unfacted host). The detail page conditionally branches on
        # uptime/history; smoke that the template doesn't 500.
        from johnny.persistence import Host
        session.add(Host(fqdn="bare.example.com", last_facts={}))
        session.commit()
        r = client.get("/h/bare.example.com/")
        assert r.status_code == 200
        body = r.data.decode()
        assert "bare.example.com" in body
        assert "No fact snapshots recorded" in body


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
        # All seven counters are summed: ok/changed/failed/unreachable/
        # skipped/rescued/ignored. Don't drop any — every counter pair
        # the dashboard might surface needs an assertion or it'll silently
        # regress if the filter shape changes.
        stats = {
            "a.example.com": {"ok": 5, "changed": 1, "failed": 0,
                              "unreachable": 0, "skipped": 2,
                              "rescued": 1, "ignored": 3},
            "b.example.com": {"ok": 3, "changed": 2, "failed": 1,
                              "unreachable": 0, "skipped": 0,
                              "rescued": 2, "ignored": 1},
        }
        out = _play_stats(stats)
        assert out is not None
        assert out["hosts"] == 2
        assert out["ok"] == 8
        assert out["changed"] == 3
        assert out["failed"] == 1
        assert out["unreachable"] == 0
        assert out["skipped"] == 2
        assert out["rescued"] == 3
        assert out["ignored"] == 4

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

    def test_running_playbook_with_no_data_renders(
        self, client: FlaskClient, session: Session
    ) -> None:
        # A play in RUNNING state has no roster, no events, no stats.
        # The detail template branches on each; smoke that it doesn't
        # 500 in this state (the most common state when an operator
        # clicks through mid-play).
        from johnny.services.plays import PlayService
        pid = uuid4()
        PlayService(session).start(
            PlaybookStart(
                id=pid,
                name="midflight.yml",
                inventory_sources=["inventory.yml"],
                started_at=datetime(2026, 5, 8, 12, 0, tzinfo=timezone.utc),
                user="ansible",
            )
        )
        session.commit()
        r = client.get(f"/playbooks/{pid}")
        assert r.status_code == 200
        body = r.data.decode()
        assert "midflight.yml" in body
        assert "running" in body
        assert "No roster recorded" in body
        assert "No events recorded" in body


class TestListSearchAndSort:
    """Smoke the scoped-search + sortable-header wiring on the list routes."""

    def _seed_two_plays(self, session: Session) -> tuple[str, str]:
        """Seed two distinct plays so filter/sort have something to bite."""
        from sqlalchemy import update

        from johnny.persistence.models import Playbook, PlaybookStatus
        from johnny.services.plays import PlayService

        svc = PlayService(session)
        a = PlaybookStart(
            id=uuid4(),
            name="alpha-deploy.yml",
            inventory_sources=["prod.yml"],
            started_at=datetime(2026, 5, 8, 12, 0, tzinfo=timezone.utc),
            user="ansible",
        )
        b = PlaybookStart(
            id=uuid4(),
            name="zulu-deploy.yml",
            inventory_sources=["staging.yml"],
            started_at=datetime(2026, 5, 9, 12, 0, tzinfo=timezone.utc),
            user="cron",
        )
        svc.start(a)
        svc.start(b)
        session.execute(
            update(Playbook).where(Playbook.id == a.id)
            .values(status=PlaybookStatus.FAILED)
        )
        session.execute(
            update(Playbook).where(Playbook.id == b.id)
            .values(status=PlaybookStatus.FINISHED)
        )
        session.commit()
        return str(a.id), str(b.id)

    def test_scoped_status_filter_on_playbooks(
        self, client: FlaskClient, session: Session
    ) -> None:
        self._seed_two_plays(session)
        r = client.get("/playbooks?q=status:failed")
        assert r.status_code == 200
        body = r.data.decode()
        assert "alpha-deploy.yml" in body
        assert "zulu-deploy.yml" not in body

    def test_bare_term_matches_name_or_user(
        self, client: FlaskClient, session: Session
    ) -> None:
        self._seed_two_plays(session)
        r = client.get("/playbooks?q=cron")
        body = r.data.decode()
        # `cron` is the user on zulu; should appear, alpha shouldn't.
        assert "zulu-deploy.yml" in body
        assert "alpha-deploy.yml" not in body

    def test_inventory_scope_matches_json_list_entry(
        self, client: FlaskClient, session: Session
    ) -> None:
        self._seed_two_plays(session)
        r = client.get("/playbooks?q=inventory:prod")
        body = r.data.decode()
        assert "alpha-deploy.yml" in body
        assert "zulu-deploy.yml" not in body

    def test_sort_by_name_asc(
        self, client: FlaskClient, session: Session
    ) -> None:
        self._seed_two_plays(session)
        r = client.get("/playbooks?sort=name&dir=asc")
        body = r.data.decode()
        assert body.index("alpha-deploy.yml") < body.index("zulu-deploy.yml")

    def test_sort_by_name_desc(
        self, client: FlaskClient, session: Session
    ) -> None:
        self._seed_two_plays(session)
        r = client.get("/playbooks?sort=name&dir=desc")
        body = r.data.decode()
        assert body.index("zulu-deploy.yml") < body.index("alpha-deploy.yml")

    def test_unknown_sort_param_falls_back_to_default(
        self, client: FlaskClient
    ) -> None:
        # Bad params shouldn't 400 a browse interaction.
        r = client.get("/playbooks?sort=lol&dir=sideways")
        assert r.status_code == 200

    def test_htmx_request_returns_partial_not_full_page(
        self, client: FlaskClient
    ) -> None:
        r = client.get("/playbooks", headers={"HX-Request": "true"})
        assert r.status_code == 200
        body = r.data.decode()
        assert "<!doctype html>" not in body.lower()
        assert 'id="plays-tbl"' in body

    def test_full_page_render_includes_html_shell(
        self, client: FlaskClient
    ) -> None:
        r = client.get("/playbooks")
        assert r.status_code == 200
        body = r.data.decode()
        assert body.lower().startswith("<!doctype html>")
        assert 'id="plays-tbl"' in body

    def test_groups_index_search_filters_by_name(
        self, client: FlaskClient, session: Session
    ) -> None:
        _seed_full_play(session)
        r = client.get("/?q=name:web")
        body = r.data.decode()
        assert "webservers" in body
        assert "linux" not in body or "linux</strong>" not in body

    def test_group_detail_kernel_scope(
        self, client: FlaskClient, session: Session
    ) -> None:
        _seed_full_play(session)
        # Match against the seeded host's kernel substring.
        r = client.get("/g/all/?q=kernel:6.1")
        body = r.data.decode()
        assert "web1.example.com" in body


class TestFooterVersion:
    def test_footer_shows_johnny_version(self, client: FlaskClient) -> None:
        from johnny import __version__

        r = client.get("/")
        assert r.status_code == 200
        body = r.data.decode()
        assert f"johnny {__version__}" in body
