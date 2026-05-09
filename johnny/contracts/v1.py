"""Wire contract between the johnny callback plugin and johnny-api.

Versioned at /api/v1/. Any breaking change here must bump the URL
prefix and document the migration in CONTEXT.md.

The plugin lives in a separate repo and re-implements these dict
shapes; drift is checked manually until/unless we ship them as a
shared package. Keep field names stable.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

STDOUT_MAX = 4096
DIFF_MAX = 16384


class _Strict(BaseModel):
    """Base for wire models: forbid unknown keys, immutable after parse."""

    model_config = ConfigDict(extra="forbid", frozen=True)


# ---------------------------------------------------------- playbook lifecycle


class PlaybookStart(_Strict):
    """POST /api/v1/playbooks body. Plugin calls at v2_playbook_on_start."""

    id: UUID = Field(description="UUIDv7 from the plugin; PK on the server")
    name: str = Field(min_length=1)
    inventory_sources: list[str] = Field(min_length=1)
    started_at: AwareDatetime
    user: str = Field(min_length=1, description="Controller-side ansible_user")
    limit: str | None = None
    tags: list[str] = Field(default_factory=list)
    skip_tags: list[str] = Field(default_factory=list)
    check_mode: bool = False


class HostStatCounts(_Strict):
    ok: int = Field(ge=0)
    changed: int = Field(ge=0)
    failed: int = Field(ge=0)
    unreachable: int = Field(ge=0)
    skipped: int = Field(ge=0)
    rescued: int = Field(ge=0)
    ignored: int = Field(ge=0)


class PlaybookFinish(_Strict):
    """POST /api/v1/playbooks/{id}/finish body. Plugin calls at v2_playbook_on_stats."""

    finished_at: AwareDatetime
    stats: dict[str, HostStatCounts] = Field(
        description="Per-host (key=fqdn) counts from the ansible stats object"
    )


# ---------------------------------------------------------- facts batch


class FactRecord(_Strict):
    """One host's fact snapshot. fqdn already resolved client-side via
    the fallback chain ansible_fqdn -> ansible_nodename -> inventory_hostname."""

    fqdn: str = Field(min_length=1)
    inventory_hostname: str = Field(min_length=1)
    groups: list[str] = Field(default_factory=list)
    ansible_facts: dict[str, Any] = Field(min_length=1)


class FactsBatch(_Strict):
    """POST /api/v1/playbooks/{id}/facts body."""

    captured_at: AwareDatetime
    hosts: list[FactRecord] = Field(min_length=1)


# ---------------------------------------------------------- events batch


class TaskStatus(StrEnum):
    OK = "ok"
    CHANGED = "changed"
    FAILED = "failed"
    UNREACHABLE = "unreachable"
    SKIPPED = "skipped"


class TaskEvent(_Strict):
    """One task result on one host. event_uuid is plugin-generated UUIDv7;
    server uses INSERT OR IGNORE on conflict so retries are safe."""

    event_uuid: UUID
    fqdn: str = Field(min_length=1)
    task_name: str = Field(min_length=1)
    task_action: str = Field(min_length=1, description="Module name, e.g. 'apt', 'copy'")
    status: TaskStatus
    started_at: AwareDatetime
    duration_ms: int = Field(ge=0)
    stdout: str = Field(default="", max_length=STDOUT_MAX)
    stdout_truncated: bool = False
    diff: str | None = Field(default=None, max_length=DIFF_MAX)
    # Defaults to False so older plugins (no key sent) parse cleanly
    # under extra="forbid". Server release MUST land before plugin
    # release that starts emitting this field — old server with
    # extra="forbid" would 422 on the unknown key.
    diff_truncated: bool = False


class EventsBatch(_Strict):
    """POST /api/v1/playbooks/{id}/events body."""

    events: list[TaskEvent] = Field(min_length=1)


# ---------------------------------------------------------- response shapes


class PlaybookAccepted(_Strict):
    """202 response from the playbook lifecycle endpoints."""

    id: UUID


class BatchAccepted(_Strict):
    """202 response from facts/events batches.

    accepted = landed; ignored = duplicates skipped via INSERT OR IGNORE.
    """

    accepted: int
    ignored: int = 0
