"""HostService: upsert facts, manage history, prune retention.

All methods take a SQLAlchemy Session at construction time. Tests
instantiate directly with a fixture session; routes get one via
their framework's session dependency.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from johnny.contracts.v1 import FactRecord
from johnny.persistence.models import Host, HostFactsHistory


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
