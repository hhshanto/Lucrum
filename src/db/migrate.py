"""Migration runner for plain ``.sql`` files.

Raw SQL rather than an ORM migration DSL, because the schema depends on
constructs an ORM tends to abstract away or generate incorrectly: append-only
triggers, partial unique indexes, closure-history guards, per-store composite
uniqueness. The guardrail DDL in ``docs/guardrails/sql/`` is also plain SQL and
composes with these directly.

Rules:

* Files are applied in filename order and recorded, so each runs exactly once.
* A file runs inside a transaction. A failure leaves nothing half-applied.
* An applied file is checksummed. Editing a migration that has already run is
  an error rather than a silent divergence between environments -- the usual
  way a schema drifts is someone "just fixing" an old migration locally.
* Migrations are additive. Rewriting history is not a migration strategy.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

import psycopg

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"

_TRACKING_TABLE = """
create table if not exists schema_migrations (
  filename    text        primary key,
  checksum    text        not null,
  applied_at  timestamptz not null default now()
)
"""


class MigrationError(RuntimeError):
    pass


def _checksum(text: str) -> str:
    # Normalise line endings so a Windows checkout and a Linux CI runner agree.
    return hashlib.sha256(text.replace("\r\n", "\n").encode("utf-8")).hexdigest()


_TXN_CONTROL = re.compile(
    r"(?mi)^\s*(begin|commit|rollback|start\s+transaction)\s*;",
)


def _reject_transaction_control(path: Path, sql: str) -> None:
    """Migrations must not manage their own transaction.

    The runner wraps each file, so an inner BEGIN/COMMIT breaks the savepoint
    the wrapper relies on. Rejecting it here turns a confusing runtime failure
    ("savepoint _pg3_1 does not exist") into a clear message at the point of
    the mistake.
    """
    found = _TXN_CONTROL.search(sql)
    if found:
        line = sql[: found.start()].count("\n") + 1
        raise MigrationError(
            f"{path.name}:{line} contains explicit transaction control "
            f"({found.group(1).upper()}). The runner wraps every migration in a "
            f"transaction -- remove it."
        )


def discover(directory: Path = MIGRATIONS_DIR) -> list[Path]:
    return sorted(p for p in directory.glob("*.sql") if p.is_file())


def applied(conn: psycopg.Connection) -> dict[str, str]:
    conn.execute(_TRACKING_TABLE)
    rows = conn.execute("select filename, checksum from schema_migrations").fetchall()
    return {r["filename"]: r["checksum"] for r in rows}


def migrate(conn: psycopg.Connection, directory: Path = MIGRATIONS_DIR) -> list[str]:
    """Apply pending migrations. Returns the filenames applied."""
    already = applied(conn)
    run: list[str] = []

    for path in discover(directory):
        sql = path.read_text(encoding="utf-8")
        _reject_transaction_control(path, sql)
        digest = _checksum(sql)

        if path.name in already:
            if already[path.name] != digest:
                raise MigrationError(
                    f"{path.name} has already been applied but its contents have "
                    f"changed. Migrations are immutable once applied -- add a new "
                    f"one instead. Editing this file would leave environments "
                    f"silently out of step."
                )
            continue

        with conn.transaction():
            conn.execute(sql)
            conn.execute(
                "insert into schema_migrations (filename, checksum) values (%s, %s)",
                (path.name, digest),
            )
        run.append(path.name)

    return run


def main() -> int:
    from src.config import load_settings

    settings = load_settings()
    with psycopg.connect(settings.database_url, row_factory=psycopg.rows.dict_row) as conn:
        done = migrate(conn)
    if done:
        for name in done:
            print(f"applied {name}")
    else:
        print("no pending migrations")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
