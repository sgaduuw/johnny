"""PlayService: playbook lifecycle and per-run host roster."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from johnny.contracts.v1 import HostStatCounts, PlaybookFinish, PlaybookStart
from johnny.persistence.models import Playbook, PlaybookHost, PlaybookStatus


class PlayService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def start(self, payload: PlaybookStart) -> Playbook:
        """Create the Playbook row in RUNNING state. Idempotent on id:
        a re-POST with the same id returns the existing row unchanged
        (first-write-wins, since a "play start" can't logically replay
        with different metadata)."""
        existing = self.session.get(Playbook, payload.id)
        if existing is not None:
            return existing
        playbook = Playbook(
            id=payload.id,
            name=payload.name,
            inventory_sources=list(payload.inventory_sources),
            started_at=payload.started_at,
            user=payload.user,
            limit=payload.limit,
            tags=list(payload.tags),
            skip_tags=list(payload.skip_tags),
            check_mode=payload.check_mode,
        )
        self.session.add(playbook)
        self.session.flush()
        return playbook

    def finish(self, playbook_id: UUID, payload: PlaybookFinish) -> Playbook:
        """Set finished_at + status + stats. Status derived from stats:
        FAILED if any host has failed>0 or unreachable>0, else FINISHED.
        Last-write-wins on repeated calls (a corrected stats POST
        supersedes an earlier one). Raises ValueError if the playbook
        doesn't exist (route handler should turn that into 404)."""
        playbook = self.session.get(Playbook, playbook_id)
        if playbook is None:
            raise ValueError(f"playbook not found: {playbook_id}")
        playbook.finished_at = payload.finished_at
        playbook.stats = payload.model_dump()["stats"]
        playbook.status = _derive_status(payload.stats)
        self.session.flush()
        return playbook

    def upsert_membership(
        self,
        playbook_id: UUID,
        host_id: int,
        inventory_hostname: str,
        groups: list[str],
    ) -> PlaybookHost:
        """Insert-or-update a single PlaybookHost roster row. Idempotent
        on the (playbook_id, host_id) PK; safe under plugin retries."""
        membership = self.session.get(PlaybookHost, (playbook_id, host_id))
        if membership is None:
            membership = PlaybookHost(
                playbook_id=playbook_id,
                host_id=host_id,
                inventory_hostname=inventory_hostname,
                groups=list(groups),
            )
            self.session.add(membership)
        else:
            membership.inventory_hostname = inventory_hostname
            membership.groups = list(groups)
        self.session.flush()
        return membership


    def list_recent(self, limit: int = 20) -> list[Playbook]:
        """Most-recent playbooks first, by started_at desc."""
        return list(
            self.session.scalars(
                select(Playbook).order_by(Playbook.started_at.desc()).limit(limit)
            )
        )

    def roster(self, playbook_id: UUID) -> list[PlaybookHost]:
        """Per-run host roster, with .host eagerly loaded for template render."""
        return list(
            self.session.scalars(
                select(PlaybookHost)
                .where(PlaybookHost.playbook_id == playbook_id)
                .options(joinedload(PlaybookHost.host))
            )
        )


def _derive_status(stats: dict[str, HostStatCounts]) -> PlaybookStatus:
    for counts in stats.values():
        if counts.failed > 0 or counts.unreachable > 0:
            return PlaybookStatus.FAILED
    return PlaybookStatus.FINISHED
