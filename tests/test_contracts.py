"""Wire-contract invariants for johnny.contracts.v1.

The plugin builds these dicts from a separate repo with no shared
import. Every contract surface is `_Strict` (extra='forbid' +
frozen=True), so typos surface as 422s at the wire boundary rather
than as silently-dropped fields. This file locks both halves:

  * the extra='forbid' wall — a stray key on any model raises.
  * the min_length floors — empty list/string where one is required.

A refactor that loosens either invariant on any model fails here,
not in production against a working plugin.
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest
from pydantic import BaseModel, ValidationError

from johnny.contracts.v1 import (
    EventsBatch,
    FactRecord,
    FactsBatch,
    HostStatCounts,
    PlaybookFinish,
    PlaybookStart,
    TaskEvent,
    TaskStatus,
)


def _facts() -> dict[str, Any]:
    return {"ansible_fqdn": "host.example.com"}


def _valid_factrecord() -> dict[str, Any]:
    return {
        "fqdn": "host.example.com",
        "inventory_hostname": "host",
        "groups": [],
        "ansible_facts": _facts(),
    }


def _valid_task_event() -> dict[str, Any]:
    return {
        "event_uuid": str(uuid4()),
        "fqdn": "host.example.com",
        "task_name": "install nginx",
        "task_action": "apt",
        "status": "ok",
        "started_at": "2026-05-08T12:00:00+00:00",
        "duration_ms": 100,
    }


def _valid_stat_counts() -> dict[str, int]:
    return dict(
        ok=0, changed=0, failed=0, unreachable=0,
        skipped=0, rescued=0, ignored=0,
    )


def _valid_payloads() -> dict[type[BaseModel], dict[str, Any]]:
    """Minimal-valid payload per wire model. Reused by the strictness
    and min_length tests below."""
    return {
        PlaybookStart: {
            "id": str(uuid4()),
            "name": "deploy.yml",
            "inventory_sources": ["inventory.yml"],
            "started_at": "2026-05-08T12:00:00+00:00",
            "user": "ansible",
        },
        HostStatCounts: _valid_stat_counts(),
        PlaybookFinish: {
            "finished_at": "2026-05-08T12:05:00+00:00",
            "stats": {"host.example.com": _valid_stat_counts()},
        },
        FactRecord: _valid_factrecord(),
        FactsBatch: {
            "captured_at": "2026-05-08T12:00:00+00:00",
            "hosts": [_valid_factrecord()],
        },
        TaskEvent: _valid_task_event(),
        EventsBatch: {"events": [_valid_task_event()]},
    }


class TestExtraForbid:
    """Every wire model rejects unknown keys. PlaybookStart already
    has a dedicated test on the HTTP layer (test_api.py); this file
    locks the same invariant for the remaining models so a refactor
    can't loosen one without the wall coming down here."""

    @pytest.mark.parametrize(
        "model",
        [
            PlaybookStart,
            HostStatCounts,
            PlaybookFinish,
            FactRecord,
            FactsBatch,
            TaskEvent,
            EventsBatch,
        ],
    )
    def test_unknown_top_level_key_raises(
        self, model: type[BaseModel]
    ) -> None:
        valid = _valid_payloads()[model]
        # First confirm the baseline payload parses, so a failure of the
        # *with-extra* parse can only mean the strictness wall fired.
        model.model_validate(valid)
        with pytest.raises(ValidationError, match="extra"):
            model.model_validate({**valid, "unexpected_key": "oops"})


class TestFrozen:
    """Wire models are frozen=True so passed-around instances can't be
    mutated between routes and services (the original 2024 design
    moved dicts around freely; v2 makes immutability explicit)."""

    def test_task_event_is_immutable(self) -> None:
        ev = TaskEvent.model_validate(_valid_task_event())
        with pytest.raises(ValidationError, match="frozen"):
            ev.task_name = "mutated"  # type: ignore[misc]


class TestMinLengthFloors:
    """`min_length=1` on the lists, strings, and ansible_facts dict.
    Empty plays/batches and zero-length identifiers must be rejected
    at the wire boundary."""

    @pytest.mark.parametrize(
        "field,empty_value",
        [
            ("name", ""),
            ("inventory_sources", []),
            ("user", ""),
        ],
    )
    def test_playbook_start_required_strings_and_lists(
        self, field: str, empty_value: Any
    ) -> None:
        body = _valid_payloads()[PlaybookStart] | {field: empty_value}
        with pytest.raises(ValidationError):
            PlaybookStart.model_validate(body)

    def test_facts_batch_rejects_empty_hosts_list(self) -> None:
        body = _valid_payloads()[FactsBatch] | {"hosts": []}
        with pytest.raises(ValidationError):
            FactsBatch.model_validate(body)

    def test_events_batch_rejects_empty_events_list(self) -> None:
        body = _valid_payloads()[EventsBatch] | {"events": []}
        with pytest.raises(ValidationError):
            EventsBatch.model_validate(body)

    @pytest.mark.parametrize(
        "field,empty_value",
        [
            ("fqdn", ""),
            ("inventory_hostname", ""),
            ("ansible_facts", {}),
        ],
    )
    def test_fact_record_required_fields(
        self, field: str, empty_value: Any
    ) -> None:
        body = _valid_factrecord() | {field: empty_value}
        with pytest.raises(ValidationError):
            FactRecord.model_validate(body)

    @pytest.mark.parametrize(
        "field",
        ["fqdn", "task_name", "task_action"],
    )
    def test_task_event_required_strings(self, field: str) -> None:
        body = _valid_task_event() | {field: ""}
        with pytest.raises(ValidationError):
            TaskEvent.model_validate(body)


class TestStatusAndCountFloors:
    """Negative counters and unknown status values are rejected — the
    plugin can't construct these by accident, but the wire endpoint
    is reachable to anyone holding the bearer token."""

    def test_negative_count_rejected(self) -> None:
        body = _valid_stat_counts() | {"ok": -1}
        with pytest.raises(ValidationError):
            HostStatCounts.model_validate(body)

    def test_unknown_status_rejected(self) -> None:
        body = _valid_task_event() | {"status": "exploded"}
        with pytest.raises(ValidationError):
            TaskEvent.model_validate(body)

    def test_negative_duration_rejected(self) -> None:
        body = _valid_task_event() | {"duration_ms": -1}
        with pytest.raises(ValidationError):
            TaskEvent.model_validate(body)


class TestRoundTrips:
    """Cheap sanity check: the minimal-valid payload for every model
    actually parses. Catches a refactor that adds a new required field
    without updating the helpers above (which the parametric tests
    also depend on)."""

    @pytest.mark.parametrize(
        "model",
        [
            PlaybookStart,
            HostStatCounts,
            PlaybookFinish,
            FactRecord,
            FactsBatch,
            TaskEvent,
            EventsBatch,
        ],
    )
    def test_valid_payload_parses(self, model: type[BaseModel]) -> None:
        instance = model.model_validate(_valid_payloads()[model])
        # Round-trip through model_dump back to a dict the contract
        # also accepts; catches a serializer that drops fields.
        re_parsed = model.model_validate(instance.model_dump(mode="json"))
        assert re_parsed == instance

    def test_started_at_must_be_tz_aware(self) -> None:
        # AwareDatetime rejects naive ISO timestamps — same contract
        # the persistence layer enforces, applied at the wire boundary
        # so a misconfigured plugin fails fast.
        body = _valid_payloads()[PlaybookStart] | {
            "started_at": "2026-05-08T12:00:00"
        }
        with pytest.raises(ValidationError):
            PlaybookStart.model_validate(body)


class TestGroupsTopologyCompat:
    """Wire-compat span for the groups_topology field (added 0.5.0).

    Old plugins (pre-callback-0.3.0) send no groups_topology key; the
    default must apply at the wire boundary for the entire window
    during which old plugins coexist with the new server.
    """

    def test_defaults_empty_for_old_plugin_payloads(self) -> None:
        pb = PlaybookStart.model_validate(_valid_payloads()[PlaybookStart])
        assert pb.groups_topology == {}

    def test_new_shape_roundtrips(self) -> None:
        body = _valid_payloads()[PlaybookStart] | {
            "groups_topology": {"linux": ["debian"], "debian": []}
        }
        pb = PlaybookStart.model_validate(body)
        assert pb.groups_topology == {"linux": ["debian"], "debian": []}


class TestTaskEventDefaults:
    """Defaults that older plugin versions rely on under extra='forbid'.
    The diff_truncated default is also tested at the service layer
    (test_events.py::test_diff_truncated_defaults_false_for_old_plugin_payloads);
    here we lock the contract-side default for stdout, stdout_truncated,
    and diff too, so a future field-shape change can't quietly break
    the receiver-first deploy order."""

    def test_stdout_defaults_to_empty_string(self) -> None:
        ev = TaskEvent.model_validate(_valid_task_event())
        assert ev.stdout == ""
        assert ev.stdout_truncated is False
        assert ev.diff is None
        assert ev.diff_truncated is False

    def test_status_enum_accepts_every_value(self) -> None:
        # Every value in the contracts.TaskStatus enum round-trips
        # through model_validate, including from the bare string form
        # the plugin uses on the wire.
        for status in TaskStatus:
            body = _valid_task_event() | {"status": status.value}
            ev = TaskEvent.model_validate(body)
            assert ev.status == status
