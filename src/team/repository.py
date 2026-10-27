"""Reads and writes on ``user_store_assignments``.

The table itself (``src/db/migrations/002_access.sql:120-125``) is a bare
composite-primary-key row: ``(user_id, store_id)``. No surrogate key, no
timestamp, no active flag -- presence of the row *is* the assignment, so a
grant is an ``INSERT`` and a revoke is a ``DELETE``, exactly as
``access.service.resolve_scope`` (the one true scope-resolution path) already
assumes when it reads this same table.

Follows the convention ``src/stores/repository.py`` establishes: callers pass
already-validated ids, not caller-supplied column names, and existence
questions are answered by a dedicated query rather than by catching whatever
exception an absent row happens to produce three layers up.
"""

from __future__ import annotations

import uuid
from typing import Any

import psycopg

__all__ = [
    "ASSIGNMENT_PK_CONSTRAINT",
    "list_assignments",
    "assignment_exists",
    "create_assignment",
    "delete_assignment",
]

# The name Postgres assigns a composite PRIMARY KEY declared with no explicit
# name of its own (002_access.sql:124, `primary key (user_id, store_id)`) --
# confirmed against the running schema (see US-003's F-45 for why this must
# never be guessed): `user_store_assignments_pkey`.
ASSIGNMENT_PK_CONSTRAINT = "user_store_assignments_pkey"


def list_assignments(
    conn: psycopg.Connection, *, store_id: uuid.UUID
) -> list[dict[str, Any]]:
    """Every user currently assigned to this store."""
    return conn.execute(
        "select user_id, store_id from user_store_assignments "
        "where store_id = %s order by user_id",
        (store_id,),
    ).fetchall()


def assignment_exists(
    conn: psycopg.Connection, *, user_id: uuid.UUID, store_id: uuid.UUID
) -> bool:
    row = conn.execute(
        "select 1 from user_store_assignments where user_id = %s and store_id = %s",
        (user_id, store_id),
    ).fetchone()
    return row is not None


def create_assignment(
    conn: psycopg.Connection, *, user_id: uuid.UUID, store_id: uuid.UUID
) -> None:
    """Insert one assignment row.

    Raises ``psycopg.errors.UniqueViolation`` (constraint
    ``ASSIGNMENT_PK_CONSTRAINT``) if the pair already exists -- the caller
    (``src.team.service``) is responsible for translating that into a named
    conflict, not this layer.
    """
    conn.execute(
        "insert into user_store_assignments (user_id, store_id) values (%s, %s)",
        (user_id, store_id),
    )


def delete_assignment(
    conn: psycopg.Connection, *, user_id: uuid.UUID, store_id: uuid.UUID
) -> bool:
    """Remove one assignment row. Returns whether a row was actually deleted."""
    cur = conn.execute(
        "delete from user_store_assignments where user_id = %s and store_id = %s",
        (user_id, store_id),
    )
    return cur.rowcount > 0
