"""CallbackIngest: top-level orchestrator for the four callback endpoints.

Owns sub-service composition; sub-services share its session. Does NOT
own transaction boundaries — the calling route handler commits at the
end of a successful request and rolls back on exception, via the
framework's session dependency. CallbackIngest only flushes (through
the sub-services), so partial work inside a single batch stays
together as one unit.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.orm import Session

from johnny.contracts.v1 import (
    EventsBatch,
    FactsBatch,
    PlaybookFinish,
    PlaybookStart,
)
from johnny.services.events import EventService
from johnny.services.groups import GroupService
from johnny.services.hosts import HostService
from johnny.services.plays import PlayService


class CallbackIngest:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.hosts = HostService(session)
        self.events = EventService(session)
        self.plays = PlayService(session)
        self.groups = GroupService(session)

    def start_playbook(self, payload: PlaybookStart) -> None:
        self.plays.start(payload)

    def ingest_facts(
        self,
        playbook_id: UUID,
        payload: FactsBatch,
    ) -> tuple[int, int]:
        """For each host in the batch: upsert facts (with history) and
        upsert per-run roster membership. Returns (hosts_upserted, 0).
        The second value is reserved for future idempotency semantics
        on facts; currently no fact-level dedup is performed."""
        upserted = 0
        for record in payload.hosts:
            host = self.hosts.upsert_from_record(
                playbook_id, payload.captured_at, record
            )
            self.plays.upsert_membership(
                playbook_id,
                host.id,
                record.inventory_hostname,
                list(record.groups),
            )
            self.groups.upsert_membership(
                host.id,
                list(record.groups),
                payload.captured_at,
                playbook_id,
            )
            upserted += 1
        return (upserted, 0)

    def ingest_events(
        self,
        playbook_id: UUID,
        payload: EventsBatch,
    ) -> tuple[int, int]:
        return self.events.record_batch(playbook_id, payload.events)

    def finish_playbook(
        self,
        playbook_id: UUID,
        payload: PlaybookFinish,
    ) -> None:
        self.plays.finish(playbook_id, payload)
