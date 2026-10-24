"""User reads.

SC-13 / access-v1: ``password_hash`` and ``mfa_secret`` are excluded at the
query itself, not filtered out of the row afterward. Excluding them here
means every caller gets a safe row by construction; filtering later means
every *new* caller has to remember to.

F-11 (US-001 audit): that claim used to be true of ``get_user`` and false of
the module as a whole, because ``service.sign_in`` opened its own ad hoc
``select`` against ``users`` to reach ``password_hash`` and ``mfa_secret`` --
a second, unaudited path to the same sensitive columns. ``get_user_credentials``
below is now the *only* query anywhere in this codebase allowed to read them,
named narrowly enough that grepping for it finds every caller.
"""

from __future__ import annotations

import uuid
from typing import Any

import psycopg

__all__ = ["get_user", "get_user_credentials"]

# Deliberately not `select *`. Adding a sensitive column to `users` later
# must not silently start returning it here.
_COLUMNS = """
    id, email, role, mfa_enabled, failed_sign_in_count, locked_until,
    last_sign_in_at, is_active, created_at, updated_at
"""

# The one and only set of columns anywhere permitted to carry password_hash
# or mfa_secret out of the database. Used exclusively by sign-in.
_CREDENTIAL_COLUMNS = """
    id, role, password_hash, mfa_secret, mfa_enabled,
    failed_sign_in_count, locked_until, is_active
"""


def get_user(conn: psycopg.Connection, user_id: uuid.UUID) -> dict[str, Any] | None:
    """Fetch a user's public fields, or ``None`` if no such user exists."""
    return conn.execute(
        f"select {_COLUMNS} from users where id = %s", (user_id,)
    ).fetchone()


def get_user_credentials(
    conn: psycopg.Connection, email: str
) -> dict[str, Any] | None:
    """The one query in this codebase permitted to read ``password_hash`` and
    ``mfa_secret``. Only ``access.service.sign_in`` may call this.

    Deliberately not filtered by ``is_active`` here -- F-04 (US-001 audit):
    sign_in needs to tell an inactive account apart from no account at all
    *internally*, in order to log the real reason, even though it must answer
    the caller identically either way. Folding the filter into the query
    would erase that distinction before sign_in ever saw it.
    """
    return conn.execute(
        f"select {_CREDENTIAL_COLUMNS} from users where email = %s", (email,)
    ).fetchone()
