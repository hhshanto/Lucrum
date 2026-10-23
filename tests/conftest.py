"""Test fixtures.

Database tests run against a real Postgres rather than a mock. The invariants
this project cares about — append-only triggers, unique constraints doing
idempotency work, scope filters composed into SQL — live in the database. A
mock would assert that my mock behaves as I wrote it.

Each test runs inside a transaction that is rolled back, so tests are
order-independent and leave nothing behind.
"""

from __future__ import annotations

import os
from pathlib import Path

import psycopg
import pytest
from dotenv import load_dotenv
from psycopg.rows import dict_row

from src.db.migrate import migrate

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Without this the database tests skip silently, which reads as a green run
# while the half of the suite that touches real constraints never executed.
load_dotenv(PROJECT_ROOT / ".env")


GUARDRAIL_SQL = PROJECT_ROOT / "docs" / "guardrails" / "sql" / "001_invariants.sql"


def _database_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if url:
        return url
    # In CI a missing database is a failure, never a skip. "122 passed" and
    # "40 passed, 82 skipped" look alike at a glance, and the skipped ones are
    # exactly those that exercise real constraints.
    if os.environ.get("CI"):
        pytest.fail("DATABASE_URL is required in CI; the database tests must run.")
    pytest.skip(
        "DATABASE_URL not set. Run 'docker compose up -d' and copy .env.example "
        "to .env -- the constraint-level tests are being skipped."
    )


@pytest.fixture(scope="session")
def migrated_database() -> str:
    url = _database_url()
    with psycopg.connect(url, row_factory=dict_row) as conn:
        migrate(conn)
        # Apply the invariant DDL too. Without it the append-only triggers do
        # not exist during pytest, so a test named for DB-09 would pass against
        # a schema that does not enforce it.
        conn.execute(GUARDRAIL_SQL.read_text(encoding="utf-8"))
        conn.commit()
    return url


@pytest.fixture
def conn(migrated_database: str):
    """A connection whose work is always rolled back."""
    with psycopg.connect(migrated_database, row_factory=dict_row) as c:
        tx = c.transaction(force_rollback=True)
        with tx:
            yield c


@pytest.fixture
def store_id(conn) -> str:
    row = conn.execute(
        """
        insert into stores (name, walmart_store_key, timezone,
                            reporting_currency_code, managed_from_year,
                            managed_from_month)
        values ('Test Store', 'TEST-001', 'America/New_York', 'USD', 2026, 7)
        returning id
        """
    ).fetchone()
    return row["id"]


@pytest.fixture
def committed_store_id(migrated_database: str) -> str:
    """A store visible to other connections.

    The API under test uses its own pooled connection, so it cannot see rows
    created inside the rolled-back `conn` fixture. This one commits.

    Nothing is cleaned up afterwards: audit_events is append-only by design and
    refuses DELETE, which is the invariant working rather than a problem to
    engineer around. The test database is disposable.
    """
    import uuid as _uuid

    with psycopg.connect(migrated_database, row_factory=dict_row) as c:
        row = c.execute(
            """
            insert into stores (name, walmart_store_key, timezone,
                                reporting_currency_code, managed_from_year,
                                managed_from_month)
            values ('API Test Store', %s, 'America/New_York', 'USD', 2026, 7)
            returning id
            """,
            (f"APITEST-{_uuid.uuid4().hex[:12]}",),
        ).fetchone()
        c.commit()
        return row["id"]
