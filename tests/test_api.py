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
    """F-01 (US-001 audit): slice-0 collapsed "I don't know who you are" and
    "I know who you are, and you have no stores" into the same empty list.
    The two tests below pin the two halves apart; if they ever answer the
    same way again, F-01 has regressed.
    """

    def test_unauthenticated_caller_is_refused(self, client):
        r = client.get("/stores")
        assert r.status_code == 401
        assert r.json()["detail"]["code"] == "AUTHENTICATION_REQUIRED"

    def test_authenticated_caller_with_no_assignments_sees_an_empty_list(
        self, client, migrated_database
    ):
        import psycopg
        from psycopg.rows import dict_row

        from src.access import passwords

        password = "scoping-edge-no-assignments-password"
        email = f"scoping-edge-no-assignments-{uuid.uuid4().hex}@example.test"
        with psycopg.connect(migrated_database, row_factory=dict_row) as c:
            c.execute(
                """
                insert into users (email, role, password_hash, mfa_enabled)
                values (%s, 'manager', %s, false)
                """,
                (email, passwords.hash_password(password)),
            )
            c.commit()

        signed_in = client.post(
            "/auth/session", json={"email": email, "password": password}
        )
        assert signed_in.status_code == 201
        token = signed_in.json()["data"]["token"]

        body = client.get(
            "/stores", headers={"Authorization": f"Bearer {token}"}
        ).json()
        assert body["data"] == []
        assert body["meta"]["count"] == 0

    def test_unknown_and_unauthorised_are_indistinguishable(
        self, client, committed_store_id, migrated_database
    ):
        """BT-08 at the HTTP layer, on the write path, as a scoped user: an
        unassigned store and a non-existent one must get identical status
        and body.

        The previous version of this test sent no ``Authorization`` header
        at all, which tested "not authenticated", not "not authorized" --
        the wrong actor for BT-08. Here the caller is a real user assigned
        to a *different* store, so ``committed_store_id`` is genuinely
        out of scope rather than merely unauthenticated.
        """
        import psycopg
        from psycopg.rows import dict_row

        from src.access import passwords

        password = "scoping-edge-bt08-password"
        email = f"scoping-edge-bt08-{uuid.uuid4().hex}@example.test"
        with psycopg.connect(migrated_database, row_factory=dict_row) as c:
            other_store = c.execute(
                """
                insert into stores (name, walmart_store_key, timezone,
                                    reporting_currency_code, managed_from_year,
                                    managed_from_month)
                values ('BT-08 Other Store', %s, 'America/New_York', 'USD', 2026, 7)
                returning id
                """,
                (f"BT08-{uuid.uuid4().hex[:12]}",),
            ).fetchone()
            user = c.execute(
                """
                insert into users (email, role, password_hash, mfa_enabled)
                values (%s, 'manager', %s, false)
                returning id
                """,
                (email, passwords.hash_password(password)),
            ).fetchone()
            c.execute(
                "insert into user_store_assignments (user_id, store_id) values (%s, %s)",
                (user["id"], other_store["id"]),
            )
            c.commit()

        signed_in = client.post(
            "/auth/session", json={"email": email, "password": password}
        )
        assert signed_in.status_code == 201
        token = signed_in.json()["data"]["token"]
        headers = {"Authorization": f"Bearer {token}"}

        existing = client.post(
            f"/stores/{committed_store_id}/notes",
            json={"note": "x"},
            headers={**headers, "Idempotency-Key": str(uuid.uuid4())},
        )
        missing = client.post(
            f"/stores/{uuid.uuid4()}/notes",
            json={"note": "x"},
            headers={**headers, "Idempotency-Key": str(uuid.uuid4())},
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
