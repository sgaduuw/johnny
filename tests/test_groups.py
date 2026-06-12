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
    GroupParent,
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
        hosts, has_more = svc.hosts_in(group)
        fqdns = [h.fqdn for h in hosts]
        assert fqdns == ["web1.example.com", "web2.example.com"]
        assert has_more is False

    def test_pagination_signals_has_more_then_terminates(
        self, session: Session, playbook: Playbook
    ) -> None:
        # page_size+1 fetch trick: with page_size=2 and 3 members,
        # page 1 returns 2 rows + has_more=True, page 2 returns 1 + False.
        svc = GroupService(session)
        captured = datetime.now(timezone.utc)
        for fqdn in ("a.example.com", "b.example.com", "c.example.com"):
            host = _make_host(session, fqdn)
            svc.upsert_membership(host.id, ["webservers"], captured, playbook.id)
        group = svc.get_by_name("webservers")
        assert group is not None

        first, more1 = svc.hosts_in(group, page=1, page_size=2)
        assert [h.fqdn for h in first] == ["a.example.com", "b.example.com"]
        assert more1 is True

        second, more2 = svc.hosts_in(group, page=2, page_size=2)
        assert [h.fqdn for h in second] == ["c.example.com"]
        assert more2 is False

        third, more3 = svc.hosts_in(group, page=3, page_size=2)
        assert third == []
        assert more3 is False


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

    def test_rebuild_advances_last_seen_when_play_is_newer(
        self, session: Session
    ) -> None:
        """A pre-existing Group row gets its `last_seen_at` advanced
        if the rebuild walks a playbook started after the group's
        current last_seen. Exercises the else-branch's `last >
        group.last_seen_at` update path."""
        host = _make_host(session, "web1.example.com")
        svc = GroupService(session)
        t0 = datetime(2026, 5, 1, tzinfo=timezone.utc)
        # First observation: group created at t0.
        pb_old = _make_play(session, t0, name="old")
        svc.upsert_membership(host.id, ["webservers"], t0, pb_old.id)
        # Second observation, t1, via the audit log only (no upsert
        # this time — that's the path the rebuild walks).
        t1 = t0 + timedelta(days=7)
        pb_new = _make_play(session, t1, name="new")
        session.add(
            PlaybookHost(
                playbook_id=pb_new.id,
                host_id=host.id,
                inventory_hostname="web1",
                groups=["webservers"],
            )
        )
        session.flush()

        result = svc.rebuild_from_history()
        # Note: rebuild walks `playbook_hosts` rows, not the
        # upsert_membership history; only one PH row is in scope
        # (the t1 one), so group_seen reports (t1, t1).
        assert result["memberships"] == 1
        group = svc.get_by_name("webservers")
        assert group is not None
        # The else-branch noticed t1 > existing last_seen and bumped it.
        assert group.last_seen_at == t1

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


class TestUpsertTopology:
    def _edges(self, session: Session) -> set[tuple[int, int]]:
        return {
            (e.child_id, e.parent_id)
            for e in session.query(GroupParent).all()
        }

    def _gid(self, session: Session, name: str) -> int:
        return GroupService(session).get_by_name(name).id

    def test_edge_direction_and_parent_only_group_creation(
        self, session: Session
    ) -> None:
        svc = GroupService(session)
        t0 = datetime.now(timezone.utc)
        svc.upsert_topology({"linux": ["debian"]}, t0)
        assert self._edges(session) == {
            (self._gid(session, "debian"), self._gid(session, "linux"))
        }

    def test_empty_payload_is_noop(self, session: Session) -> None:
        GroupService(session).upsert_topology({}, datetime.now(timezone.utc))
        assert session.query(Group).count() == 0

    def test_latest_run_wins_prunes_dropped_child(
        self, session: Session
    ) -> None:
        svc = GroupService(session)
        t0 = datetime.now(timezone.utc)
        svc.upsert_topology({"linux": ["debian", "redhat"]}, t0)
        svc.upsert_topology({"linux": ["debian"]}, t0 + timedelta(hours=1))
        assert self._edges(session) == {
            (self._gid(session, "debian"), self._gid(session, "linux"))
        }
        assert svc.get_by_name("redhat") is not None

    def test_empty_list_prunes_all_children(self, session: Session) -> None:
        svc = GroupService(session)
        t0 = datetime.now(timezone.utc)
        svc.upsert_topology({"linux": ["debian"]}, t0)
        svc.upsert_topology({"linux": []}, t0 + timedelta(hours=1))
        assert self._edges(session) == set()
        assert svc.get_by_name("linux") is not None
        assert svc.get_by_name("debian") is not None

    def test_empty_list_unknown_parent_creates_nothing(
        self, session: Session
    ) -> None:
        svc = GroupService(session)
        svc.upsert_topology({"ghost": []}, datetime.now(timezone.utc))
        assert svc.get_by_name("ghost") is None

    def test_cross_parent_isolation(self, session: Session) -> None:
        svc = GroupService(session)
        t0 = datetime.now(timezone.utc)
        svc.upsert_topology({"linux": ["debian"]}, t0)
        svc.upsert_topology({"webservers": ["nginx"]}, t0 + timedelta(hours=1))
        assert self._edges(session) == {
            (self._gid(session, "debian"), self._gid(session, "linux")),
            (self._gid(session, "nginx"), self._gid(session, "webservers")),
        }

    def test_stale_run_guard_skips_only_stale_parents(
        self, session: Session
    ) -> None:
        svc = GroupService(session)
        t0 = datetime.now(timezone.utc)
        t1 = t0 + timedelta(hours=1)
        svc.upsert_topology({"linux": ["debian"]}, t1)
        svc.upsert_topology({"linux": ["redhat"], "web": ["nginx"]}, t0)
        assert self._edges(session) == {
            (self._gid(session, "debian"), self._gid(session, "linux")),
            (self._gid(session, "nginx"), self._gid(session, "web")),
        }
        assert svc.get_by_name("redhat") is None

    def test_reparent_inversion_in_one_payload(
        self, session: Session
    ) -> None:
        svc = GroupService(session)
        t0 = datetime.now(timezone.utc)
        svc.upsert_topology({"linux": ["debian"]}, t0)
        svc.upsert_topology(
            {"linux": [], "debian": ["linux"]}, t0 + timedelta(hours=1)
        )
        assert self._edges(session) == {
            (self._gid(session, "linux"), self._gid(session, "debian"))
        }

    def test_genuine_cycle_skips_offending_edge_and_warns(
        self, session: Session, caplog: pytest.LogCaptureFixture
    ) -> None:
        svc = GroupService(session)
        with caplog.at_level("WARNING", logger="johnny.services.groups"):
            svc.upsert_topology(
                {"a": ["b"], "b": ["a"]}, datetime.now(timezone.utc)
            )
        assert self._edges(session) == {
            (self._gid(session, "b"), self._gid(session, "a"))
        }
        assert any("cycle" in r.message for r in caplog.records)

    def test_three_node_cycle_in_one_payload_skips_back_edge(
        self, session: Session, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A cycle formed by three edges in a single payload must be
        caught by the in-loop graph mutation: a -> b -> c accepted,
        then c -> a closes the loop and is the one edge dropped."""
        svc = GroupService(session)
        with caplog.at_level("WARNING", logger="johnny.services.groups"):
            svc.upsert_topology(
                {"a": ["b"], "b": ["c"], "c": ["a"]},
                datetime.now(timezone.utc),
            )
        assert self._edges(session) == {
            (self._gid(session, "b"), self._gid(session, "a")),
            (self._gid(session, "c"), self._gid(session, "b")),
        }
        assert any("cycle" in r.message for r in caplog.records)
