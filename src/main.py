"""Application entry point.

Slice 0: a walking skeleton. Two routes, enough to prove the whole stack
connects -- FastAPI, the pool, a migrated schema, store scoping, and the audit
writer -- and deliberately no features.

Authentication is slice 1. Until it exists, the request scope is
``StoreScope.none()``: no stores, nothing visible. The safe default is chosen
deliberately rather than leaving scoping unenforced and "adding auth later",
which is how an endpoint ships without it.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response
from pydantic import BaseModel, Field

from src import audit
from src.clock import utc_now
from src.config import ConfigurationMissing, load_settings
from src.db.connection import close_pool, connection, init_pool
from src.scoping import OutOfScope, StoreScope
from src.stores import repository


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = load_settings()
    init_pool(settings)
    app.state.settings = settings
    try:
        yield
    finally:
        close_pool()


app = FastAPI(
    title="Lucrum — Walmart Store Manager",
    version="0.1.0",
    lifespan=lifespan,
)


def request_scope(request: Request) -> StoreScope:
    """Resolve the caller's store scope.

    Slice 1 replaces this with a real session lookup. Until then it denies
    everything. A permissive placeholder would make every endpoint built on top
    of it appear to work while being unprotected.
    """
    return StoreScope.none()


@app.exception_handler(OutOfScope)
async def out_of_scope_handler(request: Request, exc: OutOfScope):
    # 404, never 403. US-002 AC-02: an unauthorised store must be
    # indistinguishable from one that does not exist.
    raise HTTPException(status_code=404, detail="Not found")


@app.exception_handler(ConfigurationMissing)
async def configuration_missing_handler(request: Request, exc: ConfigurationMissing):
    raise HTTPException(
        status_code=503,
        detail={"code": "CONFIGURATION_MISSING", "key": exc.key, "message": str(exc)},
    )


@app.get("/health")
def health() -> dict[str, object]:
    """Liveness plus real database connectivity.

    Runs a query rather than reporting the pool's opinion of itself. A health
    check that does not touch the database reports healthy while the database
    is unreachable.
    """
    with connection() as conn:
        row = conn.execute("select 1 as ok").fetchone()
        migrations = conn.execute(
            "select count(*) as n from schema_migrations"
        ).fetchone()
    return {
        "status": "ok" if row["ok"] == 1 else "degraded",
        "migrations_applied": migrations["n"],
        "time_utc": utc_now().isoformat(),
    }


class StoreNote(BaseModel):
    """A trivial write, existing to prove the write pattern end to end."""

    note: str = Field(min_length=1, max_length=500)
    reason: str | None = None


@app.post("/stores/{store_id}/notes", status_code=201)
def add_store_note(
    store_id: uuid.UUID,
    body: StoreNote,
    response: Response,
    operation_id: str = Header(alias="Idempotency-Key"),
    scope: StoreScope = Depends(request_scope),
) -> dict[str, object]:
    """Record a note against a store.

    Slice 0's worked example of a write. It carries nothing of business value
    and exists so that every write endpoint in slices 1 onward has a correct
    pattern to copy rather than inventing one:

    * scope is checked before anything is read or written,
    * ``operation_id`` arrives from the caller and is required, not generated
      server-side -- a server-generated id is different on every retry and so
      guarantees nothing,
    * the audit event is written in the **same transaction** as the change, so
      it cannot survive a rollback and describe something that never happened,
    * a replay returns the original outcome with 200 instead of acting twice.
    """
    with connection() as conn:
        store = repository.get_store(conn, scope, store_id)

        try:
            event_id = audit.record(
                conn,
                audit.AuditEvent(
                    event_type="store_note_added",
                    entity_type="store",
                    entity_id=store["id"],
                    store_id=store["id"],
                    operation_id=operation_id,
                    actor_id=scope.actor_id,
                    after_state={"note": body.note},
                    reason=body.reason,
                ),
            )
        except audit.DuplicateOperation as replay:
            # 200, not 201: nothing was created on this call. The status is
            # part of the idempotency contract, not decoration.
            response.status_code = 200
            return {
                "data": {"event_id": str(replay.existing_id), "replayed": True},
                "meta": {"operation_id": operation_id},
                "errors": [],
            }

    return {
        "data": {"event_id": str(event_id), "replayed": False},
        "meta": {"operation_id": operation_id},
        "errors": [],
    }


@app.get("/stores")
def list_stores(scope: StoreScope = Depends(request_scope)) -> dict[str, object]:
    """Stores within the caller's scope.

    Returns real rows from a migrated database -- the slice 0 done criterion.
    With scoping unresolved until slice 1 this is an empty list, which is the
    correct answer for a caller with no assignments.
    """
    with connection() as conn:
        rows = repository.list_stores(conn, scope)

    return {
        "data": [
            {
                "id": str(r["id"]),
                "name": r["name"],
                "walmart_store_key": r["walmart_store_key"],
                "timezone": r["timezone"],
                "currency_code": r["reporting_currency_code"],
                "activation_blockers": repository.activation_blockers(r),
            }
            for r in rows
        ],
        "meta": {"count": len(rows)},
        "errors": [],
    }
