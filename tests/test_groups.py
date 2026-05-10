"""GroupService unit tests.

Cover the live-ingest path (upsert_membership), the rebuild-from-history
backfill, and the read methods used by the web tier.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from johnny.persistence.models import (
    Group,
    Host,
    HostGroup,
    Playbook,
    PlaybookHost,
)
from johnny.services.groups import GroupService


def _make_host(session: Session, fqdn: str) -> Host:
    h = Host(fqdn=fqdn)
    session.add(h)
    session.flush()
    return h


def _make_play(
    session: Session, started_at: datetime, name: str = "test"
) -> Playbook:
    pb = Playbook(
        id=uuid4(),
        name=name,
        inventory_sources=["inv.yml"],
        started_at=started_at,
        user="ansible",
    )
    session.add(pb)
    session.flush()
    return pb


class TestUpsertMembership:
    def test_first_observation_creates_group_and_membership(
        self, session: Session, playbook: Playbook
    ) -> None:
        host = _make_host(session, "web1.example.com")
        captured = datetime.now(timezone.utc)
        GroupService(session).upsert_membership(
            host.id, ["all", "webservers"], captured, playbook.id
        )
        names = sorted(g.name for g in session.query(Group).all())
        assert names == ["all", "webservers"]
        memberships = session.query(HostGroup).all()
        assert len(memberships) == 2
        assert {m.host_id for m in memberships} == {host.id}

    def test_dropped_group_is_removed_from_membership(
        self, session: Session, playbook: Playbook
    ) -> None:
        """Latest-play-wins: a host that previously appeared in
        `webservers` and now reports only `all` loses the webservers
        membership. Group row stays."""
        host = _make_host(session, "web1.example.com")
        svc = GroupService(session)
        t0 = datetime.now(timezone.utc)
        svc.upsert_membership(host.id, ["all", "webservers"], t0, playbook.id)
        t1 = t0 + timedelta(hours=1)
        svc.upsert_membership(host.id, ["all"], t1, playbook.id)

        names = {
            g.name
            for g in session.query(Group)
            .join(HostGroup, HostGroup.group_id == Group.id)
            .filter(HostGroup.host_id == host.id)
            .all()
        }
        assert names == {"all"}
        # The dropped group itself stays; only its membership row is gone.
        assert session.query(Group).filter_by(name="webservers").one()

    def test_last_seen_advances_on_repeat(
        self, session: Session, playbook: Playbook
    ) -> None:
        host = _make_host(session, "web1.example.com")
        svc = GroupService(session)
        t0 = datetime.now(timezone.utc)
        svc.upsert_membership(host.id, ["all"], t0, playbook.id)
        t1 = t0 + timedelta(hours=1)
        svc.upsert_membership(host.id, ["all"], t1, playbook.id)
        group = session.query(Group).filter_by(name="all").one()
        assert group.first_seen_at == t0
        assert group.last_seen_at == t1


class TestListWithCounts:
    def test_all_pinned_first_then_alphabetical(
        self, session: Session, playbook: Playbook
    ) -> None:
        host = _make_host(session, "web1.example.com")
        svc = GroupService(session)
        svc.upsert_membership(
            host.id,
            ["webservers", "all", "linux"],
            datetime.now(timezone.utc),
            playbook.id,
        )
        ordered = [g.name for g, _ in svc.list_with_counts()]
        assert ordered == ["all", "linux", "webservers"]

    def test_count_reflects_member_hosts(
        self, session: Session, playbook: Playbook
    ) -> None:
        h1 = _make_host(session, "web1.example.com")
        h2 = _make_host(session, "db1.example.com")
        svc = GroupService(session)
        captured = datetime.now(timezone.utc)
        svc.upsert_membership(h1.id, ["all", "webservers"], captured, playbook.id)
        svc.upsert_membership(h2.id, ["all", "dbservers"], captured, playbook.id)
        counts = {g.name: n for g, n in svc.list_with_counts()}
        assert counts == {"all": 2, "dbservers": 1, "webservers": 1}


class TestHostsIn:
    def test_returns_member_hosts_ordered_by_fqdn(
        self, session: Session, playbook: Playbook
    ) -> None:
        h1 = _make_host(session, "web2.example.com")
        h2 = _make_host(session, "web1.example.com")
        svc = GroupService(session)
        captured = datetime.now(timezone.utc)
        svc.upsert_membership(h1.id, ["webservers"], captured, playbook.id)
        svc.upsert_membership(h2.id, ["webservers"], captured, playbook.id)
        group = svc.get_by_name("webservers")
        assert group is not None
        fqdns = [h.fqdn for h in svc.hosts_in(group)]
        assert fqdns == ["web1.example.com", "web2.example.com"]


class TestSetDescription:
    def test_set_then_clear(
        self, session: Session, playbook: Playbook
    ) -> None:
        host = _make_host(session, "web1.example.com")
        svc = GroupService(session)
        svc.upsert_membership(
            host.id, ["webservers"], datetime.now(timezone.utc), playbook.id
        )
        svc.set_description("webservers", "Front-end HTTP servers")
        assert svc.get_by_name("webservers").description == "Front-end HTTP servers"
        svc.set_description("webservers", None)
        assert svc.get_by_name("webservers").description is None

    def test_unknown_group_raises(self, session: Session) -> None:
        with pytest.raises(LookupError):
            GroupService(session).set_description("nope", "x")


class TestRebuildFromHistory:
    def test_rebuild_uses_latest_play_per_host(self, session: Session) -> None:
        """Two plays for the same host with different group lists; the
        rebuild should reflect only the most-recent observation, matching
        the live-ingest semantics."""
        host = _make_host(session, "web1.example.com")
        t0 = datetime(2026, 5, 1, 12, 0, tzinfo=timezone.utc)
        t1 = datetime(2026, 5, 8, 12, 0, tzinfo=timezone.utc)
        pb_old = _make_play(session, t0, name="old")
        pb_new = _make_play(session, t1, name="new")
        session.add(
            PlaybookHost(
                playbook_id=pb_old.id,
                host_id=host.id,
                inventory_hostname="web1",
                groups=["all", "webservers", "legacy"],
            )
        )
        session.add(
            PlaybookHost(
                playbook_id=pb_new.id,
                host_id=host.id,
                inventory_hostname="web1",
                groups=["all", "webservers"],
            )
        )
        session.flush()

        svc = GroupService(session)
        result = svc.rebuild_from_history()
        assert result["groups"] == 3  # all, webservers, legacy seen in audit
        assert result["memberships"] == 2  # latest play has 2 groups

        current_groups = {
            g.name
            for g in session.query(Group)
            .join(HostGroup, HostGroup.group_id == Group.id)
            .filter(HostGroup.host_id == host.id)
            .all()
        }
        assert current_groups == {"all", "webservers"}
        # `legacy` row exists in groups (was observed historically) but
        # has no current member.
        legacy = svc.get_by_name("legacy")
        assert legacy is not None
        assert legacy.first_seen_at == t0
        assert legacy.last_seen_at == t0

    def test_rebuild_preserves_descriptions(
        self, session: Session, playbook: Playbook
    ) -> None:
        host = _make_host(session, "web1.example.com")
        captured = datetime.now(timezone.utc)
        svc = GroupService(session)
        svc.upsert_membership(host.id, ["webservers"], captured, playbook.id)
        svc.set_description("webservers", "kept across rebuild")
        # Seed a playbook_hosts row so rebuild has audit data to walk.
        session.add(
            PlaybookHost(
                playbook_id=playbook.id,
                host_id=host.id,
                inventory_hostname="web1",
                groups=["webservers"],
            )
        )
        session.flush()

        svc.rebuild_from_history()
        assert svc.get_by_name("webservers").description == "kept across rebuild"
