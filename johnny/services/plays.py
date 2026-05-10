"""PlayService: playbook lifecycle and per-run host roster."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import String, cast, or_, select
from sqlalchemy.orm import Session, joinedload
from sqlalchemy.sql import Select

from johnny.contracts.v1 import HostStatCounts, PlaybookFinish, PlaybookStart
from johnny.persistence.models import Playbook, PlaybookHost, PlaybookStatus
from johnny.web._search import SearchQuery

# Sortable columns exposed to the list view. Keeping this an explicit
# allowlist (rather than getattr-ing arbitrary attribute names) means
# the URL surface is closed: `?sort=stats` doesn't suddenly work just
# because the column exists.
PLAY_SORT_COLUMNS = {
    "started_at": Playbook.started_at,
    "finished_at": Playbook.finished_at,
    "name": Playbook.name,
    "user": Playbook.user,
    "status": Playbook.status,
}
PLAY_DEFAULT_SORT = "started_at"
PLAY_DEFAULT_DIR = "desc"

# Scopes the search box accepts on this list. Anything else round-trips
# as a bare term (see `_search.parse`).
PLAY_SEARCH_SCOPES = {"name", "user", "status", "inventory"}
# Columns bare terms OR across.
PLAY_BARE_COLUMNS = (Playbook.name, Playbook.user)

PLAY_PAGE_SIZE = 50


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


    def list_recent(
        self,
        sort: str = PLAY_DEFAULT_SORT,
        direction: str = PLAY_DEFAULT_DIR,
        query: SearchQuery | None = None,
        page: int = 1,
        page_size: int = PLAY_PAGE_SIZE,
    ) -> tuple[list[Playbook], bool]:
        """Filterable, sortable, paginated list. Default order:
        most-recent first.

        Returns `(rows, has_more)`. `has_more` is determined by
        fetching `page_size + 1` rows; the extra row is dropped before
        return. Avoids a second COUNT query on every page.

        Unknown `sort` or `direction` silently fall back to the
        defaults — the URL surface is user-typeable, and a bad param
        shouldn't 400 a browse.
        """
        page = max(1, page)
        offset = (page - 1) * page_size
        stmt: Select = select(Playbook)
        if query is not None and not query.is_empty():
            stmt = _apply_play_search(stmt, query)
        stmt = _apply_play_sort(stmt, sort, direction).offset(offset).limit(
            page_size + 1
        )
        rows = list(self.session.scalars(stmt))
        has_more = len(rows) > page_size
        return rows[:page_size], has_more

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


def _apply_play_sort(stmt: Select, sort: str, direction: str) -> Select:
    column = PLAY_SORT_COLUMNS.get(sort, PLAY_SORT_COLUMNS[PLAY_DEFAULT_SORT])
    if direction == "asc":
        return stmt.order_by(column.asc())
    return stmt.order_by(column.desc())


def _apply_play_search(stmt: Select, query: SearchQuery) -> Select:
    for scope, values in query.scopes.items():
        if scope == "name":
            stmt = stmt.where(or_(*(Playbook.name.ilike(f"%{v}%") for v in values)))
        elif scope == "user":
            stmt = stmt.where(or_(*(Playbook.user.ilike(f"%{v}%") for v in values)))
        elif scope == "status":
            allowed = []
            for v in values:
                try:
                    allowed.append(PlaybookStatus(v))
                except ValueError:
                    continue  # unknown status: silently drop
            if allowed:
                stmt = stmt.where(Playbook.status.in_(allowed))
        elif scope == "inventory":
            # inventory_sources is a JSON list. Cast-to-text + ILIKE is
            # the dialect-portable form; false-positives are possible
            # but acceptable for a browse filter.
            stmt = stmt.where(
                or_(
                    *(
                        cast(Playbook.inventory_sources, String).ilike(f"%{v}%")
                        for v in values
                    )
                )
            )
    for term in query.bare:
        stmt = stmt.where(
            or_(*(col.ilike(f"%{term}%") for col in PLAY_BARE_COLUMNS))
        )
    return stmt
