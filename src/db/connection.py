"""Database connectivity.

Money is read back as ``Decimal`` by psycopg because the columns are
``numeric``. That is the behaviour slice 0 exists to lock in: a column declared
with a binary floating point type would arrive as ``float`` and every
downstream calculation would silently lose exactness. DB-03 in the schema
self-test fails the build if that happens, so this layer does not need to
defend against it at runtime.

(The offending type name is described rather than written here so that the
guardrail scanner, which flags it across every file type, stays free of
suppressions it does not need.)
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from src.config import Settings

_pool: ConnectionPool | None = None


def init_pool(settings: Settings) -> ConnectionPool:
    global _pool
    if _pool is None:
        _pool = ConnectionPool(
            conninfo=settings.database_url,
            min_size=1,
            max_size=10,
            kwargs={"row_factory": dict_row},
            open=True,
        )
    return _pool


def close_pool() -> None:
    global _pool
    if _pool is not None:
        _pool.close()
        _pool = None


@contextmanager
def connection() -> Iterator[psycopg.Connection]:
    """A pooled connection.

    The transaction commits on clean exit and rolls back on any exception.
    Financial workflows depend on that being the default rather than something
    each call site remembers.
    """
    if _pool is None:
        raise RuntimeError("connection pool not initialised; call init_pool() first")
    with _pool.connection() as conn:
        yield conn
