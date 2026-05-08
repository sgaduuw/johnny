"""Callback ingest routes (/api/v1/...).

Routes are thin: parse pydantic payload -> call CallbackIngest ->
return response shape. All four endpoints require a bearer token via
the global router-level dependency."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from johnny.api.deps import get_session, require_token
from johnny.contracts.v1 import (
    BatchAccepted,
    EventsBatch,
    FactsBatch,
    PlaybookAccepted,
    PlaybookFinish,
    PlaybookStart,
)
from johnny.services.ingest import CallbackIngest

router = APIRouter(
    prefix="/api/v1",
    dependencies=[Depends(require_token)],
)


@router.post(
    "/playbooks",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=PlaybookAccepted,
)
def start_playbook(
    payload: PlaybookStart,
    session: Annotated[Session, Depends(get_session)],
) -> PlaybookAccepted:
    CallbackIngest(session).start_playbook(payload)
    return PlaybookAccepted(id=payload.id)


@router.post(
    "/playbooks/{playbook_id}/facts",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=BatchAccepted,
)
def ingest_facts(
    playbook_id: UUID,
    payload: FactsBatch,
    session: Annotated[Session, Depends(get_session)],
) -> BatchAccepted:
    accepted, ignored = CallbackIngest(session).ingest_facts(playbook_id, payload)
    return BatchAccepted(accepted=accepted, ignored=ignored)


@router.post(
    "/playbooks/{playbook_id}/events",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=BatchAccepted,
)
def ingest_events(
    playbook_id: UUID,
    payload: EventsBatch,
    session: Annotated[Session, Depends(get_session)],
) -> BatchAccepted:
    accepted, ignored = CallbackIngest(session).ingest_events(playbook_id, payload)
    return BatchAccepted(accepted=accepted, ignored=ignored)


@router.post(
    "/playbooks/{playbook_id}/finish",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=PlaybookAccepted,
)
def finish_playbook(
    playbook_id: UUID,
    payload: PlaybookFinish,
    session: Annotated[Session, Depends(get_session)],
) -> PlaybookAccepted:
    try:
        CallbackIngest(session).finish_playbook(playbook_id, payload)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(e)
        ) from e
    return PlaybookAccepted(id=playbook_id)
