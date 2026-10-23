"""The HTTP edge.

This file exists because its absence was the clearest symptom of writing the
implementation before the tests: every convention main.py upholds becomes
observable to a caller only here, and none of it was checked.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from src import main
from src.config import ConfigurationMissing
from src.scoping import OutOfScope, StoreScope


def _fetch_event(url, op):
    import psycopg
    from psycopg.rows import dict_row

    with psycopg.connect(url, row_factory=dict_row) as c:
        return c.execute(
            "select * from audit_events where operation_id = %s", (op,)
        ).fetchone()


def _count_events(url, op):
    import psycopg

    with psycopg.connect(url) as c:
        return c.execute(
            "select count(*) from audit_events where operation_id = %s", (op,)
        ).fetchone()[0]


@pytest.fixture
def client(migrated_database):
    with TestClient(main.app) as c:
        yield c


@pytest.fixture
def owner_client(migrated_database):
    """A client whose requests carry Owner scope."""
    main.app.dependency_overrides[main.request_scope] = lambda: StoreScope.owner()
    with TestClient(main.app) as c:
        yield c
    main.app.dependency_overrides.clear()


class TestHealth:
    def test_health_reports_database_state(self, client):
        body = client.get("/health").json()
        assert body["status"] == "ok"
        assert body["migrations_applied"] >= 1

    def test_health_time_is_utc(self, client):
        assert client.get("/health").json()["time_utc"].endswith("+00:00")


class TestErrorTranslation:
    """The handlers were never exercised; now they are."""

    def test_out_of_scope_becomes_404_not_403(self, client):
        @main.app.get("/_t_scope")
        def _raise():
            raise OutOfScope("secret-store-id")

        r = client.get("/_t_scope")
        assert r.status_code == 404
        # US-002 AC-02: the response must not confirm the store exists.
        assert "secret-store-id" not in r.text

    def test_configuration_missing_becomes_503(self, client):
        @main.app.get("/_t_config")
        def _raise():
            raise ConfigurationMissing("SOME_KEY", "because")

        r = client.get("/_t_config")
        assert r.status_code == 503
        assert r.json()["detail"]["code"] == "CONFIGURATION_MISSING"


class TestScopingAtTheEdge:
    def test_unauthenticated_caller_sees_no_stores(self, client):
        body = client.get("/stores").json()
        assert body["data"] == []
        assert body["meta"]["count"] == 0

    def test_unknown_and_unauthorised_are_indistinguishable(self, client, committed_store_id):
        """BT-08 at the HTTP layer: identical status and body."""
        existing = client.post(
            f"/stores/{committed_store_id}/notes",
            json={"note": "x"},
            headers={"Idempotency-Key": str(uuid.uuid4())},
        )
        missing = client.post(
            f"/stores/{uuid.uuid4()}/notes",
            json={"note": "x"},
            headers={"Idempotency-Key": str(uuid.uuid4())},
        )
        assert existing.status_code == missing.status_code == 404
        assert existing.json() == missing.json()


class TestWritePathPattern:
    """The pattern every later write endpoint copies."""

    def test_write_requires_an_idempotency_key(self, owner_client, committed_store_id):
        r = owner_client.post(f"/stores/{committed_store_id}/notes", json={"note": "hello"})
        assert r.status_code == 422

    def test_write_records_an_audit_event(self, owner_client, committed_store_id, migrated_database):
        op = str(uuid.uuid4())
        r = owner_client.post(
            f"/stores/{committed_store_id}/notes",
            json={"note": "hello"},
            headers={"Idempotency-Key": op},
        )
        assert r.status_code == 201
        assert r.json()["data"]["replayed"] is False

        row = _fetch_event(migrated_database, op)
        assert row is not None
        assert row["event_type"] == "store_note_added"

    def test_replay_returns_the_original_outcome_once(self, owner_client, committed_store_id, migrated_database):
        """US-014 AC-03: a retry takes effect once and can find what it did."""
        op = str(uuid.uuid4())
        payload = {"note": "hello"}
        headers = {"Idempotency-Key": op}

        first = owner_client.post(f"/stores/{committed_store_id}/notes", json=payload, headers=headers)
        second = owner_client.post(f"/stores/{committed_store_id}/notes", json=payload, headers=headers)

        assert first.status_code == 201
        assert second.status_code == 200
        assert second.json()["data"]["replayed"] is True
        # The replay reports the real event id, not a fabricated one.
        assert second.json()["data"]["event_id"] == first.json()["data"]["event_id"]
        assert uuid.UUID(second.json()["data"]["event_id"]).int != 0

        n = _count_events(migrated_database, op)
        assert n == 1
