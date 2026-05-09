"""EventService: bulk-insert callback events with idempotency, prune retention."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from johnny.contracts.v1 import TaskEvent
from johnny.persistence.models import Event
from johnny.services.hosts import HostService


class EventService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def record_batch(
        self,
        playbook_id: UUID,
        events: list[TaskEvent],
    ) -> tuple[int, int]:
        """Bulk-insert with INSERT OR IGNORE / ON CONFLICT DO NOTHING on
        event_uuid. Auto-creates Host rows for any unseen fqdns. Returns
        (accepted, ignored), where ignored = duplicates skipped via the
        idempotent insert (i.e. plugin retries that re-deliver an event
        already on disk)."""
        if not events:
            return (0, 0)

        host_service = HostService(self.session)
        host_id_by_fqdn: dict[str, int] = {}
        for fqdn in {e.fqdn for e in events}:
            host_id_by_fqdn[fqdn] = host_service.get_or_create_by_fqdn(fqdn).id

        rows = [
            {
                "event_uuid": e.event_uuid,
                "playbook_id": playbook_id,
                "host_id": host_id_by_fqdn[e.fqdn],
                "task_name": e.task_name,
                "task_action": e.task_action,
                "status": e.status.value,
                "started_at": e.started_at,
                "duration_ms": e.duration_ms,
                "stdout": e.stdout,
                "stdout_truncated": e.stdout_truncated,
                "diff": e.diff,
                "diff_truncated": e.diff_truncated,
            }
            for e in events
        ]

        stmt = _insert_ignore_event(self.session).values(rows)
        result = self.session.execute(stmt)
        accepted = result.rowcount
        return (accepted, len(events) - accepted)

    def for_play(self, playbook_id: UUID) -> list[Event]:
        """Events for a play, ordered by started_at ascending."""
        return list(
            self.session.scalars(
                select(Event)
                .where(Event.playbook_id == playbook_id)
                .order_by(Event.started_at)
            )
        )

    def prune(self, older_than: datetime) -> int:
        """Delete Event rows strictly older than the cutoff. Returns count."""
        result = self.session.execute(
            delete(Event).where(Event.started_at < older_than)
        )
        return result.rowcount


def _insert_ignore_event(session: Session) -> Any:
    """Build a dialect-specific INSERT ... ON CONFLICT DO NOTHING for the
    events table on event_uuid. SQLite and Postgres only."""
    dialect = session.get_bind().dialect.name
    if dialect == "sqlite":
        from sqlalchemy.dialects.sqlite import insert
    elif dialect == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
    else:
        raise NotImplementedError(
            f"insert-ignore not supported for dialect: {dialect}"
        )
    return insert(Event).on_conflict_do_nothing(index_elements=["event_uuid"])
