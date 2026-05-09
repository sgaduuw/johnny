"""HostService: upsert facts, manage history, prune retention, merge splits.

All methods take a SQLAlchemy Session at construction time. Tests
instantiate directly with a fixture session; routes get one via
their framework's session dependency.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from johnny.contracts.v1 import FactRecord
from johnny.persistence._fqdn import resolve_fqdn
from johnny.persistence.models import (
    Event,
    Host,
    HostFactsHistory,
    PlaybookHost,
)


@dataclass(frozen=True)
class MergeGroup:
    """A set of Host rows that all resolve to the same canonical FQDN.

    survivor: the row that stays. Picked deterministically: prefer the
    row whose existing fqdn already matches canonical; tie-break by
    lowest id; if no row matches canonical, prefer the longest fqdn
    (most-qualified-looking).

    fk_counts: rows to re-point per dependent table. Reported by
    --dry-run; the live merge re-counts as it runs.
    """

    canonical_fqdn: str
    survivor: Host
    orphans: tuple[Host, ...]
    fk_counts: dict[str, int]


class HostService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def upsert_from_record(
        self,
        playbook_id: UUID,
        captured_at: datetime,
        record: FactRecord,
    ) -> Host:
        """Find-or-create Host by fqdn; update last_facts + last_seen_at;
        append a HostFactsHistory row. Returns the Host (id available)."""
        host = self.session.scalar(select(Host).where(Host.fqdn == record.fqdn))
        if host is None:
            host = Host(fqdn=record.fqdn)
            self.session.add(host)
        host.last_facts = record.ansible_facts
        host.last_seen_at = captured_at
        self.session.flush()  # populate host.id for the FK below

        self.session.add(
            HostFactsHistory(
                host_id=host.id,
                captured_at=captured_at,
                facts=record.ansible_facts,
                playbook_id=playbook_id,
            )
        )
        self.session.flush()
        return host

    def get_or_create_by_fqdn(self, fqdn: str) -> Host:
        """For event ingest: ensure a Host row exists even if no facts
        have arrived yet. last_facts stays {} until a facts batch lands."""
        host = self.session.scalar(select(Host).where(Host.fqdn == fqdn))
        if host is None:
            host = Host(fqdn=fqdn)
            self.session.add(host)
            self.session.flush()
        return host

    def latest(self, fqdn: str) -> Host | None:
        return self.session.scalar(select(Host).where(Host.fqdn == fqdn))

    def list_all(self) -> list[Host]:
        """All hosts ordered by fqdn ascending."""
        return list(self.session.scalars(select(Host).order_by(Host.fqdn)))

    def history(
        self,
        fqdn: str,
        since: datetime | None = None,
    ) -> list[HostFactsHistory]:
        """Fact snapshots for a host, newest first. `since` is inclusive."""
        host = self.latest(fqdn)
        if host is None:
            return []
        stmt = select(HostFactsHistory).where(HostFactsHistory.host_id == host.id)
        if since is not None:
            stmt = stmt.where(HostFactsHistory.captured_at >= since)
        stmt = stmt.order_by(HostFactsHistory.captured_at.desc())
        return list(self.session.scalars(stmt))

    def prune_history(self, older_than: datetime) -> int:
        """Delete HostFactsHistory rows strictly older than the cutoff.
        Returns the number deleted."""
        result = self.session.execute(
            delete(HostFactsHistory).where(
                HostFactsHistory.captured_at < older_than
            )
        )
        return result.rowcount

    def find_merge_candidates(self) -> list[MergeGroup]:
        """Group Host rows by canonical FQDN; return groups with >1 row.

        Canonical FQDN is computed by running each host's most-recent
        fact snapshot through the same convergence ladder the callback
        plugin now uses (johnny.persistence._fqdn). Hosts with no
        history rows are skipped: without facts we can't disambiguate
        them, and they'd merge against nothing anyway.
        """
        hosts = list(self.session.scalars(select(Host).order_by(Host.id)))
        groups: dict[str, list[Host]] = defaultdict(list)
        for host in hosts:
            latest = self.session.scalar(
                select(HostFactsHistory.facts)
                .where(HostFactsHistory.host_id == host.id)
                .order_by(HostFactsHistory.captured_at.desc())
                .limit(1)
            )
            if latest is None:
                continue
            # host.fqdn is the best inventory_hostname proxy we have at
            # rest: it's whatever the plugin's _resolve_fqdn settled on
            # at ingest time, including the inventory_hostname fallback.
            canonical = resolve_fqdn(latest, inventory_hostname=host.fqdn)
            groups[canonical].append(host)

        out: list[MergeGroup] = []
        for canonical, members in groups.items():
            if len(members) < 2:
                continue
            survivor = self._pick_survivor(canonical, members)
            orphans = tuple(h for h in members if h.id != survivor.id)
            orphan_ids = [o.id for o in orphans]
            fk_counts = {
                "host_facts_history": self._count_fk(
                    HostFactsHistory.host_id, orphan_ids
                ),
                "events": self._count_fk(Event.host_id, orphan_ids),
                "playbook_hosts": self._count_fk(
                    PlaybookHost.host_id, orphan_ids
                ),
            }
            out.append(
                MergeGroup(
                    canonical_fqdn=canonical,
                    survivor=survivor,
                    orphans=orphans,
                    fk_counts=fk_counts,
                )
            )
        return out

    def _count_fk(self, host_id_col, orphan_ids: list[int]) -> int:
        if not orphan_ids:
            return 0
        return self.session.scalar(
            select(func.count()).where(host_id_col.in_(orphan_ids))
        ) or 0

    @staticmethod
    def _pick_survivor(canonical: str, members: list[Host]) -> Host:
        canonical_matches = [h for h in members if h.fqdn == canonical]
        if canonical_matches:
            return min(canonical_matches, key=lambda h: h.id)
        # No row already at canonical; prefer the longest fqdn (treat
        # "web1.example.com" as more-qualified than "web1"), tie-break
        # by lowest id for determinism.
        return min(members, key=lambda h: (-len(h.fqdn), h.id))

    def merge_into(
        self,
        survivor_id: int,
        orphan_ids: list[int],
        canonical_fqdn: str,
    ) -> dict[str, int]:
        """Re-point FKs from orphans to survivor, then drop orphans.

        Caller owns the transaction boundary; the CLI commits per
        group so one bad group skips rather than rolling back the run.

        Returns the per-table count of rows actually touched.
        """
        if not orphan_ids:
            return {
                "host_facts_history": 0,
                "events": 0,
                "playbook_hosts": 0,
                "fqdn_updated": 0,
                "hosts_deleted": 0,
            }

        history_rows = self.session.execute(
            update(HostFactsHistory)
            .where(HostFactsHistory.host_id.in_(orphan_ids))
            .values(host_id=survivor_id)
        ).rowcount

        event_rows = self.session.execute(
            update(Event)
            .where(Event.host_id.in_(orphan_ids))
            .values(host_id=survivor_id)
        ).rowcount

        # playbook_hosts has composite PK (playbook_id, host_id). A
        # straight UPDATE would violate PK if survivor already has a
        # row for that playbook. Per orphan-row, decide UPDATE vs
        # DELETE based on whether the survivor is already present.
        playbook_host_rows = 0
        survivor_pbs = set(
            self.session.scalars(
                select(PlaybookHost.playbook_id).where(
                    PlaybookHost.host_id == survivor_id
                )
            )
        )
        orphan_phs = list(
            self.session.scalars(
                select(PlaybookHost).where(
                    PlaybookHost.host_id.in_(orphan_ids)
                )
            )
        )
        for ph in orphan_phs:
            if ph.playbook_id in survivor_pbs:
                # Survivor already represents this host on this play.
                # Drop the orphan's row outright; keep the survivor's
                # groups/inventory_hostname (older record wins by
                # virtue of having been there first).
                self.session.delete(ph)
            else:
                # Safe to re-point: no PK collision.
                self.session.execute(
                    update(PlaybookHost)
                    .where(
                        PlaybookHost.playbook_id == ph.playbook_id,
                        PlaybookHost.host_id == ph.host_id,
                    )
                    .values(host_id=survivor_id)
                )
                survivor_pbs.add(ph.playbook_id)
            playbook_host_rows += 1

        # Update survivor.fqdn to canonical if it differs. The fqdn
        # UNIQUE constraint can't fire because the only other holders
        # of `canonical` are the orphans, deleted in the next step.
        survivor = self.session.get(Host, survivor_id)
        fqdn_updated = 0
        if survivor is not None and survivor.fqdn != canonical_fqdn:
            survivor.fqdn = canonical_fqdn
            fqdn_updated = 1

        deleted = self.session.execute(
            delete(Host).where(Host.id.in_(orphan_ids))
        ).rowcount

        return {
            "host_facts_history": history_rows,
            "events": event_rows,
            "playbook_hosts": playbook_host_rows,
            "fqdn_updated": fqdn_updated,
            "hosts_deleted": deleted,
        }
