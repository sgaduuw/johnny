"""SQLAlchemy 2.0 declarative models for johnny.

JSON columns use a portable JSON type with a JSONB variant on
Postgres. Generated columns use the dialect-portable `json_path`
helper so the same model definition compiles cleanly on SQLite
(json_extract) and Postgres (-> / ->> with optional ::cast).
See CONTEXT.md "Projection via generated columns".
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from sqlalchemy import (
    Boolean,
    Computed,
    Enum,
    ForeignKey,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
    false,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from johnny.persistence._json import json_path
from johnny.persistence._types import UtcDateTime


class Base(DeclarativeBase):
    pass


JsonDict = JSON().with_variant(JSONB(), "postgresql")


class PlaybookStatus(StrEnum):
    RUNNING = "running"
    FINISHED = "finished"
    FAILED = "failed"


class TaskStatus(StrEnum):
    OK = "ok"
    CHANGED = "changed"
    FAILED = "failed"
    UNREACHABLE = "unreachable"
    SKIPPED = "skipped"


class Playbook(Base):
    __tablename__ = "playbooks"

    id: Mapped[UUID] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(512))
    inventory_sources: Mapped[list[str]] = mapped_column(JsonDict)
    started_at: Mapped[datetime] = mapped_column(UtcDateTime(), index=True)
    finished_at: Mapped[datetime | None] = mapped_column(UtcDateTime())
    user: Mapped[str] = mapped_column(String(128))
    status: Mapped[PlaybookStatus] = mapped_column(
        Enum(PlaybookStatus, name="playbook_status"),
        default=PlaybookStatus.RUNNING,
    )
    tags: Mapped[list[str]] = mapped_column(JsonDict, default=list)
    skip_tags: Mapped[list[str]] = mapped_column(JsonDict, default=list)
    limit: Mapped[str | None] = mapped_column(String(512))
    check_mode: Mapped[bool] = mapped_column(Boolean, default=False)
    stats: Mapped[dict[str, Any] | None] = mapped_column(JsonDict)

    hosts: Mapped[list[PlaybookHost]] = relationship(
        back_populates="playbook", cascade="all, delete-orphan"
    )
    events: Mapped[list[Event]] = relationship(back_populates="playbook")


class Host(Base):
    __tablename__ = "hosts"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    fqdn: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    last_facts: Mapped[dict[str, Any]] = mapped_column(JsonDict, default=dict)
    last_seen_at: Mapped[datetime | None] = mapped_column(
        UtcDateTime(), index=True
    )

    default_ipv4: Mapped[str | None] = mapped_column(
        String(45),
        Computed(
            json_path("last_facts", "ansible_default_ipv4.address"),
            persisted=True,
        ),
    )
    uptime_seconds: Mapped[int | None] = mapped_column(
        Integer,
        Computed(
            json_path("last_facts", "ansible_uptime_seconds", as_type="int"),
            persisted=True,
        ),
    )
    virt_role: Mapped[str | None] = mapped_column(
        String(16),
        Computed(
            json_path("last_facts", "ansible_virtualization_role"),
            persisted=True,
        ),
    )
    virt_type: Mapped[str | None] = mapped_column(
        String(32),
        Computed(
            json_path("last_facts", "ansible_virtualization_type"),
            persisted=True,
        ),
    )
    memtotal_mb: Mapped[int | None] = mapped_column(
        Integer,
        Computed(
            json_path("last_facts", "ansible_memtotal_mb", as_type="int"),
            persisted=True,
        ),
    )
    vcpus: Mapped[int | None] = mapped_column(
        Integer,
        Computed(
            json_path("last_facts", "ansible_processor_vcpus", as_type="int"),
            persisted=True,
        ),
    )
    distribution: Mapped[str | None] = mapped_column(
        String(64),
        Computed(
            json_path("last_facts", "ansible_distribution"),
            persisted=True,
        ),
    )
    distribution_version: Mapped[str | None] = mapped_column(
        String(64),
        Computed(
            json_path("last_facts", "ansible_distribution_version"),
            persisted=True,
        ),
    )
    kernel: Mapped[str | None] = mapped_column(
        String(128),
        Computed(
            json_path("last_facts", "ansible_kernel"),
            persisted=True,
        ),
    )

    history: Mapped[list[HostFactsHistory]] = relationship(
        back_populates="host",
        cascade="all, delete-orphan",
        order_by="HostFactsHistory.captured_at.desc()",
    )


class HostFactsHistory(Base):
    __tablename__ = "host_facts_history"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    host_id: Mapped[int] = mapped_column(
        ForeignKey("hosts.id", ondelete="CASCADE"), index=True
    )
    captured_at: Mapped[datetime] = mapped_column(UtcDateTime(), index=True)
    facts: Mapped[dict[str, Any]] = mapped_column(JsonDict)
    playbook_id: Mapped[UUID] = mapped_column(
        ForeignKey("playbooks.id", ondelete="CASCADE"), index=True
    )

    host: Mapped[Host] = relationship(back_populates="history")


class PlaybookHost(Base):
    """Per-run roster: which hosts ran this play, in which groups, under
    what inventory_hostname. Groups are a per-run observation, not a
    host attribute."""

    __tablename__ = "playbook_hosts"

    playbook_id: Mapped[UUID] = mapped_column(
        ForeignKey("playbooks.id", ondelete="CASCADE"), primary_key=True
    )
    host_id: Mapped[int] = mapped_column(
        ForeignKey("hosts.id", ondelete="CASCADE"), primary_key=True
    )
    inventory_hostname: Mapped[str] = mapped_column(String(255))
    groups: Mapped[list[str]] = mapped_column(JsonDict, default=list)

    playbook: Mapped[Playbook] = relationship(back_populates="hosts")
    host: Mapped[Host] = relationship()


class Event(Base):
    __tablename__ = "events"
    __table_args__ = (UniqueConstraint("event_uuid", name="uq_events_event_uuid"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    event_uuid: Mapped[UUID] = mapped_column(index=True)
    playbook_id: Mapped[UUID] = mapped_column(
        ForeignKey("playbooks.id", ondelete="CASCADE"), index=True
    )
    host_id: Mapped[int] = mapped_column(
        ForeignKey("hosts.id", ondelete="CASCADE"), index=True
    )
    task_name: Mapped[str] = mapped_column(String(512))
    task_action: Mapped[str] = mapped_column(String(128))
    status: Mapped[TaskStatus] = mapped_column(Enum(TaskStatus, name="task_status"))
    started_at: Mapped[datetime] = mapped_column(UtcDateTime(), index=True)
    duration_ms: Mapped[int]
    stdout: Mapped[str] = mapped_column(Text, default="")
    stdout_truncated: Mapped[bool] = mapped_column(Boolean, default=False)
    diff: Mapped[str | None] = mapped_column(Text)
    # server_default mirrors the alembic migration's
    # ALTER TABLE ADD COLUMN ... DEFAULT false. Without it, alembic
    # check reports model/schema drift.
    diff_truncated: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=false()
    )

    playbook: Mapped[Playbook] = relationship(back_populates="events")
    host: Mapped[Host] = relationship()
