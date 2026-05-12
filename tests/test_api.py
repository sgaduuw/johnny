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
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from johnny.api import create_app
from johnny.api.deps import get_engine, get_session, get_settings
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


class TestEngineDependency:
    """`get_engine` lru-caches one engine per database URL — the
    production boot path that the per-test fixtures override. Cover
    both the inner cache call and the outer dep wrapper directly so
    a future refactor that breaks the boot path isn't only caught
    in a real deploy."""

    def test_engine_for_returns_engine_with_url(self) -> None:
        from johnny.api.deps import _engine_for

        engine = _engine_for("sqlite:///:memory:")
        try:
            assert engine.url.database == ":memory:"
        finally:
            engine.dispose()
            _engine_for.cache_clear()

    def test_get_engine_reads_from_settings(self) -> None:
        engine = get_engine(
            Settings(
                database_url="sqlite:///:memory:",
                johnny_api_token="x",
            )
        )
        try:
            assert engine.dialect.name == "sqlite"
        finally:
            engine.dispose()
            from johnny.api.deps import _engine_for
            _engine_for.cache_clear()


class TestUnconfiguredServer:
    """A johnny-api container booted without JOHNNY_API_TOKEN must fail
    loud on every request rather than silently accepting them. See
    johnny/api/deps.py::require_token and the rationale comment on
    johnny/config.py::Settings.johnny_api_token."""

    def test_returns_503_when_token_not_configured(
        self, session: Session
    ) -> None:
        app = create_app()
        app.dependency_overrides[get_session] = lambda: session
        app.dependency_overrides[get_settings] = lambda: Settings(
            johnny_api_token=None, database_url="sqlite:///:memory:"
        )
        with TestClient(app) as c:
            # Even with a syntactically valid Bearer token, the server
            # has nothing to compare against — 503, not 401.
            r = c.post("/api/v1/playbooks", json=_start_body(), headers=AUTH)
        assert r.status_code == 503
        assert "JOHNNY_API_TOKEN not configured" in r.text


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


class TestRequestAtomicity:
    """The api's session dependency owns the per-request transaction:
    commit on success, rollback on exception. CallbackIngest itself
    only flushes through its sub-services, so a mid-batch failure
    must surface as a 500 with the whole request's writes rolled back.

    Exercises the production get_session generator (not the test-fixture
    override), so any future refactor that moves commit() into the
    service layer or drops the rollback branch fails here."""

    @pytest.fixture
    def atomic_client(
        self,
        engine: Engine,
        monkeypatch: pytest.MonkeyPatch,
    ) -> Iterator[TestClient]:
        # Override get_engine, NOT get_session — the production session
        # dep then runs against the test's in-memory engine and exercises
        # its commit-on-success / rollback-on-exception branches.
        app = create_app()
        app.dependency_overrides[get_engine] = lambda: engine
        app.dependency_overrides[get_settings] = lambda: Settings(
            johnny_api_token=TOKEN, database_url="sqlite:///:memory:"
        )
        with TestClient(app, raise_server_exceptions=False) as c:
            yield c

    def test_facts_batch_rolls_back_on_mid_batch_failure(
        self,
        atomic_client: TestClient,
        engine: Engine,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # /playbooks succeeds and commits the Playbook row. Then a facts
        # batch with two hosts is sent; the second host's upsert raises.
        # The whole facts request must roll back — host A's row and its
        # history row must NOT survive, even though they were flushed
        # before the failure.
        start = _start_body()
        r = atomic_client.post("/api/v1/playbooks", json=start, headers=AUTH)
        assert r.status_code == 202

        from johnny.services import hosts as hosts_module

        real_upsert = hosts_module.HostService.upsert_from_record
        calls = {"n": 0}

        def _raise_on_second(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            calls["n"] += 1
            if calls["n"] >= 2:
                raise RuntimeError("simulated mid-batch failure")
            return real_upsert(self, *args, **kwargs)

        monkeypatch.setattr(
            hosts_module.HostService, "upsert_from_record", _raise_on_second
        )

        facts = {
            "captured_at": "2026-05-08T12:00:30+00:00",
            "hosts": [
                {
                    "fqdn": "a.example.com",
                    "inventory_hostname": "a",
                    "groups": ["webservers"],
                    "ansible_facts": {"ansible_fqdn": "a.example.com"},
                },
                {
                    "fqdn": "b.example.com",
                    "inventory_hostname": "b",
                    "groups": ["webservers"],
                    "ansible_facts": {"ansible_fqdn": "b.example.com"},
                },
            ],
        }
        r = atomic_client.post(
            f"/api/v1/playbooks/{start['id']}/facts",
            json=facts,
            headers=AUTH,
        )
        assert r.status_code == 500

        # Verify on a fresh session — the API session is closed by now.
        with Session(engine) as verify:
            assert verify.query(Playbook).count() == 1  # /playbooks committed
            # Host A was flushed before the raise; rollback must drop it.
            assert verify.query(Host).count() == 0
            assert verify.query(HostFactsHistory).count() == 0
