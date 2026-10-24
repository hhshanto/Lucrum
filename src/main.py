"""Application entry point.

US-001 wires real authentication in: every request's store scope is resolved
from a server-side session, never from a placeholder. Everything else here
still follows the slice 0 pattern -- scope checked before any read or write,
``operation_id`` required on mutations, the audit event in the same
transaction as the change it describes.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from src import audit
from src.access import service as access_service
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


def _bearer_token(header: str) -> str:
    """Extract the token from an ``Authorization: Bearer <token>`` header.

    Anything else -- wrong scheme, missing token -- is a credential that was
    offered and is bad, which ``request_scope`` now treats exactly like no
    credential at all (F-01, US-001 audit): both raise ``SessionInvalid``,
    never a disclosure of which part was wrong.
    """
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise access_service.SessionInvalid("not a bearer token")
    return token


def request_scope(request: Request) -> StoreScope:
    """Resolve the caller's store scope from a real, live session lookup.

    F-01 (US-001 audit): this used to return ``StoreScope.none()`` for a
    request with no ``Authorization`` header at all -- byte-identical to the
    scope of an authenticated Manager with zero store assignments. That
    collapsed two different answers ("I don't know who you are" and "you
    have no stores") into one value, and it meant a protected endpoint had no
    way to *require* authentication: a missing credential reached the same
    code path as a present-but-empty one and so never got the "please sign
    in" response AC-02 requires.

    Every caller of this dependency protects something that needs a known
    identity, so a missing credential is now exactly as invalid as a bad one
    -- both raise ``SessionInvalid``, translated to 401 by the handler below.
    They are deliberately not told apart from each other either: "you never
    signed in" and "your session is no longer valid" get the same prompt, for
    the same non-disclosure reason ``SessionInvalid`` already covers unknown,
    expired and revoked tokens as one case.

    Scope is resolved fresh on every call rather than cached on the session:
    AC-03 requires that authorization withdrawn after a session was issued
    is re-checked on the very next request.
    """
    header = request.headers.get("Authorization")
    if not header:
        raise access_service.SessionInvalid("no credential offered")

    token = _bearer_token(header)
    with connection() as conn:
        session = access_service.authenticate(conn, token)
        return access_service.resolve_scope(conn, session.user_id)


@app.exception_handler(OutOfScope)
async def out_of_scope_handler(request: Request, exc: OutOfScope):
    # 404, never 403. US-002 AC-02: an unauthorised store must be
    # indistinguishable from one that does not exist.
    raise HTTPException(status_code=404, detail="Not found")


@app.exception_handler(access_service.SessionInvalid)
async def session_invalid_handler(request: Request, exc: access_service.SessionInvalid):
    # One body for every reason a session fails to validate -- unknown,
    # idle-expired, absolute-expired, revoked -- so the response never
    # discloses which. AC-02: prompts the caller to authenticate again.
    raise HTTPException(
        status_code=401,
        detail={
            "code": "AUTHENTICATION_REQUIRED",
            "message": "Sign in again to continue.",
        },
    )


# F-07 (US-001 audit): one fixed public body per exception *type*, never the
# raw exception text. ``str(exc)`` used to be handed straight to the caller,
# so (for example) the MfaRequired branch disclosed -- in its very message --
# that the account exists and the password was correct for a privileged
# role. ``session_invalid_handler`` above already gets this right for four
# internal causes; this is the same discipline applied here. MfaRequired's
# code is deliberately still distinguishable from the rest: a client in a
# legitimate two-step sign-in needs to know to prompt for a code, and that is
# the only additional fact disclosed -- never a reason string explaining why
# a *specific* attempt failed.
_AUTH_FAILURE_RESPONSES: dict[type[access_service.AuthenticationFailed], tuple[str, str]] = {
    access_service.MfaRequired: (
        "MFA_REQUIRED",
        "Enter your authentication code to continue.",
    ),
    access_service.AccountLocked: (
        "ACCOUNT_LOCKED",
        "This account is temporarily locked. Try again later.",
    ),
}
_DEFAULT_AUTH_FAILURE = (
    "AUTHENTICATION_FAILED",
    "Invalid email, password, or authentication code.",
)


@app.exception_handler(access_service.AuthenticationFailed)
async def authentication_failed_handler(
    request: Request, exc: access_service.AuthenticationFailed
):
    # Catches InvalidCredentials, AccountLocked, MfaRequired -- anything
    # raised directly by sign_in that SessionInvalid's more specific handler
    # above does not already cover.
    code, message = _AUTH_FAILURE_RESPONSES.get(type(exc), _DEFAULT_AUTH_FAILURE)
    raise HTTPException(
        status_code=401,
        detail={"code": code, "message": message},
    )


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, exc: RequestValidationError):
    """Strip ``input`` out of every validation error before it reaches the
    caller.

    F-05 (US-001 audit): FastAPI/Pydantic v2 attach an ``input`` key to each
    error entry, and for a ``missing`` error on a required field, ``input``
    is the *parent object* -- the entire request body. Omit ``email`` from a
    sign-in request while still sending ``password`` and the default 422
    body contains the caller's actual password: SC-13 ("no password, token,
    or session identifier in ... error messages") forbids exactly this, and
    it is a framework default rather than anything this codebase wrote, which
    is why the scanner cannot see it. Every entry is rebuilt here without
    ``input`` -- never passed through whole -- so the leak cannot reopen the
    next time a validated field happens to be named ``password``.
    """
    errors = []
    for err in exc.errors():
        sanitized = {k: v for k, v in err.items() if k != "input"}
        ctx = sanitized.get("ctx")
        if isinstance(ctx, dict):
            sanitized["ctx"] = {k: v for k, v in ctx.items() if k != "input"}
        errors.append(sanitized)
    return JSONResponse(
        status_code=422,
        content=jsonable_encoder(
            {"detail": {"code": "VALIDATION_ERROR", "errors": errors}},
            exclude_none=False,
        ),
    )


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


class SignInRequest(BaseModel):
    email: str
    password: str
    totp_code: str | None = None


@app.post("/auth/session", status_code=201)
def create_session(body: SignInRequest) -> dict[str, object]:
    """Sign in. UI-01.

    Deliberately no ``Idempotency-Key``: access-v1 rotates the session
    identifier on every sign-in, so a replayed sign-in must not hand back a
    previous token -- there is nothing here for idempotency to preserve.

    A failed attempt still needs its lockout bookkeeping (the failed-attempt
    count, and the lock itself once the threshold is reached) to survive even
    though the attempt as a whole raises. ``connection()`` rolls back on any
    exception, which would otherwise erase that bookkeeping along with the
    rejected sign-in -- so the partial state is committed explicitly before
    the exception is allowed to propagate to the client as 401.
    """
    with connection() as conn:
        try:
            result = access_service.sign_in(
                conn,
                email=body.email,
                password=body.password,
                totp_code=body.totp_code,
            )
        except access_service.AuthenticationFailed:
            conn.commit()
            raise

    return {
        "data": {"token": result.token},
        "meta": {},
        "errors": [],
    }


@app.delete("/auth/session", status_code=204)
def delete_session(request: Request) -> Response:
    """Sign out. Revokes the session server-side (AC-04): a revoked token is
    rejected immediately on its next use, not merely dropped by the client."""
    header = request.headers.get("Authorization")
    if not header:
        raise access_service.SessionInvalid("no session to sign out of")
    token = _bearer_token(header)

    with connection() as conn:
        access_service.sign_out(conn, token)

    return Response(status_code=204)


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

    An authenticated caller with no assignments sees an empty list -- the
    correct answer for "you have no stores". An unauthenticated caller now
    gets 401 from ``request_scope`` before this function body ever runs
    (F-01, US-001 audit): "I don't know who you are" is a different answer
    and must not collapse into the same empty list.
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


@app.get("/stores/{store_id}")
def get_store_detail(
    store_id: uuid.UUID, scope: StoreScope = Depends(request_scope)
) -> dict[str, object]:
    """One store, if it is in the caller's scope.

    AC-03: authorization is re-checked on every call -- ``scope`` is resolved
    fresh per request, never snapshotted at sign-in -- so a store reached a
    moment ago stops being reachable the instant the assignment is withdrawn.
    An unassigned store and a non-existent one both raise ``OutOfScope``,
    translated by the handler above to an identical 404.
    """
    with connection() as conn:
        store = repository.get_store(conn, scope, store_id)

    return {
        "data": {
            "id": str(store["id"]),
            "name": store["name"],
            "walmart_store_key": store["walmart_store_key"],
            "timezone": store["timezone"],
            "currency_code": store["reporting_currency_code"],
            "activation_blockers": repository.activation_blockers(store),
        },
        "meta": {},
        "errors": [],
    }
