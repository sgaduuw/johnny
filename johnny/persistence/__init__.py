"""Persistence layer: SQLAlchemy 2.0 declarative models, the engine
factory with SQLite PRAGMAs, and the dialect-portable JSON path helper."""

from johnny.persistence._json import json_path
from johnny.persistence.engine import make_engine
from johnny.persistence.models import (
    Base,
    Event,
    Host,
    HostFactsHistory,
    Playbook,
    PlaybookHost,
    PlaybookStatus,
    TaskStatus,
)

__all__ = [
    "Base",
    "Event",
    "Host",
    "HostFactsHistory",
    "Playbook",
    "PlaybookHost",
    "PlaybookStatus",
    "TaskStatus",
    "json_path",
    "make_engine",
]
