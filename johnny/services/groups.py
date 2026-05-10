"""GroupService: maintain the normalized group/host_groups view from
the per-run audit log in playbook_hosts.groups_json.

Source-of-truth split mirrors the host_facts_history vs host.last_facts
shape: the JSON list on playbook_hosts is the immutable observation
log; this service maintains the derived current-state view that the
read tier queries against. See CONTEXT.md "Groups model".
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from johnny.persistence.models import (
    Group,
    Host,
    HostGroup,
    Playbook,
    PlaybookHost,
)


class GroupService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def upsert_membership(
        self,
        host_id: int,
        group_names: list[str],
        captured_at: datetime,
        playbook_id: UUID,
    ) -> None:
        """Replace this host's current group membership with the given
        list. Latest-play-wins semantics: a host that appeared in
        ``[webservers, linux]`` last run and now reports ``[linux]``
        loses its `webservers` membership. Groups themselves are never
        auto-deleted; their description survives membership churn.
        """
        new_names = set(group_names)

        name_to_id: dict[str, int] = {}
        for name in new_names:
            group = self.session.scalar(select(Group).where(Group.name == name))
            if group is None:
                group = Group(
                    name=name,
                    first_seen_at=captured_at,
                    last_seen_at=captured_at,
                )
                self.session.add(group)
                self.session.flush()
            elif captured_at > group.last_seen_at:
                group.last_seen_at = captured_at
            name_to_id[name] = group.id

        existing = list(
            self.session.scalars(
                select(HostGroup).where(HostGroup.host_id == host_id)
            )
        )
        existing_by_gid = {hg.group_id: hg for hg in existing}

        for gid in name_to_id.values():
            hg = existing_by_gid.pop(gid, None)
            if hg is None:
                self.session.add(
                    HostGroup(
                        host_id=host_id,
                        group_id=gid,
                        last_seen_at=captured_at,
                        last_playbook_id=playbook_id,
                    )
                )
            else:
                hg.last_seen_at = captured_at
                hg.last_playbook_id = playbook_id

        for hg in existing_by_gid.values():
            self.session.delete(hg)

        self.session.flush()

    def list_with_counts(self) -> list[tuple[Group, int]]:
        """Every known group with its current member count. `all` is
        pinned first; the rest are alphabetical."""
        count_subq = (
            select(HostGroup.group_id, func.count().label("n"))
            .group_by(HostGroup.group_id)
            .subquery()
        )
        rows = self.session.execute(
            select(Group, func.coalesce(count_subq.c.n, 0))
            .outerjoin(count_subq, count_subq.c.group_id == Group.id)
        ).all()
        return sorted(
            ((g, int(n)) for g, n in rows),
            key=lambda gn: (gn[0].name != "all", gn[0].name),
        )

    def get_by_name(self, name: str) -> Group | None:
        return self.session.scalar(select(Group).where(Group.name == name))

    def hosts_in(self, group: Group) -> list[Host]:
        return list(
            self.session.scalars(
                select(Host)
                .join(HostGroup, HostGroup.host_id == Host.id)
                .where(HostGroup.group_id == group.id)
                .order_by(Host.fqdn)
            )
        )

    def set_description(self, name: str, description: str | None) -> Group:
        """Set or clear a group's free-text description. Raises
        LookupError if no group with that name exists; create the
        group via ingest first (or via a future operator-create
        command)."""
        group = self.get_by_name(name)
        if group is None:
            raise LookupError(f"unknown group: {name}")
        group.description = description
        self.session.flush()
        return group

    def rebuild_from_history(self) -> dict[str, int]:
        """Recompute groups + host_groups from playbook_hosts.groups_json.

        Walks every playbook_hosts row in chronological order:
          * Upserts a Group per name observed; first_seen/last_seen
            track min/max started_at.
          * Replaces host_groups membership with the *latest* play's
            groups list per host (matches the live ingest semantics).

        Existing Group descriptions are preserved. Groups with no
        members are left in place (the description may still be
        useful; obsolescence is its own concern).
        """
        rows = self.session.execute(
            select(
                PlaybookHost.host_id,
                PlaybookHost.groups,
                Playbook.id,
                Playbook.started_at,
            )
            .join(Playbook, Playbook.id == PlaybookHost.playbook_id)
            .order_by(Playbook.started_at)
        ).all()

        group_seen: dict[str, tuple[datetime, datetime]] = {}
        latest_per_host: dict[int, tuple[UUID, datetime, list[str]]] = {}
        for host_id, groups_list, pb_id, started_at in rows:
            names = list(groups_list or [])
            for name in names:
                if name not in group_seen:
                    group_seen[name] = (started_at, started_at)
                else:
                    first, _ = group_seen[name]
                    group_seen[name] = (first, started_at)
            latest_per_host[host_id] = (pb_id, started_at, names)

        for name, (first, last) in group_seen.items():
            group = self.get_by_name(name)
            if group is None:
                self.session.add(
                    Group(name=name, first_seen_at=first, last_seen_at=last)
                )
            else:
                if first < group.first_seen_at:
                    group.first_seen_at = first
                if last > group.last_seen_at:
                    group.last_seen_at = last
        self.session.flush()

        self.session.execute(delete(HostGroup))
        memberships = 0
        for host_id, (pb_id, started_at, names) in latest_per_host.items():
            for name in names:
                group = self.get_by_name(name)
                if group is None:
                    continue
                self.session.add(
                    HostGroup(
                        host_id=host_id,
                        group_id=group.id,
                        last_seen_at=started_at,
                        last_playbook_id=pb_id,
                    )
                )
                memberships += 1
        self.session.flush()

        return {"groups": len(group_seen), "memberships": memberships}
