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
from dataclasses import dataclass

from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, model_validator

from src import audit
from src.access import repository as access_repository
from src.access import service as access_service
from src.clock import utc_now
from src.config import ConfigurationMissing, load_settings
from src.db.connection import close_pool, connection, init_pool
from src.scoping import Forbidden, OutOfScope, StoreScope
from src.stores import repository, service as stores_service


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


def _authenticate_request(
    conn, request: Request
) -> access_service.AuthenticatedSession:
    """The authentication gate shared by every dependency below (F-35, US-003
    audit): parse the header, extract the bearer token, validate the
    session. ``request_scope`` and ``request_actor`` used to each carry
    their own verbatim copy of these three lines -- Invariant #5 (one
    implementation) does not only apply to money; two copies of an
    authentication gate diverge exactly as silently, the moment one of them
    picks up a fix (a rate limiter, F-19's ``FOR UPDATE``) and the other
    does not.

    A missing or malformed credential is exactly as invalid as a bad one --
    see ``request_scope``'s docstring below for why that collapsing is
    deliberate.
    """
    header = request.headers.get("Authorization")
    if not header:
        raise access_service.SessionInvalid("no credential offered")

    token = _bearer_token(header)
    return access_service.authenticate(conn, token)


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
    with connection() as conn:
        session = _authenticate_request(conn, request)
        return access_service.resolve_scope(conn, session.user_id)


@dataclass(frozen=True)
class Actor:
    """An authenticated caller's scope *and* role.

    ``request_scope`` alone is enough for every endpoint that only needs to
    answer "which stores" (US-001, US-002). US-003 AC-03 needs a second,
    independent answer -- "is this role even allowed to attempt this
    action" -- which ``StoreScope`` carries no information about by design
    (scope and role are different questions; see
    ``src/stores/service.py``). Resolved from one authenticated session
    rather than two, so a role-checked endpoint does not validate the same
    token twice.
    """

    scope: StoreScope
    role: str


def request_actor(request: Request) -> Actor:
    """Like ``request_scope``, but also resolves the caller's role.

    Only used by endpoints that must apply a capability-matrix check
    (BR-11) in addition to store scope -- today, store creation and store
    identity changes (US-003 AC-03).
    """
    with connection() as conn:
        session = _authenticate_request(conn, request)
        scope = access_service.resolve_scope(conn, session.user_id)
        user = access_repository.get_user(conn, session.user_id)
        if user is None:  # pragma: no cover - resolve_scope already refused this
            raise access_service.SessionInvalid("the signed-in user no longer exists")
        return Actor(scope=scope, role=user["role"])


@app.exception_handler(OutOfScope)
async def out_of_scope_handler(request: Request, exc: OutOfScope):
    # 404, never 403. US-002 AC-02: an unauthorised store must be
    # indistinguishable from one that does not exist.
    raise HTTPException(status_code=404, detail="Not found")


@app.exception_handler(Forbidden)
async def forbidden_handler(request: Request, exc: Forbidden):
    # 403, never 404. US-003 AC-03: a role rejection is a different answer
    # from "not found" -- the actor already knows the resource exists (they
    # may even be assigned to it), so there is nothing left to protect by
    # disguising the rejection as a 404. str(exc) is deliberately not
    # surfaced: it is an internal note for whoever reads the exception, not
    # a public message.
    raise HTTPException(
        status_code=403,
        detail={"code": "FORBIDDEN", "message": "Not permitted."},
    )


@app.exception_handler(audit.ReplayConflict)
async def replay_conflict_handler(request: Request, exc: audit.ReplayConflict):
    # F-31 (US-003 audit): an Idempotency-Key reused against a different
    # entity or a different kind of write is a genuine conflict, never a
    # replay -- 409, never 200/201. Returning either of those would report
    # someone else's write as this call's own success.
    raise HTTPException(
        status_code=409,
        detail={
            "code": "OPERATION_ID_CONFLICT",
            "message": "This Idempotency-Key was already used for a different operation.",
        },
    )


@app.exception_handler(stores_service.DuplicateStoreKey)
async def duplicate_store_key_handler(
    request: Request, exc: stores_service.DuplicateStoreKey
):
    # F-45 (US-003 audit): a conflicting external identifier is a
    # configuration problem (AC-02's own principle), named rather than
    # surfaced as a generic 500.
    raise HTTPException(
        status_code=409,
        detail={
            "code": "DUPLICATE_WALMART_STORE_KEY",
            "field": "walmart_store_key",
            "message": "A store with this walmart_store_key is already registered.",
        },
    )


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
    * a replay returns the original outcome with 200 instead of acting twice,
    * an ``Idempotency-Key`` reused against a *different* write (a different
      store, a different endpoint entirely) is a 409 conflict, never a 200 --
      ``audit.check_replay_target`` is what makes that true here; copy it
      along with everything else (F-46, US-003 audit: this endpoint did not
      carry it even after the same commit introduced it for the two newer
      ones, which is exactly backwards for a function that advertises
      itself as the pattern to copy).
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
            audit.check_replay_target(
                replay.existing_event,
                event_type="store_note_added",
                entity_type="store",
                entity_id=store["id"],
            )
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


def _store_body(store: dict[str, object]) -> dict[str, object]:
    """The public representation of a store, shared by every endpoint that
    returns one. One place composing ``activation_blockers`` onto a row
    means list, detail, create and rename can never quietly disagree about
    its shape (the exact asymmetry F-21/B-7 found between two hand-written
    copies of this same dict)."""
    return {
        "id": str(store["id"]),
        "name": store["name"],
        "walmart_store_key": store["walmart_store_key"],
        "timezone": store["timezone"],
        "currency_code": store["reporting_currency_code"],
        "managed_from_year": store["managed_from_year"],
        "managed_from_month": store["managed_from_month"],
        "activation_blockers": repository.activation_blockers(store),
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
        "data": [_store_body(r) for r in rows],
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
        "data": _store_body(store),
        "meta": {},
        "errors": [],
    }


class StoreCreateRequest(BaseModel):
    """US-003 AC-01. Field names follow the column names in
    ``001_baseline.sql`` with one adjustment -- ``currency_code`` rather than
    ``reporting_currency_code`` -- to match the key this API's existing
    ``GET /stores`` responses already use (see the test file's docstring;
    neither ``walmart-user-stories.md`` nor the API document specifies a
    request body shape for this endpoint).

    ``timezone`` and ``currency_code`` are optional with no default of any
    kind (US-003 AC-02 / period-v1 / BR-12): a store may be created before
    either is known, and the absent value is stored as ``NULL``, never
    guessed. ``walmart_store_key`` is a required, ``NOT NULL`` database
    column (001_baseline.sql:29) and is not one of AC-02's two named
    blockers -- its absence is a plain validation error, not an activation
    blocker.

    F-44 (US-003 audit): ``extra="forbid"`` -- Pydantic's default is
    ``extra="ignore"``, which silently drops any field it does not
    recognise. Without this, a caller who sent ``reporting_currency_code``
    (the real column name, per database-schema.md:191 and this module's own
    ``_IDENTITY_COLUMNS``) instead of ``currency_code`` got back 201 with
    the field quietly discarded and the column left NULL -- a confident
    success report for a request that did not do what it said.
    """

    model_config = ConfigDict(extra="forbid")

    # F-36 (US-003 audit): max_length is deliberately NOT set here. Both
    # columns are unconstrained `text` (001_baseline.sql) -- a length cap
    # invented at this layer cannot be cited to any document, and it was
    # asymmetric with update besides (rejected at create, accepted at
    # rename). min_length=1 stays: an empty name/key is nonsensical and
    # update now enforces the same floor (StoreUpdateRequest below).
    name: str = Field(min_length=1)
    walmart_store_key: str = Field(min_length=1)
    timezone: str | None = None
    currency_code: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    managed_from_year: int | None = None
    managed_from_month: int | None = Field(default=None, ge=1, le=12)

    @model_validator(mode="after")
    def _managed_from_paired(self) -> "StoreCreateRequest":
        # Mirrors stores_managed_from_paired (001_baseline.sql): reported as
        # a clear 422 here rather than as a database IntegrityError.
        if (self.managed_from_year is None) != (self.managed_from_month is None):
            raise ValueError(
                "managed_from_year and managed_from_month must both be "
                "provided, or both omitted"
            )
        return self


@app.post("/stores", status_code=201)
def create_store(
    body: StoreCreateRequest,
    response: Response,
    operation_id: str = Header(alias="Idempotency-Key"),
    actor: Actor = Depends(request_actor),
) -> dict[str, object]:
    """Register a store (US-003 AC-01). Owner only (BR-11, AC-03).

    Follows the write pattern established by ``add_store_note``:
    ``operation_id`` is required from the caller, not generated here; the
    audit event is written in the same transaction as the store row it
    describes; a replay reports the original store rather than creating a
    second one (``src/stores/service.py`` carries the mechanics of how that
    is made atomic for a row-creating write specifically).
    """
    with connection() as conn:
        store, replayed = stores_service.create_store(
            conn,
            actor.scope,
            actor_id=actor.scope.actor_id,
            role=actor.role,
            fields={
                "name": body.name,
                "walmart_store_key": body.walmart_store_key,
                "timezone": body.timezone,
                "reporting_currency_code": body.currency_code,
                "managed_from_year": body.managed_from_year,
                "managed_from_month": body.managed_from_month,
            },
            operation_id=operation_id,
        )

    if replayed:
        # Nothing was created on this call -- same convention as
        # add_store_note's replay branch.
        response.status_code = 200

    return {
        "data": _store_body(store),
        "meta": {"operation_id": operation_id, "replayed": replayed},
        "errors": [],
    }


class StoreUpdateRequest(BaseModel):
    """US-003 AC-04 / BR-11 "change store settings". Every field is
    optional -- only the ones actually present in the request body are
    changed (``model_dump(exclude_unset=True)`` below), so a rename that
    supplies only ``name`` cannot disturb ``timezone`` or any other field by
    merely defaulting it to ``None``.

    F-32 (US-003 audit): validation here now matches create exactly for
    every field that *can* be set to a value -- ``name`` gets the same
    ``min_length=1`` floor create already has (an empty-string rename was
    previously accepted; a NULL rename was previously accepted too, and
    ``name`` is ``NOT NULL`` -- that one was a 500, not merely a bad value).

    Explicit ``null`` is rejected for every field, not treated as "clear
    this field" -- decided, not left ambiguous: no document grants any role
    the power to blank a store's timezone, currency, or managed_from once a
    value exists, and doing so silently would be exactly the kind of guess
    ``AGENTS.md`` forbids (a store with months already resolved against its
    current timezone would have that history reinterpreted the moment the
    field reads as unset again). If a field should ever become legitimately
    clearable, that is a business decision for the Owner to make explicitly,
    naming which fields and why -- not a side effect of an update endpoint
    accepting whatever shape JSON happens to allow. Omit the field entirely
    to leave it unchanged; this is unambiguous and already worked before
    this fix.

    F-44 (US-003 audit): ``extra="forbid"`` -- the F-39 no-op-for-empty-
    patch fix made an unrecognised field's silent drop *worse*, not better:
    ``{"reporting_currency_code": "EUR"}`` (the real column name) used to
    compute ``fields={}`` after translation and take the "nothing to do"
    branch, returning 200 "unchanged" with no indication the field was
    never recognised at all. ``{"is_active": false}`` behaved the same way.
    """

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1)
    timezone: str | None = None
    currency_code: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    managed_from_year: int | None = None
    managed_from_month: int | None = Field(default=None, ge=1, le=12)

    @model_validator(mode="after")
    def _no_field_may_be_cleared(self) -> "StoreUpdateRequest":
        # F-32: a field is "provided" exactly when it is in
        # model_fields_set -- the same signal exclude_unset reads below.
        # Checking the *value* alone (`is None`) would be indistinguishable
        # from "omitted", which is the one case this must NOT reject.
        nulled = [
            field
            for field in self.model_fields_set
            if getattr(self, field) is None
        ]
        if nulled:
            raise ValueError(
                f"{', '.join(sorted(nulled))}: explicit null is not accepted -- "
                "no identity field may be cleared once a value exists "
                "(US-003 audit F-32). Omit the field entirely to leave it "
                "unchanged."
            )
        return self

    @model_validator(mode="after")
    def _managed_from_paired(self) -> "StoreUpdateRequest":
        # Mirrors stores_managed_from_paired (001_baseline.sql), checked on
        # *presence* (model_fields_set) rather than on value -- the value-null
        # case is already rejected above, so by the time this runs, "present"
        # means "present with a real value".
        year_given = "managed_from_year" in self.model_fields_set
        month_given = "managed_from_month" in self.model_fields_set
        if year_given != month_given:
            raise ValueError(
                "managed_from_year and managed_from_month must both be "
                "provided together, or neither provided"
            )
        return self


# Request field name -> column name. Kept next to the request model rather
# than in the repository, which only ever sees already-translated column
# names (src/stores/repository.py's own _IDENTITY_COLUMNS is the second half
# of this same allowlist discipline, one layer further in).
_UPDATE_FIELD_TO_COLUMN = {
    "name": "name",
    "timezone": "timezone",
    "currency_code": "reporting_currency_code",
    "managed_from_year": "managed_from_year",
    "managed_from_month": "managed_from_month",
}


@app.patch("/stores/{store_id}")
def update_store(
    store_id: uuid.UUID,
    body: StoreUpdateRequest,
    response: Response,
    operation_id: str = Header(alias="Idempotency-Key"),
    actor: Actor = Depends(request_actor),
) -> dict[str, object]:
    """Change a store's identity fields -- rename, retimezone, recurrency,
    or re-set ``managed_from`` (US-003 AC-04). Owner or Admin for most
    fields; ``managed_from_*`` specifically is Owner-only pending B-13 (see
    ``src/stores/service.py``). Scoped to stores the actor is assigned to
    (BR-11, AC-03).

    B-9 (open, F-42): this endpoint carries no ``is_active`` filter --
    whether an inactive store accepts writes at all is not settled, exactly
    as ``repository.list_stores``'s docstring notes for the read path.
    """
    provided = body.model_dump(exclude_unset=True)
    fields = {_UPDATE_FIELD_TO_COLUMN[k]: v for k, v in provided.items()}

    with connection() as conn:
        store, replayed = stores_service.update_store_identity(
            conn,
            actor.scope,
            actor_id=actor.scope.actor_id,
            role=actor.role,
            store_id=store_id,
            fields=fields,
            operation_id=operation_id,
        )

    return {
        "data": _store_body(store),
        "meta": {"operation_id": operation_id, "replayed": replayed},
        "errors": [],
    }
