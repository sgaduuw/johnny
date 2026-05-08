"""FastAPI dependencies: settings, session, auth.

The session dependency owns the per-request transaction: commit on
success, rollback on exception. CallbackIngest only flushes through
its sub-services, so partial work inside a single request stays
together as one unit.
"""

from __future__ import annotations

import secrets
from functools import lru_cache
from typing import Annotated, Iterator

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from johnny.config import Settings, get_settings
from johnny.persistence import make_engine


@lru_cache
def _engine_for(url: str) -> Engine:
    return make_engine(url)


def get_engine(settings: Annotated[Settings, Depends(get_settings)]) -> Engine:
    return _engine_for(settings.database_url)


def get_session(
    engine: Annotated[Engine, Depends(get_engine)],
) -> Iterator[Session]:
    with Session(engine) as session:
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise


def require_token(
    settings: Annotated[Settings, Depends(get_settings)],
    authorization: Annotated[str | None, Header()] = None,
) -> None:
    if settings.johnny_api_token is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="JOHNNY_API_TOKEN not configured on the server",
        )
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="missing bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    presented = authorization.removeprefix("Bearer ").strip()
    if not secrets.compare_digest(presented, settings.johnny_api_token):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )
