"""US-001 — Sign in securely. Module: Access. Screen: UI-01.

"As an authorized user, I want to sign in and understand session expiry so that
business data is available only within an authorized session."

Nothing here exists yet. Every assertion is derived from the acceptance criteria
and from ``access-v1``, never from an implementation, because an implementation
does not exist. The contract these tests fix is listed in the module docstring
of each section so that the implementer is held to a named surface rather than
to whatever happens to make the file green.

Two conventions inherited from the existing suite:

* Database tests run inside the ``conn`` fixture's rolled-back transaction, so
  service functions must **never commit** -- the caller owns the transaction,
  exactly as ``src/main.py`` already assumes via ``db.connection()``.
* HTTP tests need rows the API's own pool can see, so they commit on a separate
  connection. Nothing is cleaned up; the test database is disposable and
  ``audit_events`` refuses DELETE by design.

Imports of ``src.access`` are deliberately made *inside* each test rather than
at module scope. While the module is absent the failure must be reported
per-test as a missing behaviour, not as a collection error that hides every
other assertion in the file.

Deliberately not asserted: whether an ordinary sign-in or sign-out writes an
``audit_events`` row. That is an open question escalated to the Owner, so this
file asserts neither its presence nor its absence -- only that if such a row is
written it leaks nothing (SC-13).
"""

from __future__ import annotations

import uuid
from contextlib import contextmanager
from datetime import timedelta

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row

from src import main
from src.clock import utc_now

# A conventional RFC 4648 base32 test vector. MFA *enrolment* is out of scope
# for US-001, so the secret is seeded rather than generated.
TOTP_TEST_SECRET = "JBSWY3DPEHPK3PXP"

IDLE_TIMEOUT_KEY = "session_idle_timeout_minutes"
ABSOLUTE_TIMEOUT_KEY = "session_absolute_timeout_minutes"
LOCKOUT_THRESHOLD_KEY = "sign_in_failure_lockout_threshold"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _new_email(label: str) -> str:
    return f"us001-{label}-{uuid.uuid4().hex}@example.test"


def _create_user(c, *, role, password, mfa_enabled=None, mfa_secret=None):
    """Seed a user. Imports the hasher here so an absent module fails the test."""
    from src.access import passwords

    if mfa_enabled is None:
        # access-v1: MFA is required for Owner and Admin, optional for Manager.
        mfa_enabled = role in ("owner", "admin")
    if mfa_enabled and mfa_secret is None:
        mfa_secret = TOTP_TEST_SECRET

    email = _new_email(role)
    row = c.execute(
        """
        insert into users (email, role, password_hash, mfa_enabled, mfa_secret)
        values (%s, %s, %s, %s, %s)
        returning id
        """,
        (email, role, passwords.hash_password(password), mfa_enabled, mfa_secret),
    ).fetchone()
    return {"id": row["id"], "email": email, "password": password}


def _create_store(c, name="US-001 Store"):
    row = c.execute(
        """
        insert into stores (name, walmart_store_key, timezone,
                            reporting_currency_code, managed_from_year,
                            managed_from_month)
        values (%s, %s, 'America/New_York', 'USD', 2026, 7)
        returning id
        """,
        (name, f"US001-{uuid.uuid4().hex[:12]}"),
    ).fetchone()
    return row["id"]


def _assign(c, user_id, store_id):
    c.execute(
        "insert into user_store_assignments (user_id, store_id) values (%s, %s)",
        (user_id, store_id),
    )


def _unassign(c, user_id, store_id):
    c.execute(
        "delete from user_store_assignments where user_id = %s and store_id = %s",
        (user_id, store_id),
    )


def _policy_value(c, key: str) -> int:
    """Read a policy value. access-v1: 'timeout values are configuration'."""
    row = c.execute(
        "select policy_value from access_policy where policy_key = %s", (key,)
    ).fetchone()
    assert row is not None, (
        f"access_policy has no row for {key!r}. access-v1 requires timeout and "
        "lockout values to be configuration read from a table, not constants."
    )
    return row["policy_value"]


def _set_policy_value(c, key: str, value: int) -> None:
    updated = c.execute(
        "update access_policy set policy_value = %s where policy_key = %s",
        (value, key),
    ).rowcount
    assert updated == 1, f"no access_policy row to update for {key!r}"


def _session_row(c, session_id):
    return c.execute(
        """
        select id, user_id, token_hash, issued_at, last_seen_at, expires_at,
               revoked_at
        from user_sessions where id = %s
        """,
        (session_id,),
    ).fetchone()


@contextmanager
def _committed(url):
    """A connection whose work is visible to the API's own pool."""
    with psycopg.connect(url, row_factory=dict_row) as c:
        yield c
        c.commit()


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _sign_in_over_http(client, user) -> str:
    r = client.post(
        "/auth/session",
        json={"email": user["email"], "password": user["password"]},
    )
    assert r.status_code == 201, r.text
    token = r.json()["data"]["token"]
    assert token
    return token


@pytest.fixture
def client(migrated_database):
    with TestClient(main.app) as c:
        yield c


# ---------------------------------------------------------------------------
# AC-01
# ---------------------------------------------------------------------------


def test_ac_01_invalid_or_expired_authentication_returns_no_protected_record(
    client, migrated_database
):
    """AC-01: invalid or expired authentication returns no protected record.

    The criterion is about *records*, not about a particular status code, so
    the assertion is that the store's identifier never reaches the caller. The
    valid-session leg is what stops this passing vacuously: the record must be
    genuinely reachable with a good session before its absence means anything.
    """
    password = "us001-ac01-correct-horse-battery"
    with _committed(migrated_database) as c:
        store_id = _create_store(c, "AC-01 Store")
        user = _create_user(c, role="manager", password=password)
        _assign(c, user["id"], store_id)

    token = _sign_in_over_http(client, user)

    # The record is protected data this caller is entitled to see.
    allowed = client.get("/stores", headers=_auth(token))
    assert allowed.status_code == 200
    assert str(store_id) in [s["id"] for s in allowed.json()["data"]]

    # No authentication at all.
    assert str(store_id) not in client.get("/stores").text

    # A syntactically plausible but invalid token.
    forged = client.get("/stores", headers=_auth(uuid.uuid4().hex))
    assert str(store_id) not in forged.text

    # A malformed credential.
    garbage = client.get("/stores", headers={"Authorization": "Bearer not-a-token"})
    assert str(store_id) not in garbage.text

    # An expired session: the absolute timeout has passed server-side.
    with _committed(migrated_database) as c:
        c.execute(
            """
            update user_sessions
               set expires_at = now() - interval '1 minute'
             where user_id = %s and revoked_at is null
            """,
            (user["id"],),
        )

    expired = client.get("/stores", headers=_auth(token))
    assert str(store_id) not in expired.text


# ---------------------------------------------------------------------------
# AC-02
# ---------------------------------------------------------------------------


def test_ac_02_expiry_during_edit_prompts_and_replays_nothing(
    client, migrated_database
):
    """AC-02: both halves.

    Half one -- the user is prompted to authenticate: 401 carrying an explicit
    code, so the UI can tell "sign in again" apart from "that failed".

    Half two -- no financial action is silently replayed: the underlying state
    is unchanged *and* re-authenticating does not cause the pending save to
    apply by itself. The final leg resubmits deliberately and succeeds, which
    is what stops half two passing on an implementation where writes never
    work at all.
    """
    password = "us001-ac02-correct-horse-battery"
    with _committed(migrated_database) as c:
        store_id = _create_store(c, "AC-02 Store")
        user = _create_user(c, role="manager", password=password)
        _assign(c, user["id"], store_id)

    token = _sign_in_over_http(client, user)
    operation_id = str(uuid.uuid4())
    payload = {"note": "AC-02 pending edit"}

    # The session expires while the form is open.
    with _committed(migrated_database) as c:
        c.execute(
            """
            update user_sessions
               set expires_at = now() - interval '1 minute'
             where user_id = %s and revoked_at is null
            """,
            (user["id"],),
        )

    attempted = client.post(
        f"/stores/{store_id}/notes",
        json=payload,
        headers={**_auth(token), "Idempotency-Key": operation_id},
    )

    # Half one: prompted to authenticate.
    assert attempted.status_code == 401
    assert attempted.json()["detail"]["code"] == "AUTHENTICATION_REQUIRED"

    # Half two, first part: the state is genuinely unchanged.
    with _committed(migrated_database) as c:
        assert _audit_count(c, operation_id) == 0

    # Half two, second part: re-authenticating does not apply the pending save.
    fresh_token = _sign_in_over_http(client, user)
    with _committed(migrated_database) as c:
        assert _audit_count(c, operation_id) == 0

    # And the save is not lost either -- it applies when deliberately resubmitted.
    resubmitted = client.post(
        f"/stores/{store_id}/notes",
        json=payload,
        headers={**_auth(fresh_token), "Idempotency-Key": operation_id},
    )
    assert resubmitted.status_code == 201
    with _committed(migrated_database) as c:
        assert _audit_count(c, operation_id) == 1


def _audit_count(c, operation_id: str) -> int:
    return c.execute(
        "select count(*) as n from audit_events where operation_id = %s",
        (operation_id,),
    ).fetchone()["n"]


# ---------------------------------------------------------------------------
# AC-03
# ---------------------------------------------------------------------------


def test_ac_03_returning_to_a_saved_destination_rechecks_authorization(
    client, migrated_database
):
    """AC-03: store and record authorization are checked *again* on return.

    The real claim is that authorization is not snapshotted into the session at
    sign-in. The same token that reached the destination a moment ago must stop
    reaching it once the assignment is gone.

    The second half is the disclosure rule: an unassigned store and a
    non-existent one must be indistinguishable -- identical status *and*
    identical body, or the response confirms that a store the caller may not
    see exists.
    """
    password = "us001-ac03-correct-horse-battery"
    with _committed(migrated_database) as c:
        destination = _create_store(c, "AC-03 Destination")
        never_assigned = _create_store(c, "AC-03 Other Store")
        user = _create_user(c, role="manager", password=password)
        _assign(c, user["id"], destination)

    token = _sign_in_over_http(client, user)

    # The saved destination is reachable while the assignment holds.
    arrived = client.get(f"/stores/{destination}", headers=_auth(token))
    assert arrived.status_code == 200
    assert arrived.json()["data"]["id"] == str(destination)

    nonexistent = client.get(f"/stores/{uuid.uuid4()}", headers=_auth(token))
    assert nonexistent.status_code == 404

    # A store that exists but was never assigned is indistinguishable from one
    # that does not exist.
    unassigned = client.get(f"/stores/{never_assigned}", headers=_auth(token))
    assert unassigned.status_code == nonexistent.status_code
    assert unassigned.json() == nonexistent.json()
    assert str(never_assigned) not in unassigned.text

    # Authorization is withdrawn after the session was issued.
    with _committed(migrated_database) as c:
        _unassign(c, user["id"], destination)

    # Returning to the saved destination with the same session is re-checked.
    returned = client.get(f"/stores/{destination}", headers=_auth(token))
    assert returned.status_code == nonexistent.status_code
    assert returned.json() == nonexistent.json()
    assert str(destination) not in returned.text


# ---------------------------------------------------------------------------
# AC-04
# ---------------------------------------------------------------------------


def test_ac_04_sign_out_revokes_server_side_and_clears_the_previous_session(
    client, migrated_database
):
    """AC-04: a revoked token is rejected immediately, not merely dropped.

    Rejection is asserted by *replaying the old token after sign-out*. A client
    that simply forgot the token would pass a test that only checked the second
    user's view; this one hands the revoked token straight back to the server.
    """
    password_a = "us001-ac04-alpha-correct-horse"
    password_b = "us001-ac04-bravo-correct-horse"
    with _committed(migrated_database) as c:
        store_a = _create_store(c, "AC-04 Store A")
        store_b = _create_store(c, "AC-04 Store B")
        user_a = _create_user(c, role="manager", password=password_a)
        user_b = _create_user(c, role="manager", password=password_b)
        _assign(c, user_a["id"], store_a)
        _assign(c, user_b["id"], store_b)

    # Sign-in takes no Idempotency-Key: a replayed sign-in must not hand back
    # the previous token, because access-v1 rotates the identifier each time.
    token_a = _sign_in_over_http(client, user_a)
    seen_by_a = client.get("/stores", headers=_auth(token_a))
    assert str(store_a) in [s["id"] for s in seen_by_a.json()["data"]]

    signed_out = client.delete("/auth/session", headers=_auth(token_a))
    assert signed_out.status_code == 204

    # Revocation is recorded server-side, not left to the client.
    with _committed(migrated_database) as c:
        rows = c.execute(
            "select revoked_at from user_sessions where user_id = %s",
            (user_a["id"],),
        ).fetchall()
    assert len(rows) == 1
    assert rows[0]["revoked_at"] is not None

    # The revoked token is rejected on a read ...
    replayed_read = client.get("/stores", headers=_auth(token_a))
    assert str(store_a) not in replayed_read.text

    # ... and on a write, explicitly, with the prompt to authenticate.
    replayed_write = client.post(
        f"/stores/{store_a}/notes",
        json={"note": "after sign-out"},
        headers={**_auth(token_a), "Idempotency-Key": str(uuid.uuid4())},
    )
    assert replayed_write.status_code == 401
    assert replayed_write.json()["detail"]["code"] == "AUTHENTICATION_REQUIRED"

    # Another user signs in on the same client.
    token_b = _sign_in_over_http(client, user_b)
    assert token_b != token_a

    seen_by_b = client.get("/stores", headers=_auth(token_b))
    assert seen_by_b.status_code == 200
    ids = [s["id"] for s in seen_by_b.json()["data"]]
    assert str(store_b) in ids
    # Business data from the previous session is not displayed.
    assert str(store_a) not in ids
    assert str(store_a) not in seen_by_b.text

    # The first user's token stays dead after the second signs in.
    assert str(store_a) not in client.get("/stores", headers=_auth(token_a)).text


# ---------------------------------------------------------------------------
# access-v1 policy clauses
# ---------------------------------------------------------------------------


def test_access_v1_password_storage_is_not_reversible():
    """access-v1: Argon2id, or bcrypt cost >= 12. Never reversible.

    The algorithm is checked from the encoded hash itself rather than from an
    import, so the test constrains what is actually written to
    ``users.password_hash`` rather than which library was installed.
    """
    from src.access import passwords

    password = "us001-correct-horse-battery-staple"
    digest = passwords.hash_password(password)

    assert isinstance(digest, str)
    assert digest != password
    assert password not in digest

    assert passwords.verify_password(password, digest) is True
    assert passwords.verify_password(password + "x", digest) is False
    assert passwords.verify_password("", digest) is False

    # Salted: the same password hashed twice must not produce the same string.
    # A bare digest would be reversible by lookup.
    assert passwords.hash_password(password) != digest

    if digest.startswith("$argon2id$"):
        pass
    elif digest.startswith("$2"):
        # $2b$12$... -- the cost factor is the third field.
        cost = int(digest.split("$")[2])
        assert cost >= 12, "access-v1 requires bcrypt cost >= 12"
    else:
        pytest.fail(
            "password_hash must be Argon2id or bcrypt with cost >= 12; "
            f"got an unrecognised encoding: {digest[:12]!r}"
        )


def test_access_v1_idle_timeout_comes_from_policy_table(conn):
    """access-v1: 'timeout values are configuration, not constants.'

    The value in the table is *changed* and the same code is asked again. An
    implementation with 30 hardcoded passes the first leg and fails the second,
    which is the whole point -- asserting that 30 appears somewhere would pass
    against a constant.
    """
    from src.access import service

    assert _policy_value(conn, IDLE_TIMEOUT_KEY) == 30

    password = "us001-idle-correct-horse-battery"
    user = _create_user(conn, role="manager", password=password)
    issued = service.sign_in(conn, email=user["email"], password=password)

    def _idle_for(minutes: int) -> None:
        conn.execute(
            "update user_sessions set last_seen_at = now() - make_interval(mins => %s) "
            "where id = %s",
            (minutes, issued.session_id),
        )

    # Twenty minutes idle, policy allows thirty.
    _idle_for(20)
    assert service.authenticate(conn, issued.token).user_id == user["id"]

    # Same session, same idle gap, tighter policy. No code change.
    _set_policy_value(conn, IDLE_TIMEOUT_KEY, 10)
    _idle_for(20)
    with pytest.raises(service.SessionInvalid):
        service.authenticate(conn, issued.token)

    # And loosening it again restores the session, so the first leg was not
    # passing for some unrelated reason.
    _set_policy_value(conn, IDLE_TIMEOUT_KEY, 90)
    _idle_for(20)
    assert service.authenticate(conn, issued.token).user_id == user["id"]


def test_access_v1_absolute_timeout_comes_from_policy_table(conn):
    """access-v1: absolute timeout 12 hours, read from configuration.

    ``expires_at`` is set at issue time, so the observable effect of changing
    the policy is the horizon given to the *next* session. Both the horizon and
    the refusal past it are asserted.
    """
    from src.access import service

    assert _policy_value(conn, ABSOLUTE_TIMEOUT_KEY) == 720  # 12 hours

    password = "us001-absolute-correct-horse-battery"
    user = _create_user(conn, role="manager", password=password)

    first = service.sign_in(conn, email=user["email"], password=password)
    row = _session_row(conn, first.session_id)
    # A few seconds of slack: issued_at and expires_at may be stamped from
    # instants microseconds apart. Twelve hours against forty-five minutes is
    # what this discriminates.
    drift = (row["expires_at"] - row["issued_at"]) - timedelta(minutes=720)
    assert abs(drift) < timedelta(seconds=5)

    _set_policy_value(conn, ABSOLUTE_TIMEOUT_KEY, 45)

    second = service.sign_in(conn, email=user["email"], password=password)
    row = _session_row(conn, second.session_id)
    drift = (row["expires_at"] - row["issued_at"]) - timedelta(minutes=45)
    assert abs(drift) < timedelta(seconds=5)

    # Past the absolute horizon the session is refused even though it was seen
    # a moment ago -- the absolute timeout is not the idle timeout.
    conn.execute(
        """
        update user_sessions
           set expires_at = now() - interval '1 minute', last_seen_at = now()
         where id = %s
        """,
        (second.session_id,),
    )
    with pytest.raises(service.SessionInvalid):
        service.authenticate(conn, second.token)


def test_access_v1_lockout_after_repeated_failures(conn):
    """access-v1: temporary lockout after repeated failures.

    The threshold is read from the policy table rather than guessed, so this
    test does not quietly invent a business value. The discriminating assertion
    is that the *correct* password is refused once locked -- and that a user one
    failure short of the threshold can still sign in, which rules out an
    implementation that locks everybody.
    """
    from src.access import service

    threshold = _policy_value(conn, LOCKOUT_THRESHOLD_KEY)
    assert threshold >= 1

    password = "us001-lockout-correct-horse-battery"
    victim = _create_user(conn, role="manager", password=password)
    survivor = _create_user(conn, role="manager", password=password)

    # One short of the threshold: still able to sign in.
    for _ in range(threshold - 1):
        with pytest.raises(service.InvalidCredentials):
            service.sign_in(conn, email=survivor["email"], password="wrong-password")
    assert service.sign_in(conn, email=survivor["email"], password=password).token

    # At the threshold. The final attempt may report either outcome; what the
    # policy fixes is the state afterwards, not the wording of the last refusal.
    for _ in range(threshold):
        with pytest.raises(service.AuthenticationFailed):
            service.sign_in(conn, email=victim["email"], password="wrong-password")

    with pytest.raises(service.AccountLocked):
        service.sign_in(conn, email=victim["email"], password=password)

    row = conn.execute(
        "select failed_sign_in_count, locked_until from users where id = %s",
        (victim["id"],),
    ).fetchone()
    assert row["failed_sign_in_count"] >= threshold
    assert row["locked_until"] is not None
    assert row["locked_until"] > utc_now()

    # No session was issued to the locked account at any point.
    sessions = conn.execute(
        "select count(*) as n from user_sessions where user_id = %s", (victim["id"],)
    ).fetchone()
    assert sessions["n"] == 0


def test_access_v1_session_id_rotates_on_sign_in(conn):
    """access-v1: rotate the session identifier on sign-in.

    Also fixes that the raw token is never what is stored. ``token_hash`` is a
    hash of the token, so the database must not contain the token itself.
    """
    from src.access import service

    password = "us001-rotate-correct-horse-battery"
    user = _create_user(conn, role="manager", password=password)

    first = service.sign_in(conn, email=user["email"], password=password)
    second = service.sign_in(conn, email=user["email"], password=password)

    assert first.session_id != second.session_id
    assert first.token != second.token

    rows = conn.execute(
        "select id, token_hash from user_sessions where user_id = %s", (user["id"],)
    ).fetchall()
    assert len(rows) == 2
    assert len({r["id"] for r in rows}) == 2
    assert len({r["token_hash"] for r in rows}) == 2

    stored = {r["token_hash"] for r in rows}
    assert first.token not in stored
    assert second.token not in stored

    # Rotation is not a cosmetic relabelling of one row: each session is
    # independently addressable and resolves to its own token.
    assert service.authenticate(conn, second.token).session_id == second.session_id


def test_access_v1_mfa_required_for_owner_and_admin(conn):
    """access-v1: MFA is required for Owner and Admin, optional for Manager.

    The claim under test is enforcement, not enrolment: a privileged user must
    not end up with a *usable* session on a password alone. Asserting the
    exception is not enough -- a implementation could raise and still have
    written a session row -- so the absence of any session is asserted too.
    """
    from src.access import service

    password = "us001-mfa-correct-horse-battery"

    for role in ("owner", "admin"):
        user = _create_user(conn, role=role, password=password, mfa_enabled=True)

        with pytest.raises(service.MfaRequired):
            service.sign_in(conn, email=user["email"], password=password)

        with pytest.raises(service.AuthenticationFailed):
            service.sign_in(
                conn, email=user["email"], password=password, totp_code="000000"
            )

        issued = conn.execute(
            "select count(*) as n from user_sessions where user_id = %s", (user["id"],)
        ).fetchone()
        assert issued["n"] == 0, f"{role} obtained a session without MFA"

    # A Manager, for whom MFA is optional, signs in on a password alone.
    manager = _create_user(
        conn, role="manager", password=password, mfa_enabled=False, mfa_secret=None
    )
    result = service.sign_in(conn, email=manager["email"], password=password)
    assert service.authenticate(conn, result.token).user_id == manager["id"]

    # The requirement is structural as well: a privileged user cannot be stored
    # with MFA switched off, so enforcement cannot be bypassed by a bad row.
    with pytest.raises(
        (psycopg.errors.IntegrityError, psycopg.errors.RaiseException)
    ):
        with conn.transaction():
            conn.execute(
                """
                insert into users (email, role, password_hash, mfa_enabled)
                values (%s, 'owner', 'not-a-real-hash', false)
                """,
                (_new_email("owner-no-mfa"),),
            )


def test_sc13_credentials_never_serialised(conn, client, migrated_database):
    """SC-13: credentials and MFA secrets are never serialised.

    access-v1: no password, token, or session identifier in logs, URLs,
    exports, audit payloads, or error messages. Checked at the three places
    they would actually escape -- the repository read, the stored session row,
    and the API response.
    """
    from src.access import repository, service

    password = "us001-sc13-correct-horse-battery"
    user = _create_user(conn, role="admin", password=password, mfa_enabled=True)

    stored = conn.execute(
        "select password_hash, mfa_secret from users where id = %s", (user["id"],)
    ).fetchone()

    # Excluded at the repository layer, not filtered later by each caller.
    read = repository.get_user(conn, user["id"])
    assert "password_hash" not in read
    assert "mfa_secret" not in read
    assert password not in str(read)
    assert stored["password_hash"] not in str(read)
    assert stored["mfa_secret"] not in str(read)

    # A session stores a hash of the token, never the token.
    manager_password = "us001-sc13-manager-correct-horse"
    manager = _create_user(conn, role="manager", password=manager_password)
    issued = service.sign_in(
        conn, email=manager["email"], password=manager_password
    )
    row = _session_row(conn, issued.session_id)
    assert row["token_hash"] != issued.token
    assert issued.token not in row["token_hash"]

    # Whatever the Owner decides about auditing sign-in, no audit payload may
    # carry a credential. This asserts nothing about whether rows exist.
    events = conn.execute(
        "select * from audit_events where actor_id = any(%s)",
        ([user["id"], manager["id"]],),
    ).fetchall()
    for event in events:
        text = str(event)
        assert manager_password not in text
        assert password not in text
        assert issued.token not in text
        assert row["token_hash"] not in text
        assert stored["password_hash"] not in text
        assert stored["mfa_secret"] not in text

    # And nothing leaks through the HTTP edge.
    http_password = "us001-sc13-http-correct-horse"
    with _committed(migrated_database) as c:
        http_user = _create_user(c, role="manager", password=http_password)
        http_hash = c.execute(
            "select password_hash from users where id = %s", (http_user["id"],)
        ).fetchone()["password_hash"]

    response = client.post(
        "/auth/session",
        json={"email": http_user["email"], "password": http_password},
    )
    assert response.status_code == 201
    assert http_password not in response.text
    assert http_hash not in response.text
    assert "password_hash" not in response.text
    assert "mfa_secret" not in response.text
    assert TOTP_TEST_SECRET not in response.text

    # An error message must not echo the credential back either.
    rejected = client.post(
        "/auth/session",
        json={"email": http_user["email"], "password": http_password + "-wrong"},
    )
    assert rejected.status_code == 401
    assert http_password not in rejected.text


# ---------------------------------------------------------------------------
# Audit findings F-01 .. F-11 (US-001 re-audit).
#
# Each of these pins down the specific behaviour a blocking or non-blocking
# finding demanded, so the fix cannot quietly regress the next time this
# module is touched.
# ---------------------------------------------------------------------------


def test_f01_unauthenticated_requests_are_prompted_to_authenticate_not_404(
    client, migrated_database
):
    """F-01: a request with no credential at all must be told to sign in,
    not treated as an out-of-scope lookup. 404 means "exists, but not
    yours"; an unauthenticated caller never got as far as "yours" at all, so
    collapsing the two let an unauthenticated write look like it simply
    failed to find anything, rather than prompting the caller to sign in.
    """
    with _committed(migrated_database) as c:
        store_id = _create_store(c, "F-01 Store")

    no_auth_list = client.get("/stores")
    assert no_auth_list.status_code == 401
    assert no_auth_list.json()["detail"]["code"] == "AUTHENTICATION_REQUIRED"

    no_auth_detail = client.get(f"/stores/{store_id}")
    assert no_auth_detail.status_code == 401
    assert no_auth_detail.json()["detail"]["code"] == "AUTHENTICATION_REQUIRED"

    no_auth_write = client.post(
        f"/stores/{store_id}/notes",
        json={"note": "x"},
        headers={"Idempotency-Key": str(uuid.uuid4())},
    )
    assert no_auth_write.status_code == 401
    assert no_auth_write.json()["detail"]["code"] == "AUTHENTICATION_REQUIRED"


def test_f02_unknown_account_and_wrong_password_cost_the_same_time(conn):
    """F-02: access-v1 requires identical response *timing*, not only
    identical wording, for an unknown account and a wrong password. A bare
    early return for an unknown email is orders of magnitude faster than a
    real bcrypt comparison, and that gap is itself an account-enumeration
    oracle.

    This is a timing test, not a benchmark, so the tolerance is deliberately
    generous -- wide enough to be reliable under CI jitter, narrow enough
    that a reintroduced fast path (a difference of two to three orders of
    magnitude, per the audit) still fails it. A fresh user is created for
    every "known account, wrong password" sample so the lockout threshold is
    never approached and never contaminates the timing.
    """
    import time

    from src.access import service

    password = "us001-f02-correct-horse-battery"

    def _time_unknown() -> float:
        start = time.perf_counter()
        with pytest.raises(service.InvalidCredentials):
            service.sign_in(
                conn, email=_new_email("f02-unknown"), password="whatever"
            )
        return time.perf_counter() - start

    def _time_known_wrong() -> float:
        user = _create_user(conn, role="manager", password=password)
        start = time.perf_counter()
        with pytest.raises(service.InvalidCredentials):
            service.sign_in(conn, email=user["email"], password="not-the-password")
        return time.perf_counter() - start

    unknown_times = [_time_unknown() for _ in range(3)]
    known_times = [_time_known_wrong() for _ in range(3)]

    avg_unknown = sum(unknown_times) / len(unknown_times)
    avg_known = sum(known_times) / len(known_times)

    assert avg_unknown > avg_known * 0.3, (
        f"unknown-account path ({avg_unknown:.4f}s avg) is far faster than a "
        f"known-account wrong password ({avg_known:.4f}s avg); the timing "
        "oracle F-02 closed may have reopened."
    )


def test_f03_failed_totp_code_counts_toward_lockout(conn):
    """F-03: a rejected TOTP code is a failed sign-in attempt exactly like a
    rejected password, and must count the same way. Before this fix, an
    attacker already holding a valid Owner/Admin password could brute-force
    the second factor without limit -- rate limiting is deliberately
    unimplemented (no window/multiplier/cap specified), so this counter was
    the only brake available.
    """
    from src.access import service

    threshold = _policy_value(conn, LOCKOUT_THRESHOLD_KEY)
    password = "us001-f03-correct-horse-battery"
    user = _create_user(conn, role="admin", password=password, mfa_enabled=True)

    for _ in range(threshold):
        with pytest.raises(service.AuthenticationFailed):
            service.sign_in(
                conn, email=user["email"], password=password, totp_code="000000"
            )

    row = conn.execute(
        "select failed_sign_in_count, locked_until from users where id = %s",
        (user["id"],),
    ).fetchone()
    assert row["failed_sign_in_count"] >= threshold
    assert row["locked_until"] is not None
    assert row["locked_until"] > utc_now()

    # Locked now: even the correct password and the correct TOTP code are
    # refused until the lock expires.
    with pytest.raises(service.AccountLocked):
        service.sign_in(conn, email=user["email"], password=password)


def test_f04_deactivating_a_user_invalidates_their_live_session_immediately(conn):
    """F-04: ``is_active`` must be honoured at validate time, not only at
    sign-in. Before this fix a user deactivated after signing in kept a
    fully working session until it happened to idle out or hit its absolute
    horizon -- up to twelve hours, across every store, for a deactivated
    Owner.
    """
    from src.access import service

    password = "us001-f04-correct-horse-battery"
    user = _create_user(conn, role="manager", password=password)

    issued = service.sign_in(conn, email=user["email"], password=password)
    assert service.authenticate(conn, issued.token).user_id == user["id"]

    conn.execute("update users set is_active = false where id = %s", (user["id"],))

    with pytest.raises(service.SessionInvalid):
        service.authenticate(conn, issued.token)

    # Defence in depth: resolve_scope must not trust is_active was already
    # checked by whoever called it -- the same principle already applied to
    # the MFA check in sign_in.
    with pytest.raises(service.SessionInvalid):
        service.resolve_scope(conn, user["id"])


def test_f04_deactivation_is_enforced_at_the_http_edge(client, migrated_database):
    """F-04, at the edge: the same session token stops working the moment
    the underlying user is deactivated, with no re-authentication involved.
    """
    password = "us001-f04-http-correct-horse"
    with _committed(migrated_database) as c:
        store_id = _create_store(c, "F-04 Store")
        user = _create_user(c, role="manager", password=password)
        _assign(c, user["id"], store_id)

    token = _sign_in_over_http(client, user)
    working = client.get("/stores", headers=_auth(token))
    assert str(store_id) in [s["id"] for s in working.json()["data"]]

    with _committed(migrated_database) as c:
        c.execute("update users set is_active = false where id = %s", (user["id"],))

    deactivated = client.get("/stores", headers=_auth(token))
    assert deactivated.status_code == 401
    assert deactivated.json()["detail"]["code"] == "AUTHENTICATION_REQUIRED"


def test_f05_malformed_sign_in_body_does_not_echo_the_password(client):
    """F-05: Pydantic v2 attaches an ``input`` key to a ``missing`` error
    that, for a required field on the parent model, is the *whole* request
    body. Omitting ``email`` while still sending ``password`` used to put
    the caller's actual password in the 422 response -- SC-13's governing
    clause forbids a password in an error message, and this was a framework
    default rather than anything this codebase wrote.
    """
    secret_password = "us001-f05-should-never-appear-anywhere"
    r = client.post("/auth/session", json={"password": secret_password})
    assert r.status_code == 422
    assert secret_password not in r.text


def test_f06_sign_in_emits_the_documented_audit_events(conn):
    """F-06: ``auth_sign_in_succeeded``, ``auth_sign_in_failed`` and
    ``auth_account_locked`` are required by the API design document, which
    governs where the user stories are silent (walmart-user-stories.md:24).
    ``auth_account_unlocked`` is deliberately never emitted in *this*
    scenario -- it only fires on the automatic, expiry-driven unlock path
    (see ``test_f12_automatic_unlock_emits_an_audit_event``), and this
    scenario never drives a lock to expiry. Admin-initiated unlock (US-004)
    remains out of scope entirely, here and there.
    """
    from src.access import service

    threshold = _policy_value(conn, LOCKOUT_THRESHOLD_KEY)
    password = "us001-f06-correct-horse-battery"
    user = _create_user(conn, role="manager", password=password)

    def _event_types() -> list[str]:
        rows = conn.execute(
            "select event_type from audit_events where actor_id = %s "
            "order by created_at",
            (user["id"],),
        ).fetchall()
        return [r["event_type"] for r in rows]

    result = service.sign_in(conn, email=user["email"], password=password)
    assert "auth_sign_in_succeeded" in _event_types()

    for _ in range(threshold):
        with pytest.raises(service.AuthenticationFailed):
            service.sign_in(conn, email=user["email"], password="wrong-password")

    types = _event_types()
    assert types.count("auth_sign_in_failed") >= threshold
    assert "auth_account_locked" in types
    assert "auth_account_unlocked" not in types

    # SC-13: whatever is recorded carries no credential, regardless of what
    # the sign-in attempt submitted.
    rows = conn.execute(
        "select * from audit_events where actor_id = %s", (user["id"],)
    ).fetchall()
    for row in rows:
        text = str(row)
        assert password not in text
        assert "wrong-password" not in text
        assert result.token not in text


def test_f06_failed_sign_in_for_unknown_email_does_not_log_the_email(conn):
    """F-06 / SC-13 together: a failed sign-in for an email that resolves to
    no account must not record that email -- doing so would turn the audit
    log itself into an account-enumeration list, defeating the point of
    F-02's timing fix at the data layer instead of the HTTP layer.
    """
    from src.access import service

    email = _new_email("f06-unknown")
    with pytest.raises(service.InvalidCredentials):
        service.sign_in(conn, email=email, password="whatever")

    rows = conn.execute(
        "select * from audit_events where event_type = 'auth_sign_in_failed' "
        "and actor_id is null"
    ).fetchall()
    assert len(rows) >= 1
    for row in rows:
        assert email not in str(row)


def test_f08_failed_attempt_counter_decays_after_the_lock_expires(conn):
    """F-08: once a lock's duration has passed, a single further wrong
    password must not immediately relock the account for the full duration
    again. With F-02 closed, account existence is no longer discoverable
    from the outside, which is exactly what would turn an un-decayed
    counter into an indefinite denial of service against any known account
    -- including the Owner's.
    """
    from src.access import service

    threshold = _policy_value(conn, LOCKOUT_THRESHOLD_KEY)
    password = "us001-f08-correct-horse-battery"
    user = _create_user(conn, role="manager", password=password)

    for _ in range(threshold):
        with pytest.raises(service.AuthenticationFailed):
            service.sign_in(conn, email=user["email"], password="wrong")

    with pytest.raises(service.AccountLocked):
        service.sign_in(conn, email=user["email"], password=password)

    # The lock has expired.
    conn.execute(
        "update users set locked_until = now() - interval '1 second' where id = %s",
        (user["id"],),
    )

    # One further wrong password after expiry is judged on its own, not
    # treated as crossing an already-reached threshold.
    with pytest.raises(service.InvalidCredentials):
        service.sign_in(conn, email=user["email"], password="still-wrong")

    row = conn.execute(
        "select failed_sign_in_count, locked_until from users where id = %s",
        (user["id"],),
    ).fetchone()
    assert row["locked_until"] is None
    assert row["failed_sign_in_count"] < threshold

    # And the real password now works -- the account was not left locked by
    # the decay itself.
    assert service.sign_in(conn, email=user["email"], password=password).token


def test_f12_automatic_unlock_emits_an_audit_event(conn):
    """F-12: the decay path above (F-08) is a security-control state
    transition -- a lock being lifted -- exactly like the lock it reverses,
    and must leave the same kind of trace. Before this, ``audit_events``
    showed ``auth_account_locked`` with no corresponding exit, so an
    investigator could not tell a continuing attack from a fresh one once
    the lock had quietly expired.

    This is the automatic, expiry-driven path only. Admin-initiated unlock
    (US-004) is still out of scope and still emits nothing here.
    """
    from src.access import service

    threshold = _policy_value(conn, LOCKOUT_THRESHOLD_KEY)
    password = "us001-f12-correct-horse-battery"
    user = _create_user(conn, role="manager", password=password)

    def _event_types() -> list[str]:
        rows = conn.execute(
            "select event_type from audit_events where actor_id = %s "
            "order by created_at",
            (user["id"],),
        ).fetchall()
        return [r["event_type"] for r in rows]

    for _ in range(threshold):
        with pytest.raises(service.AuthenticationFailed):
            service.sign_in(conn, email=user["email"], password="wrong")

    with pytest.raises(service.AccountLocked):
        service.sign_in(conn, email=user["email"], password=password)

    assert "auth_account_unlocked" not in _event_types()

    # The lock has expired.
    conn.execute(
        "update users set locked_until = now() - interval '1 second' where id = %s",
        (user["id"],),
    )

    # One further wrong password after expiry judges the attempt on its
    # own (F-08) -- and the decay that makes that possible is now audited.
    with pytest.raises(service.InvalidCredentials):
        service.sign_in(conn, email=user["email"], password="still-wrong")

    types = _event_types()
    assert "auth_account_unlocked" in types

    # SC-13: the unlock event carries no credential, same as every other
    # sign-in audit row.
    unlock_rows = conn.execute(
        "select * from audit_events where actor_id = %s and event_type = %s",
        (user["id"], "auth_account_unlocked"),
    ).fetchall()
    assert unlock_rows
    for row in unlock_rows:
        text = str(row)
        assert password not in text
        assert "still-wrong" not in text


def test_f09_mfa_enabled_without_a_secret_is_structurally_rejected(conn):
    """F-09: ``mfa_enabled = true, mfa_secret = null`` used to satisfy every
    constraint, producing a permanently unsignable-in account that fails
    with a generic "invalid authentication code" nobody could diagnose.
    Structural for every role, not only Owner and Admin -- enabling MFA
    without a secret is nonsensical regardless of who does it.
    """
    with pytest.raises(
        (psycopg.errors.IntegrityError, psycopg.errors.RaiseException)
    ):
        with conn.transaction():
            conn.execute(
                """
                insert into users (email, role, password_hash, mfa_enabled, mfa_secret)
                values (%s, 'manager', 'not-a-real-hash', true, null)
                """,
                (_new_email("manager-mfa-no-secret"),),
            )


def test_f10_session_reprs_never_render_the_raw_secret(conn):
    """F-10: the dataclass-generated repr would render the raw session
    token (``SignInResult``) or the raw session id (``AuthenticatedSession``).
    Nothing logs either today, but this module's whole premise is that
    exclusion must be structural rather than remembered -- a repr is exactly
    the kind of thing a future debug log reaches for without thinking.
    """
    from src.access import service

    password = "us001-f10-correct-horse-battery"
    user = _create_user(conn, role="manager", password=password)
    result = service.sign_in(conn, email=user["email"], password=password)

    assert result.token not in repr(result)
    assert "redacted" in repr(result)

    session = service.authenticate(conn, result.token)
    assert str(session.session_id) not in repr(session)
    assert "redacted" in repr(session)
