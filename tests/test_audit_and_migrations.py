"""Audit writing, idempotency, and the migration runner."""

from __future__ import annotations

import uuid

import psycopg
import pytest

from src.audit import (
    AuditEvent,
    DuplicateOperation,
    find_by_operation,
    new_operation_id,
    record,
)
from src.db.migrate import MigrationError, _reject_transaction_control, discover
from pathlib import Path


class TestAuditIsAppendOnly:
    def test_event_is_recorded(self, conn, store_id):
        op = new_operation_id()
        event_id = record(
            conn,
            AuditEvent(
                event_type="store_viewed",
                entity_type="store",
                entity_id=store_id,
                store_id=store_id,
                operation_id=op,
            ),
        )
        assert event_id is not None
        assert find_by_operation(conn, op)["event_type"] == "store_viewed"

    def test_decimal_is_serialised_exactly_not_as_float(self, conn, store_id):
        from decimal import Decimal

        op = new_operation_id()
        record(
            conn,
            AuditEvent(
                event_type="cost_changed",
                entity_type="order",
                operation_id=op,
                store_id=store_id,
                after_state={"amount": Decimal("4.0950")},
            ),
        )
        stored = find_by_operation(conn, op)["after_state"]
        # A float here would misreport the very figure the trail exists to explain.
        assert stored["amount"] == "4.0950"


class TestOperationIdPreventsDoubleCounting:
    """US-014 AC-03: a replayed write takes effect once."""

    def test_replaying_an_operation_id_is_refused(self, conn, store_id):
        op = new_operation_id()
        event = AuditEvent(
            event_type="store_viewed",
            entity_type="store",
            operation_id=op,
            store_id=store_id,
        )
        record(conn, event)
        with pytest.raises(DuplicateOperation):
            record(conn, event)

    def test_uniqueness_is_enforced_by_the_database(self, conn, store_id):
        # Enforced by a constraint rather than a check-then-insert, so two
        # concurrent retries cannot both pass the check.
        op = new_operation_id()
        record(conn, AuditEvent("e", "store", op, store_id=store_id))
        with pytest.raises(psycopg.errors.UniqueViolation):
            conn.execute(
                "insert into audit_events (operation_id, entity_type, event_type) "
                "values (%s, 'store', 'e')",
                (op,),
            )

    def test_distinct_operations_both_record(self, conn, store_id):
        for _ in range(3):
            record(
                conn,
                AuditEvent("e", "store", new_operation_id(), store_id=store_id),
            )
        n = conn.execute(
            "select count(*) as n from audit_events where store_id = %s", (store_id,)
        ).fetchone()["n"]
        assert n == 3


class TestMigrationRunner:
    def test_migrations_are_discovered_in_order(self):
        names = [p.name for p in discover()]
        assert names == sorted(names)
        assert "001_baseline.sql" in names

    def test_transaction_control_is_rejected(self):
        """The runner owns the transaction; an inner BEGIN breaks its savepoint."""
        for bad in ["begin;", "COMMIT;", "  rollback;", "start transaction;"]:
            with pytest.raises(MigrationError, match="transaction control"):
                _reject_transaction_control(Path("x.sql"), f"create table t();\n{bad}\n")

    def test_normal_migration_is_accepted(self):
        _reject_transaction_control(
            Path("x.sql"), "create table t (id uuid);\n-- a comment\n"
        )

    def test_applied_migrations_are_recorded(self, conn):
        rows = conn.execute("select filename from schema_migrations").fetchall()
        assert "001_baseline.sql" in [r["filename"] for r in rows]


class TestBaselineSchemaConventions:
    """The conventions every later migration inherits."""

    def _column(self, conn, table, column):
        return conn.execute(
            """
            select data_type, column_default, is_nullable
            from information_schema.columns
            where table_name = %s and column_name = %s
            """,
            (table, column),
        ).fetchone()

    def test_currency_has_no_default(self, conn):
        col = self._column(conn, "stores", "reporting_currency_code")
        assert col["column_default"] is None

    def test_timezone_has_no_default(self, conn):
        col = self._column(conn, "stores", "timezone")
        assert col["column_default"] is None

    def test_instants_are_timestamptz(self, conn):
        col = self._column(conn, "audit_events", "created_at")
        assert col["data_type"] == "timestamp with time zone"

    def test_no_floating_point_columns_exist(self, conn):
        rows = conn.execute(
            """
            select table_name, column_name, data_type
            from information_schema.columns
            where table_schema = 'public'
              and data_type in ('double precision', 'real')
            """
        ).fetchall()
        assert rows == []


class TestAppendOnlyIsEnforced:
    """DB-09. Previously the class was named for this and never checked it.

    The triggers live in docs/guardrails/sql/001_invariants.sql, now applied by
    the test fixture -- without that these would pass against a schema with no
    enforcement at all.
    """

    def test_update_is_rejected(self, conn, store_id):
        op = new_operation_id()
        record(conn, AuditEvent("e", "store", op, store_id=store_id))
        with pytest.raises(psycopg.errors.CheckViolation, match="append-only"):
            conn.execute(
                "update audit_events set reason = 'tampered' where operation_id = %s",
                (op,),
            )

    def test_delete_is_rejected(self, conn, store_id):
        op = new_operation_id()
        record(conn, AuditEvent("e", "store", op, store_id=store_id))
        with pytest.raises(psycopg.errors.CheckViolation, match="append-only"):
            conn.execute("delete from audit_events where operation_id = %s", (op,))
