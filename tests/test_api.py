"""FastAPI route tests.

Exercises auth, payload validation, the four endpoints, and the
end-to-end happy path through the HTTP layer. Sub-service behaviour
is unit-tested elsewhere; these tests verify the wiring.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterator
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from johnny.api import create_app
from johnny.api.deps import get_session, get_settings
from johnny.config import Settings
from johnny.contracts.v1 import DIFF_MAX, STDOUT_MAX
from johnny.persistence import Event, Host, HostFactsHistory, Playbook

TOKEN = "test-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture
def client(session: Session) -> Iterator[TestClient]:
    app = create_app()
    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[get_settings] = lambda: Settings(
        johnny_api_token=TOKEN, database_url="sqlite:///:memory:"
    )
    with TestClient(app) as c:
        yield c


def _start_body(playbook_id: UUID | None = None) -> dict[str, Any]:
    return {
        "id": str(playbook_id or uuid4()),
        "name": "deploy.yml",
        "inventory_sources": ["inventory.yml"],
        "started_at": "2026-05-08T12:00:00+00:00",
        "user": "ansible",
    }


def _facts_body() -> dict[str, Any]:
    return {
        "captured_at": "2026-05-08T12:00:30+00:00",
        "hosts": [
            {
                "fqdn": "web1.example.com",
                "inventory_hostname": "web1",
                "groups": ["webservers"],
                "ansible_facts": {
                    "ansible_default_ipv4": {"address": "10.0.0.1"},
                    "ansible_uptime_seconds": 3600,
                },
            }
        ],
    }


def _events_body(n: int = 1, fqdn: str = "web1.example.com") -> dict[str, Any]:
    return {
        "events": [
            {
                "event_uuid": str(uuid4()),
                "fqdn": fqdn,
                "task_name": "install nginx",
                "task_action": "apt",
                "status": "ok",
                "started_at": "2026-05-08T12:01:00+00:00",
                "duration_ms": 200,
            }
            for _ in range(n)
        ]
    }


def _finish_body() -> dict[str, Any]:
    return {
        "finished_at": "2026-05-08T12:05:00+00:00",
        "stats": {
            "web1.example.com": {
                "ok": 1, "changed": 0, "failed": 0, "unreachable": 0,
                "skipped": 0, "rescued": 0, "ignored": 0,
            }
        },
    }


class TestAuth:
    def test_rejects_missing_authorization_header(self, client: TestClient) -> None:
        r = client.post("/api/v1/playbooks", json=_start_body())
        assert r.status_code == 401
        assert r.headers.get("WWW-Authenticate") == "Bearer"

    def test_rejects_wrong_scheme(self, client: TestClient) -> None:
        r = client.post(
            "/api/v1/playbooks",
            json=_start_body(),
            headers={"Authorization": "Basic dXNlcjpwYXNz"},
        )
        assert r.status_code == 401

    def test_rejects_wrong_token(self, client: TestClient) -> None:
        r = client.post(
            "/api/v1/playbooks",
            json=_start_body(),
            headers={"Authorization": "Bearer wrong-token"},
        )
        assert r.status_code == 401

    def test_accepts_correct_token(self, client: TestClient) -> None:
        r = client.post("/api/v1/playbooks", json=_start_body(), headers=AUTH)
        assert r.status_code == 202


class TestStartPlaybook:
    def test_creates_playbook_and_returns_id(
        self, client: TestClient, session: Session
    ) -> None:
        body = _start_body()
        r = client.post("/api/v1/playbooks", json=body, headers=AUTH)
        assert r.status_code == 202
        assert r.json() == {"id": body["id"]}
        assert session.query(Playbook).count() == 1

    def test_rejects_invalid_payload(self, client: TestClient) -> None:
        body = _start_body()
        del body["name"]  # required field
        r = client.post("/api/v1/playbooks", json=body, headers=AUTH)
        assert r.status_code == 422

    def test_rejects_extra_fields(self, client: TestClient) -> None:
        body = _start_body()
        body["unknown_field"] = "oops"
        r = client.post("/api/v1/playbooks", json=body, headers=AUTH)
        assert r.status_code == 422


class TestIngestFacts:
    def test_creates_host_and_history(
        self, client: TestClient, session: Session
    ) -> None:
        start = _start_body()
        client.post("/api/v1/playbooks", json=start, headers=AUTH)
        r = client.post(
            f"/api/v1/playbooks/{start['id']}/facts",
            json=_facts_body(),
            headers=AUTH,
        )
        assert r.status_code == 202
        assert r.json() == {"accepted": 1, "ignored": 0}
        assert session.query(Host).count() == 1
        assert session.query(HostFactsHistory).count() == 1


class TestIngestEvents:
    def test_records_events_idempotently(
        self, client: TestClient, session: Session
    ) -> None:
        # First-write-wins: re-POSTing the same event_uuids with
        # *mutated* field values must keep the original payload's
        # values, not let the second batch overwrite them.
        start = _start_body()
        client.post("/api/v1/playbooks", json=start, headers=AUTH)
        body = _events_body(n=3)
        r = client.post(
            f"/api/v1/playbooks/{start['id']}/events", json=body, headers=AUTH
        )
        assert r.status_code == 202
        assert r.json() == {"accepted": 3, "ignored": 0}

        retried = {
            "events": [
                {**ev, "task_name": "WRONG", "status": "failed",
                 "stdout": "should not persist"}
                for ev in body["events"]
            ]
        }
        r2 = client.post(
            f"/api/v1/playbooks/{start['id']}/events", json=retried, headers=AUTH
        )
        assert r2.status_code == 202
        assert r2.json() == {"accepted": 0, "ignored": 3}
        assert session.query(Event).count() == 3
        for ev in session.query(Event).all():
            assert ev.task_name == "install nginx"
            assert ev.status.value == "ok"


class TestEventTruncationLimits:
    """Wire-contract caps on stdout / diff are pydantic max_length;
    FastAPI returns 422 on validation failure. Plugin should truncate
    upstream and signal via *_truncated; server enforces caps as a
    backstop. Both sides need a regression test against the pydantic
    enforcement, otherwise a future contract relax would silently
    accept oversize bodies."""

    def _events_body_with(self, **overrides: Any) -> dict[str, Any]:
        body = _events_body(n=1)
        body["events"][0].update(overrides)
        return body

    def test_oversize_stdout_returns_422(self, client: TestClient) -> None:
        start = _start_body()
        client.post("/api/v1/playbooks", json=start, headers=AUTH)
        oversize = "x" * (STDOUT_MAX + 1)
        r = client.post(
            f"/api/v1/playbooks/{start['id']}/events",
            json=self._events_body_with(stdout=oversize),
            headers=AUTH,
        )
        assert r.status_code == 422
        # Verify the error names the offending field, so debugging the
        # 422 in the wild is straightforward.
        assert "stdout" in r.text

    def test_oversize_diff_returns_422(self, client: TestClient) -> None:
        start = _start_body()
        client.post("/api/v1/playbooks", json=start, headers=AUTH)
        oversize = "x" * (DIFF_MAX + 1)
        r = client.post(
            f"/api/v1/playbooks/{start['id']}/events",
            json=self._events_body_with(diff=oversize),
            headers=AUTH,
        )
        assert r.status_code == 422
        assert "diff" in r.text

    def test_at_cap_stdout_accepted(self, client: TestClient) -> None:
        # Cap value itself is valid — only +1 fails.
        start = _start_body()
        client.post("/api/v1/playbooks", json=start, headers=AUTH)
        r = client.post(
            f"/api/v1/playbooks/{start['id']}/events",
            json=self._events_body_with(stdout="x" * STDOUT_MAX),
            headers=AUTH,
        )
        assert r.status_code == 202


class TestFinishPlaybook:
    def test_marks_finished(
        self, client: TestClient, session: Session
    ) -> None:
        start = _start_body()
        client.post("/api/v1/playbooks", json=start, headers=AUTH)
        r = client.post(
            f"/api/v1/playbooks/{start['id']}/finish",
            json=_finish_body(),
            headers=AUTH,
        )
        assert r.status_code == 202
        assert r.json() == {"id": start["id"]}
        pb = session.get(Playbook, UUID(start["id"]))
        assert pb is not None
        assert pb.finished_at is not None

    def test_404_when_playbook_unknown(self, client: TestClient) -> None:
        r = client.post(
            f"/api/v1/playbooks/{uuid4()}/finish",
            json=_finish_body(),
            headers=AUTH,
        )
        assert r.status_code == 404


class TestFullLifecycleOverHttp:
    def test_start_facts_events_finish(
        self, client: TestClient, session: Session
    ) -> None:
        start = _start_body()
        client.post("/api/v1/playbooks", json=start, headers=AUTH).raise_for_status()
        client.post(
            f"/api/v1/playbooks/{start['id']}/facts",
            json=_facts_body(),
            headers=AUTH,
        ).raise_for_status()
        client.post(
            f"/api/v1/playbooks/{start['id']}/events",
            json=_events_body(n=2),
            headers=AUTH,
        ).raise_for_status()
        client.post(
            f"/api/v1/playbooks/{start['id']}/finish",
            json=_finish_body(),
            headers=AUTH,
        ).raise_for_status()

        pb = session.get(Playbook, UUID(start["id"]))
        assert pb is not None
        assert pb.finished_at == datetime(2026, 5, 8, 12, 5, tzinfo=timezone.utc)
        assert session.query(Host).count() == 1
        assert session.query(HostFactsHistory).count() == 1
        assert session.query(Event).count() == 2
