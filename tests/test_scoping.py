"""Store scoping (access-v1, US-002 AC-02)."""

from __future__ import annotations

import uuid

import pytest

from src.scoping import OutOfScope, StoreScope
from src.stores import repository

A = uuid.uuid4()
B = uuid.uuid4()


class TestDefaultIsDenial:
    def test_none_scope_permits_nothing(self):
        assert StoreScope.none().permits(A) is False

    def test_empty_assignment_yields_false_not_true(self):
        """The failure mode here is total exposure, so it gets its own test."""
        sql, _ = StoreScope.assigned(None, []).sql_filter()
        assert sql == "false"

    def test_owner_is_a_flag_not_an_empty_list(self):
        # If 'all stores' were modelled as an empty id list, a scope that
        # failed to load its assignments would be indistinguishable from the
        # Owner. It is a separate flag for exactly that reason.
        assert StoreScope.owner().all_stores is True
        assert StoreScope.assigned(None, []).all_stores is False


class TestScopeFilters:
    def test_owner_filter_is_always_composable(self):
        sql, params = StoreScope.owner().sql_filter()
        assert sql == "true"
        assert params == {}

    def test_assigned_filter_binds_parameters(self):
        sql, params = StoreScope.assigned(None, [A, B]).sql_filter()
        assert "%(scope_store_ids)s" in sql
        assert set(params["scope_store_ids"]) == {A, B}

    def test_filter_column_is_configurable(self):
        sql, _ = StoreScope.assigned(None, [A]).sql_filter("id")
        assert sql.startswith("id = ")

    def test_require_raises_for_unscoped_store(self):
        with pytest.raises(OutOfScope):
            StoreScope.assigned(None, [A]).require(B)


class TestUnauthorisedIsIndistinguishableFromMissing:
    """US-002 AC-02.

    Returning 403 for a store that exists and 404 for one that does not
    confirms the existence of stores the caller may not see.
    """

    def test_existing_but_unscoped_store_raises_out_of_scope(self, conn, store_id):
        with pytest.raises(OutOfScope):
            repository.get_store(conn, StoreScope.none(), store_id)

    def test_nonexistent_store_raises_the_same_error(self, conn):
        with pytest.raises(OutOfScope):
            repository.get_store(conn, StoreScope.owner(), uuid.uuid4())

    def test_both_cases_raise_the_same_type(self, conn, store_id):
        unauthorised = nonexistent = None
        try:
            repository.get_store(conn, StoreScope.none(), store_id)
        except Exception as exc:
            unauthorised = type(exc)
        try:
            repository.get_store(conn, StoreScope.owner(), uuid.uuid4())
        except Exception as exc:
            nonexistent = type(exc)
        assert unauthorised is nonexistent


class TestScopedQueries:
    def test_owner_sees_the_store(self, conn, store_id):
        rows = repository.list_stores(conn, StoreScope.owner())
        assert store_id in [r["id"] for r in rows]

    def test_unscoped_actor_sees_nothing(self, conn, store_id):
        assert repository.list_stores(conn, StoreScope.none()) == []

    def test_assigned_actor_sees_only_their_store(self, conn, store_id):
        other = conn.execute(
            """insert into stores (name, walmart_store_key)
               values ('Other', 'TEST-002') returning id"""
        ).fetchone()["id"]

        rows = repository.list_stores(
            conn, StoreScope.assigned(None, [store_id]), include_inactive=True
        )
        ids = [r["id"] for r in rows]
        assert store_id in ids
        assert other not in ids


class TestActivationBlockers:
    """US-003 AC-02: missing configuration is named, not guessed."""

    def test_fully_configured_store_has_no_blockers(self, conn, store_id):
        store = repository.get_store(conn, StoreScope.owner(), store_id)
        assert repository.activation_blockers(store) == []

    def test_missing_configuration_is_named(self, conn):
        conn.execute(
            """insert into stores (name, walmart_store_key)
               values ('Bare', 'TEST-003')"""
        )
        store = [
            s for s in repository.list_stores(conn, StoreScope.owner())
            if s["walmart_store_key"] == "TEST-003"
        ][0]
        blockers = repository.activation_blockers(store)
        assert set(blockers) == {"timezone", "reporting_currency_code", "managed_from"}
