"""Sign-in, session validation, sign-out, and store scope resolution.

Implements the testable half of ``access-v1``: password storage is delegated
to ``passwords.py``, but everything about *when* a session is valid, *when*
an account locks, and *which* stores a session may act within is a business
rule and lives here, not in ``main.py`` and not in the database schema.

Two deliberate shapes carried through this module:

* ``SessionInvalid`` is one exception for an unknown token, an idle-expired
  session, an absolute-expired session, and a revoked session. Telling those
  apart to the caller would let an attacker distinguish "never existed" from
  "existed and expired", which is information they should not get.
* Every function here takes an already-open connection and never commits.
  The caller owns the transaction -- ``main.py`` via ``db.connection()`` in
  production, the rolled-back ``conn`` fixture in tests.
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

import psycopg
import pyotp

from src import audit
from src.access import passwords
from src.access.repository import get_user, get_user_credentials
from src.clock import utc_now
from src.scoping import StoreScope

__all__ = [
    "IDLE_TIMEOUT_KEY",
    "ABSOLUTE_TIMEOUT_KEY",
    "LOCKOUT_THRESHOLD_KEY",
    "LOCKOUT_DURATION_KEY",
    "SignInResult",
    "AuthenticatedSession",
    "AuthenticationFailed",
    "InvalidCredentials",
    "AccountLocked",
    "MfaRequired",
    "SessionInvalid",
    "sign_in",
    "authenticate",
    "sign_out",
    "resolve_scope",
]


# access_policy keys. access-v1: "Timeout values are configuration, not
# constants" -- extended here to the lockout threshold and duration, which
# are no less a business decision than the timeouts are.
IDLE_TIMEOUT_KEY = "session_idle_timeout_minutes"
ABSOLUTE_TIMEOUT_KEY = "session_absolute_timeout_minutes"
LOCKOUT_THRESHOLD_KEY = "sign_in_failure_lockout_threshold"
LOCKOUT_DURATION_KEY = "sign_in_lockout_duration_minutes"

# access-v1: MFA required for Owner and Admin, optional for Manager. The
# database constraint (users_role_requires_mfa) enforces the same rule
# structurally; this list is the service-layer mirror of it, used to decide
# whether a password-only sign-in should even attempt to proceed.
_PRIVILEGED_ROLES = ("owner", "admin")

# F-02 (US-001 audit): a fixed, valid bcrypt hash of a password nobody signs
# in with. When the email does not resolve to an active account, sign_in
# still runs a real bcrypt comparison against this hash before refusing, so
# refusing an unknown account costs the same wall-clock time as refusing a
# wrong password for a known one. A constant computed once at import time
# would vary with the machine that happened to start the process; a literal
# is reproducible and auditable.
#
# Generated with: bcrypt.hashpw(b"<arbitrary>", bcrypt.gensalt(rounds=12))
# The plaintext behind this hash is not a credential for any real account and
# is not recorded anywhere -- only the hash matters, as the comparison always
# fails.
_DUMMY_PASSWORD_HASH = "$2b$12$wwYMlN7qUiNqF3KkLFlYmOEuS5ntJcOBxB5CrPeVF8EfPQ5fpMYKG"


class AuthenticationFailed(Exception):
    """Base for every sign-in and session failure."""


class InvalidCredentials(AuthenticationFailed):
    """Email, password, or TOTP code did not match."""


class AccountLocked(AuthenticationFailed):
    """The account is in a temporary lockout window."""


class MfaRequired(AuthenticationFailed):
    """A privileged account attempted to sign in without completing MFA."""


class SessionInvalid(AuthenticationFailed):
    """The session token does not resolve to an active session.

    Deliberately one class covering unknown, idle-expired, absolute-expired,
    and revoked tokens -- see the module docstring.
    """


@dataclass(frozen=True)
class SignInResult:
    token: str
    session_id: uuid.UUID

    def __repr__(self) -> str:
        # F-10 (US-001 audit): the dataclass-generated repr would include the
        # raw token. Nothing logs this value today, but the principle this
        # whole module is built on is that exclusion must be structural, not
        # remembered at every call site that might someday print or log it.
        return f"SignInResult(token=<redacted>, session_id={self.session_id!r})"


@dataclass(frozen=True)
class AuthenticatedSession:
    user_id: uuid.UUID
    session_id: uuid.UUID

    def __repr__(self) -> str:
        # F-10: session_id is not a secret by itself, but it is the value a
        # hijacked log line would need to replay a session. Redacted for the
        # same structural reason as SignInResult above.
        return f"AuthenticatedSession(user_id={self.user_id!r}, session_id=<redacted>)"


def _policy_value(conn: psycopg.Connection, key: str) -> int:
    """Read an integer policy value. Never a constant, never a fallback.

    A missing row is a configuration error, not an invitation to guess --
    the same principle BR-06 applies to edit limits applies here to timeouts
    and the lockout threshold.
    """
    row = conn.execute(
        "select policy_value from access_policy where policy_key = %s", (key,)
    ).fetchone()
    if row is None:
        raise RuntimeError(
            f"access_policy has no row for {key!r}. access-v1 requires this "
            "value to be configuration, not a constant."
        )
    return row["policy_value"]


def _new_token() -> str:
    """128 bits of randomness per side, rendered as hex. Opaque to the client."""
    return uuid.uuid4().hex + uuid.uuid4().hex


def _hash_token(token: str) -> str:
    """A one-way hash of the session token. The database stores this, never
    the token itself -- a leaked database row must not be a usable credential."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _verify_totp(secret: str, code: str) -> bool:
    """Check a TOTP code. A malformed secret or code fails closed, not open.

    ``valid_window=1`` tolerates one 30-second step of clock drift either
    side, which is standard TOTP practice and not a widened guess at the
    code itself.
    """
    try:
        return bool(pyotp.TOTP(secret).verify(code, valid_window=1))
    except Exception:
        return False


def _record_failed_attempt(conn: psycopg.Connection, user_id: uuid.UUID) -> int:
    row = conn.execute(
        """
        update users
           set failed_sign_in_count = failed_sign_in_count + 1
         where id = %s
        returning failed_sign_in_count
        """,
        (user_id,),
    ).fetchone()
    return row["failed_sign_in_count"]


def _lock_account(conn: psycopg.Connection, user_id: uuid.UUID, minutes: int) -> None:
    conn.execute(
        "update users set locked_until = %s where id = %s",
        (utc_now() + timedelta(minutes=minutes), user_id),
    )


def _reset_failed_attempts(conn: psycopg.Connection, user_id: uuid.UUID) -> None:
    conn.execute(
        "update users set failed_sign_in_count = 0, locked_until = null where id = %s",
        (user_id,),
    )


def _fail_and_maybe_lock(conn: psycopg.Connection, user_id: uuid.UUID) -> bool:
    """Record one failed attempt against ``user_id``; lock the account if the
    threshold is now reached. Returns whether the lock was just imposed.

    Shared by the password branch and the TOTP branch (F-03, US-001 audit):
    before this, only a wrong password moved the counter, so an attacker who
    already had a privileged password could guess the second factor without
    limit.
    """
    count = _record_failed_attempt(conn, user_id)
    threshold = _policy_value(conn, LOCKOUT_THRESHOLD_KEY)
    if count >= threshold:
        duration = _policy_value(conn, LOCKOUT_DURATION_KEY)
        _lock_account(conn, user_id, duration)
        return True
    return False


def _audit_sign_in(
    conn: psycopg.Connection,
    *,
    event_type: str,
    actor_id: uuid.UUID | None,
    reason: str | None = None,
) -> None:
    """One audit event for a sign-in outcome (F-06, US-001 audit).

    ``api-service-design.md`` requires ``auth_sign_in_succeeded``,
    ``auth_sign_in_failed`` and ``auth_account_locked``. Sign-in carries no
    caller-supplied idempotency key and is not a replayable write, so a fresh
    ``operation_id`` is minted here rather than threaded in from the caller.

    F-12: the automatic, expiry-driven unlock (below, where a lock's
    duration has passed) also emits ``auth_account_unlocked`` through this
    same helper -- it is a security-control state transition like the lock
    itself, and leaving it untraced meant an investigator could see a lock
    imposed with no corresponding exit. Admin-initiated unlock (US-004) is
    still out of this module's scope; only the lock decaying on its own,
    judged here before the next attempt is even checked, is audited.

    SC-13: the payload is a bare reason code, nothing else. No password, no
    token, no session identifier, no mfa_secret -- there is no field here any
    of those could be put into, by construction, not by remembering to leave
    it out.
    """
    audit.record(
        conn,
        audit.AuditEvent(
            event_type=event_type,
            entity_type="user",
            entity_id=actor_id,
            actor_id=actor_id,
            operation_id=audit.new_operation_id(),
            reason=reason,
        ),
    )


def sign_in(
    conn: psycopg.Connection,
    *,
    email: str,
    password: str,
    totp_code: str | None = None,
) -> SignInResult:
    """Authenticate and issue a new session.

    Raises ``InvalidCredentials``, ``AccountLocked``, or ``MfaRequired`` on
    failure. No session row is ever written on a failure path -- every raise
    below happens before the insert, not after a rollback that merely hopes
    the insert did not happen.

    F-11 (US-001 audit): the credential-bearing read now goes through
    ``repository.get_user_credentials`` -- the one query anywhere permitted to
    return ``password_hash`` or ``mfa_secret`` -- rather than an ad hoc select
    kept here.
    """
    user: dict[str, Any] | None = get_user_credentials(conn, email)

    if user is None or not user["is_active"]:
        # F-02: an unknown email and a deactivated account must cost the
        # caller exactly what a wrong password costs a real, active account --
        # a real bcrypt comparison, not a fast-path return. Otherwise the
        # response time itself discloses which case this was.
        passwords.verify_password(password, _DUMMY_PASSWORD_HASH)
        _audit_sign_in(
            conn,
            event_type="auth_sign_in_failed",
            # Internally distinguishable (so an operator can tell a
            # deactivated user from a typo'd email), but this is server-side
            # forensic data -- it never reaches the HTTP caller, who sees the
            # identical body and identical timing either way.
            actor_id=user["id"] if user is not None else None,
            reason="invalid_credentials" if user is None else "account_inactive",
        )
        raise InvalidCredentials("invalid email or password")

    if user["locked_until"] is not None:
        if user["locked_until"] > utc_now():
            _audit_sign_in(
                conn,
                event_type="auth_sign_in_failed",
                actor_id=user["id"],
                reason="account_locked",
            )
            raise AccountLocked("account is temporarily locked")
        # F-08 (US-001 audit): the lock has expired. The failure count that
        # caused it is stale history, not evidence of an attack in progress --
        # left alone, a single further mistake would relock the account for
        # the full duration again, forever, for anyone whose email is known
        # (and F-02 just made "is this email known" unobservable from the
        # outside, which is exactly what makes this an available denial of
        # service if left unfixed). Decaying it here, before the password is
        # even checked, means a fresh mistake is judged on its own.
        #
        # F-12: this is an unlock, not merely a counter reset -- it is the
        # one state transition in this module that used to leave no trace.
        # It is audited in the same transaction as the write it describes.
        _reset_failed_attempts(conn, user["id"])
        _audit_sign_in(
            conn,
            event_type="auth_account_unlocked",
            actor_id=user["id"],
            reason="lock_expired",
        )

    if not passwords.verify_password(password, user["password_hash"]):
        locked = _fail_and_maybe_lock(conn, user["id"])
        _audit_sign_in(
            conn,
            event_type="auth_sign_in_failed",
            actor_id=user["id"],
            reason="invalid_credentials",
        )
        if locked:
            _audit_sign_in(
                conn, event_type="auth_account_locked", actor_id=user["id"]
            )
            raise AccountLocked("account locked after repeated failures")
        raise InvalidCredentials("invalid email or password")

    if user["mfa_enabled"]:
        if not totp_code:
            raise MfaRequired("multi-factor authentication required")
        if not _verify_totp(user["mfa_secret"], totp_code):
            # F-03: a rejected TOTP code is a failed sign-in attempt exactly
            # like a rejected password, and must count the same way -- an
            # attacker already holding a valid password must not be able to
            # brute-force the second factor without limit.
            locked = _fail_and_maybe_lock(conn, user["id"])
            _audit_sign_in(
                conn,
                event_type="auth_sign_in_failed",
                actor_id=user["id"],
                reason="invalid_mfa_code",
            )
            if locked:
                _audit_sign_in(
                    conn, event_type="auth_account_locked", actor_id=user["id"]
                )
                raise AccountLocked("account locked after repeated failures")
            raise InvalidCredentials("invalid authentication code")
    elif user["role"] in _PRIVILEGED_ROLES:
        # Defence in depth: users_role_requires_mfa should make this
        # unreachable, but enforcement must not depend solely on a row
        # having been inserted correctly.
        raise MfaRequired("multi-factor authentication required")

    _reset_failed_attempts(conn, user["id"])

    token = _new_token()
    now = utc_now()
    absolute_minutes = _policy_value(conn, ABSOLUTE_TIMEOUT_KEY)
    row = conn.execute(
        """
        insert into user_sessions (user_id, token_hash, issued_at, last_seen_at, expires_at)
        values (%s, %s, %s, %s, %s)
        returning id
        """,
        (
            user["id"],
            _hash_token(token),
            now,
            now,
            now + timedelta(minutes=absolute_minutes),
        ),
    ).fetchone()

    conn.execute("update users set last_sign_in_at = %s where id = %s", (now, user["id"]))

    _audit_sign_in(
        conn, event_type="auth_sign_in_succeeded", actor_id=user["id"]
    )

    return SignInResult(token=token, session_id=row["id"])


def authenticate(conn: psycopg.Connection, token: str) -> AuthenticatedSession:
    """Validate a session token. Raises ``SessionInvalid`` on any failure.

    Idle expiry is computed here, at validate time, from ``last_seen_at``
    plus the *current* policy value -- never from a value cached at sign-in
    -- so editing ``access_policy`` changes behaviour immediately.

    F-04 (US-001 audit): joins ``users`` and checks ``is_active`` on every
    call, for the same reason idle expiry is recomputed here rather than
    trusted from sign-in time -- deactivating a user must take effect on the
    very next request, not merely once the session happens to expire on its
    own. Before this fix a user deactivated at 09:00 kept full scope for up
    to twelve hours, across every store.
    """
    row = conn.execute(
        """
        select s.id, s.user_id, s.last_seen_at, s.expires_at, s.revoked_at,
               u.is_active
          from user_sessions s
          join users u on u.id = s.user_id
         where s.token_hash = %s
        """,
        (_hash_token(token),),
    ).fetchone()

    if row is None or row["revoked_at"] is not None or not row["is_active"]:
        raise SessionInvalid("session not found")

    now = utc_now()
    if row["expires_at"] <= now:
        raise SessionInvalid("session expired")

    idle_minutes = _policy_value(conn, IDLE_TIMEOUT_KEY)
    if row["last_seen_at"] + timedelta(minutes=idle_minutes) <= now:
        raise SessionInvalid("session idle timeout")

    # A successful validation is activity: the idle window slides forward.
    conn.execute(
        "update user_sessions set last_seen_at = %s where id = %s", (now, row["id"])
    )

    return AuthenticatedSession(user_id=row["user_id"], session_id=row["id"])


def sign_out(conn: psycopg.Connection, token: str) -> None:
    """Revoke a session server-side. Idempotent: an already-revoked or
    unknown token is not an error -- sign-out always ends with "not signed
    in", whether or not it did any work to get there."""
    conn.execute(
        """
        update user_sessions
           set revoked_at = %s
         where token_hash = %s and revoked_at is null
        """,
        (utc_now(), _hash_token(token)),
    )


def resolve_scope(conn: psycopg.Connection, user_id: uuid.UUID) -> StoreScope:
    """The store scope a user currently holds.

    Queried fresh on every call, never cached on the session row: AC-03
    requires that authorization withdrawn after a session was issued takes
    effect on the very next request, not on the next sign-in.
    """
    user = get_user(conn, user_id)
    if user is None:
        raise SessionInvalid("the signed-in user no longer exists")
    if not user["is_active"]:
        # F-04 defence in depth: authenticate() above should already have
        # refused a deactivated user's session, but scope resolution must not
        # depend solely on that earlier check having run correctly -- the
        # same principle the MFA check below already applies to role.
        raise SessionInvalid("the signed-in user is deactivated")

    if user["role"] == "owner":
        return StoreScope.owner(actor_id=user_id)

    rows = conn.execute(
        "select store_id from user_store_assignments where user_id = %s",
        (user_id,),
    ).fetchall()
    return StoreScope.assigned(actor_id=user_id, store_ids=[r["store_id"] for r in rows])
