"""UI-01 — public landing page, sign-in screen, authenticated shell, sign-out.
UI-04 — orders list screen, US-005b-2 (US-005 AC-03's screen half).

HTML transport only. Every business rule reachable from here already exists
and is already tested in ``src.access.service`` (``sign_in`` / ``authenticate``
/ ``resolve_scope`` / ``sign_out``), ``src.stores.repository``
(``list_stores`` / ``get_store``) and ``src.orders.repository``
(``get_import_run_state``) -- this module adds no new rule, only a
browser-shaped way to reach the existing ones: a session cookie instead of
an ``Authorization`` header, and a redirect/re-render instead of a JSON
error body. The same discipline ``main._authenticate_request``'s own
docstring states ("two copies of an authentication gate diverge exactly as
silently... the moment one of them picks up a fix") is why this module
never re-implements password checking, lockout, MFA, scope resolution, or
import-state classification -- it only calls them.

**Disclosed, narrow gap:** no test in ``tests/test_ui001_sign_in_screen.py``
signs in as an Owner or Admin through this HTML form, and MFA entry for the
HTML form is not built here. ``access_service.sign_in`` already raises
``MfaRequired`` for a privileged account with no TOTP code; this module's
``POST /sign-in`` catches that (it is a subclass of ``AuthenticationFailed``,
same as every other rejected sign-in) and renders the same generic failure
message, with no second field to collect a code. A Manager (``mfa_enabled``
optional, per access-v1) signs in through this form exactly as every test
below exercises; an Owner/Admin signing in through the *browser* form today
gets a generic "invalid" message instead of a prompt for their code. This is
a deliberately undone piece of work, not a silent omission -- see the session
report accompanying this change.

**US-005b-2 -- the orders list screen (G-1, invented route/template/markup,
``tests/test_us005b2_orders_list_screen.py`` module docstring).** This story
is narrowly AC-03: the last successful import and the most recent failed
attempt, both visible together, never one mislabelled as the other.
Everything else UI-04's non-normative column-group description names
(filters, date-basis label, sort, pagination, list-position restoration,
and any actual order rows) is explicitly out of scope -- owned by US-006,
not started. ``_render_instant`` below is presentation-only (an ISO-8601
UTC instant for the machine-readable ``datetime`` attribute, a store-local
rendering for the human-readable text, using ``src.clock`` exactly as
``orders.service`` already does for ``order_date`` -- never a second,
divergent timezone conversion). No business rule is decided here:
"never imported" vs "last success" vs "most recent attempt's own status"
all come from ``orders_repository.get_import_run_state`` unchanged; this
module only chooses words and an HTML shape around those same three facts.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from src import clock
from src.access import repository as access_repository
from src.access import service as access_service
from src.db.connection import connection
from src.orders import repository as orders_repository
from src.stores import repository as stores_repository

router = APIRouter()

_TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))

# The cookie carries the exact same opaque, random, server-validated token
# `access_service.sign_in` already issues for the JSON API -- it is not
# separately signed or encrypted (see `access_service`'s own module
# docstring: the database never stores the raw token, only its hash, and a
# leaked cookie is revocable server-side exactly like a leaked bearer
# token). HttpOnly so client-side script cannot read it; SameSite=Lax as a
# baseline CSRF mitigation for a cookie-authenticated form post.
SESSION_COOKIE_NAME = "lucrum_session"

# One generic message for every sign-in failure reason this form can
# produce (InvalidCredentials, AccountLocked, MfaRequired alike) -- the same
# non-disclosure rule `main._AUTH_FAILURE_RESPONSES`/`_DEFAULT_AUTH_FAILURE`
# already apply to the JSON API, reused rather than re-decided here. F-13
# (whether AccountLocked should ever read differently from the rest) is an
# open contradiction this module does not resolve either -- see
# PROGRESS.md and the test file's own skip for it.
_SIGN_IN_FAILURE_MESSAGE = "Invalid email, password, or authentication code."


def _cookie_token(request: Request) -> str | None:
    return request.cookies.get(SESSION_COOKIE_NAME)


def _optional_session(conn, request: Request) -> access_service.AuthenticatedSession | None:
    """The cookie-backed counterpart of ``main._authenticate_request`` for a
    route that must render *something* either way, rather than refuse
    outright. A missing cookie and an invalid one both resolve to "not
    signed in" -- the same collapse ``SessionInvalid`` already makes for
    unknown, expired, and revoked tokens on the header-based path.
    """
    token = _cookie_token(request)
    if not token:
        return None
    try:
        return access_service.authenticate(conn, token)
    except access_service.SessionInvalid:
        return None


@router.get("/")
def root(request: Request):
    return templates.TemplateResponse(request, "index.html", {})


@router.get("/sign-in")
def sign_in_form(request: Request):
    with connection() as conn:
        session = _optional_session(conn, request)

    if session is not None:
        # Already authenticated: never re-render the password form. A
        # redirect to the shell is followed by the caller and lands on a
        # page with no password field at all, which satisfies the
        # requirement without a second, passwordless template variant that
        # could quietly drift from the real one.
        return RedirectResponse("/app", status_code=303)

    return templates.TemplateResponse(
        request, "sign_in.html", {"email": "", "error": None}
    )


@router.post("/sign-in")
def sign_in_submit(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
):
    with connection() as conn:
        try:
            result = access_service.sign_in(conn, email=email, password=password)
        except access_service.AuthenticationFailed:
            # Preserve the lockout bookkeeping `sign_in` already wrote
            # before raising -- the same discipline `main.create_session`
            # applies: `connection()` rolls back on any exception, which
            # would otherwise erase the failed-attempt count along with the
            # rejected sign-in.
            conn.commit()
            return templates.TemplateResponse(
                request,
                "sign_in.html",
                {"email": email, "error": _SIGN_IN_FAILURE_MESSAGE},
            )

    response = RedirectResponse("/app", status_code=303)
    response.set_cookie(
        SESSION_COOKIE_NAME,
        result.token,
        httponly=True,
        samesite="lax",
        # No `secure` flag here: local development serves plain HTTP.
        # Production sits behind TLS termination and must set this --
        # disclosed rather than silently decided, same as every other
        # deployment-environment gap this project tracks in PROGRESS.md.
    )
    return response


@router.get("/app")
def shell(request: Request):
    token = _cookie_token(request)
    if not token:
        return RedirectResponse("/sign-in", status_code=303)

    with connection() as conn:
        try:
            session = access_service.authenticate(conn, token)
            # Scope is resolved fresh on every request, never cached on the
            # session or the cookie -- AC-03: an assignment or a session
            # revoked after this cookie was issued must stop working on the
            # very next request, not the next sign-in. `resolve_scope`
            # itself also raises `SessionInvalid` if the user has since
            # been deactivated or deleted (see its own docstring) -- caught
            # here alongside `authenticate`'s own failures, not separately.
            scope = access_service.resolve_scope(conn, session.user_id)
            user = access_repository.get_user(conn, session.user_id)
            stores = stores_repository.list_stores(conn, scope)
        except access_service.SessionInvalid:
            return RedirectResponse("/sign-in", status_code=303)

    return templates.TemplateResponse(
        request,
        "shell.html",
        {"user_email": user["email"], "stores": stores},
    )


@router.post("/sign-out")
def sign_out(request: Request):
    token = _cookie_token(request)
    if token:
        with connection() as conn:
            # Revokes the session server-side (AC-04): the token is
            # rejected on its very next use, not merely dropped by the
            # client -- this is the same `access_service.sign_out` the
            # JSON API's `DELETE /auth/session` already calls, reached here
            # through a browser-shaped form post instead of a bearer token.
            access_service.sign_out(conn, token)

    response = RedirectResponse("/sign-in", status_code=303)
    response.delete_cookie(SESSION_COOKIE_NAME)
    return response


def _render_instant(instant: datetime, timezone_name: str | None) -> tuple[str, str]:
    """``(iso, human)`` for one stored instant.

    ``iso`` is always the UTC instant itself (G-1 point 4's machine-readable
    contract for the ``datetime`` attribute) -- this never varies with the
    store's configured timezone, only the human-readable text does.

    ``human`` converts to the store's own IANA timezone via
    ``clock.store_timezone`` -- the SAME resolution ``orders.service`` already
    uses for ``order_date``, not a second implementation. AGENTS.md's clock
    discipline ("never guess") means a missing or invalid timezone is named
    in the text, never silently presented as if the UTC instant were local.
    """
    utc_instant = clock.ensure_utc(instant)
    iso = utc_instant.isoformat()
    try:
        local = utc_instant.astimezone(clock.store_timezone(timezone_name))
    except clock.InvalidTimezone:
        return iso, f"{iso} (store timezone not configured -- shown in UTC)"
    return iso, local.strftime("%Y-%m-%d %H:%M %Z")


@router.get("/app/stores/{store_id}/orders")
def orders_list(request: Request, store_id: uuid.UUID):
    """UI-04, US-005b-2 -- US-005 AC-03's screen half.

    Renders exactly the three facts ``get_import_run_state`` already
    classifies: never-imported, the last successful run's own instant, and
    the most recent attempt's own status/instant -- never collapsed into
    one combined "status" (AGENTS.md non-negotiable #1), and never falling
    back to a stale success once a later attempt has failed (non-negotiable
    #2's sibling rule for THIS dimension: AC-03's own "stale data is not
    labeled current").

    Store scope is store-scope-only (B-20, interim, unconfirmed --
    ``get_import_run_state``'s own docstring). ``stores_repository.
    get_store`` is called first, under the SAME scope, purely to read the
    store's name/timezone for the page -- both calls raise the identical
    ``OutOfScope`` for an unassigned OR a nonexistent store id (US-002 AC-02
    discipline), which ``main.out_of_scope_handler`` already turns into a
    404 indistinguishable between the two.
    """
    token = _cookie_token(request)
    if not token:
        return RedirectResponse("/sign-in", status_code=303)

    with connection() as conn:
        try:
            session = access_service.authenticate(conn, token)
            # Fresh on every request, never cached -- same discipline as
            # `shell()` above.
            scope = access_service.resolve_scope(conn, session.user_id)
        except access_service.SessionInvalid:
            return RedirectResponse("/sign-in", status_code=303)

        store = stores_repository.get_store(conn, scope, store_id)
        import_state = orders_repository.get_import_run_state(conn, scope, store_id)

    last_success = None
    if import_state["last_success_at"] is not None:
        iso, human = _render_instant(import_state["last_success_at"], store["timezone"])
        last_success = {
            "iso": iso,
            # Deliberately never says "current"/"fresh"/"up to date", in
            # either state -- AC-03 forbids the stale case from claiming
            # currency; the simplest way to never violate that is to never
            # make the claim at all, so this text does not need to branch
            # on whether the most recent attempt succeeded.
            "text": f"Last successful import completed {human}.",
        }

    last_failure = None
    if (
        import_state["last_attempt_status"] is not None
        and import_state["last_attempt_status"] != "completed"
        and import_state["last_attempt_finished_at"] is not None
    ):
        iso, human = _render_instant(
            import_state["last_attempt_finished_at"], store["timezone"]
        )
        last_failure = {
            "iso": iso,
            "text": f"The most recent import attempt failed {human}.",
        }

    return templates.TemplateResponse(
        request,
        "orders_list.html",
        {
            "store": store,
            "never_imported": import_state["never_imported"],
            "last_success": last_success,
            "last_failure": last_failure,
        },
    )
