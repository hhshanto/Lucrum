"""US-004 -- Manage store assignments. Module: Team. Screens: UI-14.

docs/walmart-user-stories.md:768-778 (verbatim):

    As an authorized access administrator, I want to assign Managers to
    stores so that multiple Managers can collaborate without assigning
    every order to an individual.

    AC-01: Given two Managers and one store, when both receive
    assignments, then both can access permitted store records
    independently.
    AC-02: Given one Manager and two stores, when assigned to both, then
    access is available to both without creating duplicate user
    identities.
    AC-03: Given assignment removal, when the Manager makes a subsequent
    protected request, then revoked access is denied even if an old
    screen remains open.
    AC-04: Given assignment changes, when saving, then the actor,
    affected user/store, and change are audited; historical orders are
    not reassigned or repriced.

``src/access/__init__.py:4`` says plainly: "store assignment management
are later stories -- US-001 covers [identity/sessions only]." There is no
``GET /stores/{store_id}/managers``, no ``POST /stores/{store_id}/managers``,
no ``DELETE /stores/{store_id}/managers/{user_id}``, and no assignment
service anywhere in ``src/``. Every HTTP-level test below is expected to
fail against routes that do not exist yet.

-------------------------------------------------------------------------
Decisions made to write these tests, recorded so they can be checked
rather than silently assumed (spec-oracle already confirmed items 1-5;
6-9 are this file's own, disclosed, reasoned choices):

1. **Who may assign/remove.** Owner-only. The capability matrix
   (``walmart-user-stories.md:670``, "Assign managers to stores") is
   Yes/No/No -- identical shape to "Create store" (:668), which has no
   per-store scope dependency at all (the Owner has implicit access to
   every store; Admin and Manager are rejected by role alone, before any
   store lookup). Both Admin and Manager are tested, both directions
   (against a store they ARE assigned to, and against a store_id that
   does not exist at all), mirroring US-003's AC-03 ordering discipline:
   a role rejection must not depend on whether the store happens to
   exist.

2. **Scope of this story is Manager assignment only.** Every AC names
   Managers specifically. ``user_store_assignments`` also holds Admin
   rows (confirmed in ``database-schema.md`` and the built migration),
   but no capability row or AC anywhere names who may assign an *Admin*
   to a store -- a genuine, unnamed gap. Not built or tested here; see
   ``TestGapAdminAssignmentAuthorityIsUndefined`` below, a one-line skip,
   not a guess.

3. **No user-creation story exists.** AC-02's "without creating duplicate
   user identities" presumes the Manager identity already exists. This
   file seeds users directly via ``_create_user`` (same helper shape as
   every prior story's test file) and tests only the assignment
   join-table surface.

4. **AC-04's "historical orders are not reassigned or repriced"** is
   forward-looking to ``order_assignments``, a table that does not exist
   yet (slice 2 is not built). The audit half is tested for real (actor,
   target user+store, and the grant/revoke distinction are all recorded).
   The "no retroactive effect" half is tested in the only currently
   honest sense: a grant or revoke touches ``user_store_assignments`` and
   ``audit_events`` and nothing else reachable today -- in particular,
   neither the acted-on store's row nor the acted-on user's row changes.

5. **Schema reality.** ``user_store_assignments``
   (``src/db/migrations/002_access.sql:120-125``) is a bare
   composite-primary-key table: ``(user_id, store_id)``. No ``id``, no
   timestamp, no ``created_by``, no active/inactive flag. AC-03 is
   satisfiable by a hard ``DELETE`` -- presence-of-row is exactly what
   ``access_service.resolve_scope`` (``src/access/service.py:477-480``)
   already checks, the one true scope-resolution path, not duplicated
   here. AC-04's audit requirement is satisfied by ``audit_events``
   alone; it needs no extra column on the assignment table itself.

6. **Endpoint paths.** ``api-service-design.md:238-248`` names
   ``GET /stores/{storeId}/managers`` (list) and
   ``POST /stores/{storeId}/managers`` (assign) verbatim -- used as-is.
   Its ``PATCH /stores/{storeId}/managers/{assignmentId}`` presupposes a
   per-row ``assignmentId`` the built schema does not have (point 5
   above). This file uses ``DELETE /stores/{store_id}/managers/{user_id}``
   instead -- addressed by the natural key the table actually has, a
   disclosed adaptation of the doc's path family, not a silent deviation.
   ``POST`` body shape ``{"user_id": "<uuid>"}`` is this file's own
   reasoned assumption; neither document specifies one. Every write
   (including the ``DELETE``) carries ``Idempotency-Key``, exactly like
   every other write endpoint in ``src/main.py`` -- this project's
   slice-0 non-negotiable has no carve-out for DELETE.

7. **Audit event type.** ``api-service-design.md:1200``: "Store
   assignment granted or revoked | ``store_assignment_changed``" -- one
   type for both directions, no documented field distinguishing which.
   This file assumes a discriminator key ``"action"`` with value
   ``"granted"`` or ``"revoked"`` inside the event's ``after_state``,
   disclosed exactly as chosen-not-specified (same discipline as B-12 for
   ``store_created``), and pinned with a named test the same way
   ``store_created`` is pinned today (``test_us003...::TestF27...``).
   ``entity_type`` is assumed to be ``"user_store_assignment"``,
   ``entity_id`` the affected user's id (the assignment itself has no
   surrogate key to be the entity_id of; the affected *user* is the more
   stable identity to anchor the audit row to, and ``store_id`` -- a
   real column on ``audit_events`` -- already carries the store half).

8. **Response envelope and status codes.** Follows every other write
   endpoint in ``src/main.py``: 201 for a grant that creates a new row,
   200 for a revoke (nothing is "created"), 200 with
   ``meta.replayed: true`` for a replay of either, never a bare 204 --
   unlike ``DELETE /auth/session`` (which is unconditionally idempotent
   and carries no ``Idempotency-Key`` at all), this DELETE is a real,
   audited financial-adjacent write and needs the same replay contract
   POST and PATCH already have elsewhere in this codebase.

9. **Error translation for the two referential-integrity edges,
   following the ``DuplicateStoreKey`` precedent** (a constraint
   violation becomes a named, non-500 error, never a guessed status
   picked for its own sake):
   - Granting a ``user_id`` that does not exist as a ``users`` row at all
     is assumed to be ``404`` with ``{"code": "USER_NOT_FOUND"}`` -- "no
     such identity", the same class of answer as a nonexistent store,
     under a distinct code so the two 404s are never confused with each
     other by a client.
   - Granting a ``(user_id, store_id)`` pair that is already assigned,
     under a *fresh* ``Idempotency-Key`` (not a replay), is assumed to be
     ``409`` with ``{"code": "ALREADY_ASSIGNED"}`` -- a genuine conflict,
     the same status family as ``DuplicateStoreKey``, never a silent
     success and never a raw ``IntegrityError`` leaking as a 500.

Conventions carried over from ``test_us003_create_store.py`` /
``test_us001_sign_in.py``: database tests would use the rolled-back
``conn`` fixture (none needed here -- every assertion in this file is
either over HTTP or a direct read on the committed side, matching how
US-003 tests its own write endpoints); HTTP tests seed rows on a second,
committed connection because the API uses its own pool; ``src.*`` imports
happen inside each test so a genuinely missing module/attribute fails one
test, not collection; a decoy store/user/assignment is seeded in every
scoping-shaped test so the assertion is still meaningful when this file
runs alone against a fresh database (the F-23 discipline); 403 (role
rejection) is never confused with 404 (scope/existence), in either
direction (the F-30/F-63 discipline).
"""

from __future__ import annotations

import json
import uuid
from contextlib import contextmanager
from datetime import timedelta

import psycopg
import pytest
import pyotp
from fastapi.testclient import TestClient
from psycopg.rows import dict_row

from src import main

TOTP_TEST_SECRET = "JBSWY3DPEHPK3PXP"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _new_email(label: str) -> str:
    return f"us004-{label}-{uuid.uuid4().hex}@example.test"


def _totp_code(secret: str = TOTP_TEST_SECRET) -> str:
    return pyotp.TOTP(secret).now()


def _create_user(c, *, role, password, mfa_enabled=None, mfa_secret=None):
    """Seed a user, committing-connection style. Mirrors test_us003/001."""
    from src.access import passwords

    if mfa_enabled is None:
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
    return {"id": row["id"], "email": email, "password": password, "role": role}


def _create_store_db(c, name, *, timezone="America/New_York",
                      reporting_currency_code="USD", managed_from_year=2026,
                      managed_from_month=7):
    """Seed a store directly, for decoys and fixtures -- not the behaviour
    under test (that is the assignment join-table and the endpoints acting
    on it, not store creation itself)."""
    row = c.execute(
        """
        insert into stores (name, walmart_store_key, timezone,
                            reporting_currency_code, managed_from_year,
                            managed_from_month)
        values (%s, %s, %s, %s, %s, %s)
        returning id
        """,
        (
            name,
            f"US004-{uuid.uuid4().hex[:12]}",
            timezone,
            reporting_currency_code,
            managed_from_year,
            managed_from_month,
        ),
    ).fetchone()
    return row["id"]


def _assign_directly(c, user_id, store_id):
    """Seed an assignment directly, bypassing the endpoint under test --
    used only for decoy fixtures, never for the grant being asserted on."""
    c.execute(
        "insert into user_store_assignments (user_id, store_id) values (%s, %s)",
        (user_id, store_id),
    )


@contextmanager
def _committed(url):
    with psycopg.connect(url, row_factory=dict_row) as c:
        yield c
        c.commit()


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _sign_in(client, user, *, with_mfa: bool = False) -> str:
    payload = {"email": user["email"], "password": user["password"]}
    if with_mfa:
        payload["totp_code"] = _totp_code()
    r = client.post("/auth/session", json=payload)
    assert r.status_code == 201, r.text
    token = r.json()["data"]["token"]
    assert token
    return token


def _idempotency_key() -> str:
    return str(uuid.uuid4())


def _fetch_audit_rows(url: str, operation_id: str) -> list[dict]:
    with psycopg.connect(url, row_factory=dict_row) as c:
        return c.execute(
            "select * from audit_events where operation_id = %s", (operation_id,)
        ).fetchall()


def _fetch_assignment_audit_rows(url: str, *, user_id, store_id) -> list[dict]:
    with psycopg.connect(url, row_factory=dict_row) as c:
        return c.execute(
            """
            select * from audit_events
             where event_type = 'store_assignment_changed'
               and entity_id = %s
               and store_id = %s
             order by created_at
            """,
            (user_id, store_id),
        ).fetchall()


def _count_assignment_rows(url: str, *, user_id, store_id) -> int:
    with psycopg.connect(url) as c:
        return c.execute(
            "select count(*) from user_store_assignments "
            "where user_id = %s and store_id = %s",
            (user_id, store_id),
        ).fetchone()[0]


def _count_users_with_email(url: str, email: str) -> int:
    with psycopg.connect(url) as c:
        return c.execute(
            "select count(*) from users where email = %s", (email,)
        ).fetchone()[0]


def _fetch_user_row(url: str, user_id) -> dict:
    with psycopg.connect(url, row_factory=dict_row) as c:
        return c.execute(
            "select * from users where id = %s", (str(user_id),)
        ).fetchone()


def _fetch_store_row(url: str, store_id) -> dict:
    with psycopg.connect(url, row_factory=dict_row) as c:
        return c.execute(
            "select * from stores where id = %s", (str(store_id),)
        ).fetchone()


@pytest.fixture
def client(migrated_database):
    with TestClient(main.app) as c:
        yield c


def _grant(client, token, store_id, user_id, *, operation_id=None):
    headers = {**_auth(token), "Idempotency-Key": operation_id or _idempotency_key()}
    return client.post(
        f"/stores/{store_id}/managers", json={"user_id": str(user_id)}, headers=headers
    )


def _revoke(client, token, store_id, user_id, *, operation_id=None):
    headers = {**_auth(token), "Idempotency-Key": operation_id or _idempotency_key()}
    return client.delete(f"/stores/{store_id}/managers/{user_id}", headers=headers)


def _list_managers(client, token, store_id):
    return client.get(f"/stores/{store_id}/managers", headers=_auth(token))


# ---------------------------------------------------------------------------
# AC-01 -- two Managers, one store, independent access
# ---------------------------------------------------------------------------


class TestAC01TwoManagersIndependentAccess:
    def test_ac01_both_granted_managers_can_independently_reach_the_store(
        self, client, migrated_database
    ):
        with _committed(migrated_database) as c:
            store_id = str(_create_store_db(c, "AC01 Shared Store"))
            owner = _create_user(c, role="owner", password="ac01-owner-pw")
            manager_a = _create_user(c, role="manager", password="ac01-mgr-a-pw")
            manager_b = _create_user(c, role="manager", password="ac01-mgr-b-pw")

            # F-23 decoy: a third manager, holding the only assignment to a
            # different store, so a scope resolver that dropped its user_id
            # predicate cannot pass by accident even on a fresh database.
            decoy_store = str(_create_store_db(c, "AC01 Decoy Store"))
            decoy = _create_user(c, role="manager", password="ac01-decoy-pw")
            _assign_directly(c, decoy["id"], decoy_store)

        owner_token = _sign_in(client, owner, with_mfa=True)

        granted_a = _grant(client, owner_token, store_id, manager_a["id"])
        assert granted_a.status_code == 201, granted_a.text
        granted_b = _grant(client, owner_token, store_id, manager_b["id"])
        assert granted_b.status_code == 201, granted_b.text

        token_a = _sign_in(client, manager_a)
        token_b = _sign_in(client, manager_b)

        detail_a = client.get(f"/stores/{store_id}", headers=_auth(token_a))
        assert detail_a.status_code == 200
        assert detail_a.json()["data"]["id"] == store_id

        detail_b = client.get(f"/stores/{store_id}", headers=_auth(token_b))
        assert detail_b.status_code == 200
        assert detail_b.json()["data"]["id"] == store_id

        list_a = client.get("/stores", headers=_auth(token_a))
        list_b = client.get("/stores", headers=_auth(token_b))
        assert store_id in [s["id"] for s in list_a.json()["data"]]
        assert store_id in [s["id"] for s in list_b.json()["data"]]

        # The decoy's manager never gained access from either grant.
        assert decoy_store not in [s["id"] for s in list_a.json()["data"]]
        assert decoy_store not in [s["id"] for s in list_b.json()["data"]]

    def test_ac01_granting_one_manager_does_not_grant_the_other(
        self, client, migrated_database
    ):
        """The discriminating case: an implementation that (wrongly) grants
        every Manager when one is assigned -- e.g. a bug that resolves
        scope by role instead of by row -- would pass a test that only
        checked the granted Manager. This checks the one NOT yet granted
        is still refused.
        """
        with _committed(migrated_database) as c:
            store_id = str(_create_store_db(c, "AC01 Not Yet Both Store"))
            owner = _create_user(c, role="owner", password="ac01-single-owner-pw")
            manager_a = _create_user(c, role="manager", password="ac01-single-a-pw")
            manager_b = _create_user(c, role="manager", password="ac01-single-b-pw")
        owner_token = _sign_in(client, owner, with_mfa=True)

        granted = _grant(client, owner_token, store_id, manager_a["id"])
        assert granted.status_code == 201, granted.text

        token_b = _sign_in(client, manager_b)
        detail_b = client.get(f"/stores/{store_id}", headers=_auth(token_b))
        assert detail_b.status_code == 404
        assert store_id not in client.get("/stores", headers=_auth(token_b)).text


# ---------------------------------------------------------------------------
# AC-02 -- one Manager, two stores, no duplicate identity
# ---------------------------------------------------------------------------


class TestAC02OneManagerTwoStoresNoDuplicateIdentity:
    def test_ac02_one_manager_assigned_to_two_stores_sees_both(
        self, client, migrated_database
    ):
        with _committed(migrated_database) as c:
            store_1 = str(_create_store_db(c, "AC02 Store One"))
            store_2 = str(_create_store_db(c, "AC02 Store Two"))
            owner = _create_user(c, role="owner", password="ac02-owner-pw")
            manager = _create_user(c, role="manager", password="ac02-mgr-pw")

            decoy_store = str(_create_store_db(c, "AC02 Decoy Store"))
            decoy = _create_user(c, role="manager", password="ac02-decoy-pw")
            _assign_directly(c, decoy["id"], decoy_store)
        owner_token = _sign_in(client, owner, with_mfa=True)

        first = _grant(client, owner_token, store_1, manager["id"])
        assert first.status_code == 201, first.text
        second = _grant(client, owner_token, store_2, manager["id"])
        assert second.status_code == 201, second.text

        manager_token = _sign_in(client, manager)
        listing = client.get("/stores", headers=_auth(manager_token))
        ids = {s["id"] for s in listing.json()["data"]}
        assert store_1 in ids
        assert store_2 in ids
        assert decoy_store not in ids

    def test_ac02_assigning_the_same_manager_to_a_second_store_creates_no_new_user_row(
        self, client, migrated_database
    ):
        """The concrete form of 'without creating duplicate user
        identities': the user id returned by each grant is literally
        identical, and exactly one ``users`` row ever exists for that
        email -- a hypothetical implementation bug where 'assign' secretly
        re-creates identity on a second store would fail this.
        """
        with _committed(migrated_database) as c:
            store_1 = str(_create_store_db(c, "AC02 Identity Store One"))
            store_2 = str(_create_store_db(c, "AC02 Identity Store Two"))
            owner = _create_user(c, role="owner", password="ac02-identity-owner-pw")
            manager = _create_user(c, role="manager", password="ac02-identity-mgr-pw")
        owner_token = _sign_in(client, owner, with_mfa=True)

        assert _count_users_with_email(migrated_database, manager["email"]) == 1

        first = _grant(client, owner_token, store_1, manager["id"])
        assert first.status_code == 201, first.text
        second = _grant(client, owner_token, store_2, manager["id"])
        assert second.status_code == 201, second.text

        # The user id is unchanged across both grants.
        assert first.json()["data"]["user_id"] == str(manager["id"])
        assert second.json()["data"]["user_id"] == str(manager["id"])

        assert _count_users_with_email(migrated_database, manager["email"]) == 1


# ---------------------------------------------------------------------------
# AC-03 -- revoked access is denied on the very next request
# ---------------------------------------------------------------------------


class TestAC03RevocationDeniesSubsequentRequests:
    def test_ac03_revoked_manager_is_denied_on_the_next_request_same_session(
        self, client, migrated_database
    ):
        """Mirrors US-001 AC-03's discipline exactly, one layer up: the
        Manager's own *session* is never touched by an assignment change
        (sign-out and assignment are different dimensions) -- the SAME
        token that reached the store a moment ago must stop reaching it
        the instant the assignment is withdrawn, with no re-authentication
        involved and no stale screen magically still working.
        """
        with _committed(migrated_database) as c:
            store_id = str(_create_store_db(c, "AC03 Revoke Store"))
            owner = _create_user(c, role="owner", password="ac03-owner-pw")
            manager = _create_user(c, role="manager", password="ac03-mgr-pw")

            decoy_store = str(_create_store_db(c, "AC03 Decoy Store"))
            decoy = _create_user(c, role="manager", password="ac03-decoy-pw")
            _assign_directly(c, decoy["id"], decoy_store)
        owner_token = _sign_in(client, owner, with_mfa=True)
        manager_token = _sign_in(client, manager)

        granted = _grant(client, owner_token, store_id, manager["id"])
        assert granted.status_code == 201, granted.text

        before = client.get(f"/stores/{store_id}", headers=_auth(manager_token))
        assert before.status_code == 200
        assert before.json()["data"]["id"] == store_id

        revoked = _revoke(client, owner_token, store_id, manager["id"])
        assert revoked.status_code == 200, revoked.text

        # The SAME manager session, re-used, on the very next request --
        # not a fresh sign-in -- must now be denied.
        after_detail = client.get(f"/stores/{store_id}", headers=_auth(manager_token))
        after_list = client.get("/stores", headers=_auth(manager_token))

        nonexistent = client.get(f"/stores/{uuid.uuid4()}", headers=_auth(manager_token))
        assert after_detail.status_code == nonexistent.status_code == 404
        assert after_detail.json() == nonexistent.json()
        assert store_id not in [s["id"] for s in after_list.json()["data"]]
        assert store_id not in after_detail.text

    def test_ac03_revoking_one_store_does_not_revoke_the_managers_other_assignment(
        self, client, migrated_database
    ):
        """The discriminating case: revoking must act on exactly the
        targeted (user, store) pair -- a careless implementation keyed on
        user_id alone (e.g. deleting every row for that user) would wrongly
        revoke a completely different store too.
        """
        with _committed(migrated_database) as c:
            store_revoked = str(_create_store_db(c, "AC03 Revoked Store"))
            store_kept = str(_create_store_db(c, "AC03 Kept Store"))
            owner = _create_user(c, role="owner", password="ac03-kept-owner-pw")
            manager = _create_user(c, role="manager", password="ac03-kept-mgr-pw")
        owner_token = _sign_in(client, owner, with_mfa=True)

        assert _grant(client, owner_token, store_revoked, manager["id"]).status_code == 201
        assert _grant(client, owner_token, store_kept, manager["id"]).status_code == 201

        manager_token = _sign_in(client, manager)
        revoked = _revoke(client, owner_token, store_revoked, manager["id"])
        assert revoked.status_code == 200, revoked.text

        listing = client.get("/stores", headers=_auth(manager_token))
        ids = {s["id"] for s in listing.json()["data"]}
        assert store_revoked not in ids
        assert store_kept in ids

        still_reachable = client.get(f"/stores/{store_kept}", headers=_auth(manager_token))
        assert still_reachable.status_code == 200


# ---------------------------------------------------------------------------
# AC-04 -- audited, and no retroactive effect on anything else reachable
# ---------------------------------------------------------------------------


class TestAC04AuditedAndNoRetroactiveEffect:
    def test_ac04_grant_is_audited_with_actor_target_and_action(
        self, client, migrated_database
    ):
        with _committed(migrated_database) as c:
            store_id = str(_create_store_db(c, "AC04 Grant Audit Store"))
            owner = _create_user(c, role="owner", password="ac04-grant-owner-pw")
            manager = _create_user(c, role="manager", password="ac04-grant-mgr-pw")
        owner_token = _sign_in(client, owner, with_mfa=True)

        op_id = _idempotency_key()
        r = _grant(client, owner_token, store_id, manager["id"], operation_id=op_id)
        assert r.status_code == 201, r.text

        rows = _fetch_audit_rows(migrated_database, op_id)
        assert len(rows) == 1
        row = rows[0]
        assert row["event_type"] == "store_assignment_changed"
        assert str(row["actor_id"]) == str(owner["id"])
        assert str(row["store_id"]) == store_id
        assert str(row["entity_id"]) == str(manager["id"])

        payload_text = json.dumps(dict(row), default=str)
        assert "granted" in payload_text
        assert str(manager["id"]) in payload_text
        assert store_id in payload_text

    def test_ac04_revoke_is_audited_with_actor_target_and_action(
        self, client, migrated_database
    ):
        with _committed(migrated_database) as c:
            store_id = str(_create_store_db(c, "AC04 Revoke Audit Store"))
            owner = _create_user(c, role="owner", password="ac04-revoke-owner-pw")
            manager = _create_user(c, role="manager", password="ac04-revoke-mgr-pw")
        owner_token = _sign_in(client, owner, with_mfa=True)

        assert _grant(client, owner_token, store_id, manager["id"]).status_code == 201

        op_id = _idempotency_key()
        r = _revoke(client, owner_token, store_id, manager["id"], operation_id=op_id)
        assert r.status_code == 200, r.text

        rows = _fetch_audit_rows(migrated_database, op_id)
        assert len(rows) == 1
        row = rows[0]
        assert row["event_type"] == "store_assignment_changed"
        assert str(row["actor_id"]) == str(owner["id"])
        assert str(row["store_id"]) == store_id
        assert str(row["entity_id"]) == str(manager["id"])

        payload_text = json.dumps(dict(row), default=str)
        assert "revoked" in payload_text
        assert "granted" not in payload_text or row.get("before_state") is not None

        # The grant and the revoke are two distinct audit rows, both
        # discoverable against the same (user, store) pair -- a careless
        # "upsert" implementation that overwrote one row in place would
        # lose the grant's own trail.
        both = _fetch_assignment_audit_rows(
            migrated_database, user_id=manager["id"], store_id=store_id
        )
        assert len(both) == 2

    def test_ac04_revocation_does_not_alter_the_managers_own_identity_or_other_assignments(
        self, client, migrated_database
    ):
        """The concrete, currently-testable form of 'historical ... are not
        reassigned or repriced': there is no ``order_assignments`` table
        yet (slice 2), so this asserts the only thing reachable today --
        the Manager's ``users`` row and their OTHER assignment are
        byte-identical before and after, and they can still sign in and
        see the store they were not removed from.
        """
        with _committed(migrated_database) as c:
            store_revoked = str(_create_store_db(c, "AC04 Identity Revoked Store"))
            store_kept = str(_create_store_db(c, "AC04 Identity Kept Store"))
            owner = _create_user(c, role="owner", password="ac04-identity-owner-pw")
            manager = _create_user(c, role="manager", password="ac04-identity-mgr-pw")
        owner_token = _sign_in(client, owner, with_mfa=True)

        assert _grant(client, owner_token, store_revoked, manager["id"]).status_code == 201
        assert _grant(client, owner_token, store_kept, manager["id"]).status_code == 201

        before_user = _fetch_user_row(migrated_database, manager["id"])

        revoked = _revoke(client, owner_token, store_revoked, manager["id"])
        assert revoked.status_code == 200, revoked.text

        after_user = _fetch_user_row(migrated_database, manager["id"])
        assert after_user["email"] == before_user["email"]
        assert after_user["role"] == before_user["role"]
        assert after_user["password_hash"] == before_user["password_hash"]
        assert after_user["is_active"] == before_user["is_active"]
        assert after_user["created_at"] == before_user["created_at"]

        # The kept assignment survives untouched, and the manager can
        # still sign in and reach it.
        assert _count_assignment_rows(
            migrated_database, user_id=manager["id"], store_id=store_kept
        ) == 1
        manager_token = _sign_in(client, manager)
        still_there = client.get(f"/stores/{store_kept}", headers=_auth(manager_token))
        assert still_there.status_code == 200

    def test_ac04_a_grant_does_not_alter_the_stores_own_row(
        self, client, migrated_database
    ):
        """The store side of the same claim: granting a Manager must not
        touch the store's own identity fields or ``updated_at`` -- an
        assignment is not a store settings change."""
        with _committed(migrated_database) as c:
            store_id = str(_create_store_db(c, "AC04 Store Untouched"))
            owner = _create_user(c, role="owner", password="ac04-store-owner-pw")
            manager = _create_user(c, role="manager", password="ac04-store-mgr-pw")
        owner_token = _sign_in(client, owner, with_mfa=True)

        before = _fetch_store_row(migrated_database, store_id)

        granted = _grant(client, owner_token, store_id, manager["id"])
        assert granted.status_code == 201, granted.text

        after = _fetch_store_row(migrated_database, store_id)
        assert after["name"] == before["name"]
        assert after["timezone"] == before["timezone"]
        assert after["reporting_currency_code"] == before["reporting_currency_code"]
        assert after["updated_at"] == before["updated_at"]


# ---------------------------------------------------------------------------
# Rejection -- Owner-only, role checked before store existence/scope
# ---------------------------------------------------------------------------


class TestRoleRejectionOrdering:
    """'Assign managers to stores' (walmart-user-stories.md:670) is
    Owner yes / Admin NO / Manager no -- the same shape as 'Create store'
    (:668), with no per-store scope dependency at all. The role check
    must therefore reject a Manager or Admin regardless of whether the
    targeted store_id exists, mirroring US-003's own ordering pin for
    store creation (role rejected with no store in the picture at all)
    rather than US-003's 'Change store settings' ordering (where an
    Admin's rejection on an out-of-scope store is 404, because Admin DOES
    hold the capability in general).
    """

    def test_manager_cannot_grant_against_a_store_they_are_assigned_to(
        self, client, migrated_database
    ):
        with _committed(migrated_database) as c:
            store_id = str(_create_store_db(c, "Role Manager Grant Store"))
            manager = _create_user(c, role="manager", password="role-mgr-grant-pw")
            other = _create_user(c, role="manager", password="role-mgr-other-pw")
            _assign_directly(c, manager["id"], store_id)  # in scope; role must reject anyway
        token = _sign_in(client, manager)

        r = _grant(client, token, store_id, other["id"])
        assert r.status_code == 403
        assert r.status_code != 404

    def test_manager_cannot_grant_against_a_nonexistent_store(
        self, client, migrated_database
    ):
        with _committed(migrated_database) as c:
            manager = _create_user(c, role="manager", password="role-mgr-nx-pw")
            other = _create_user(c, role="manager", password="role-mgr-nx-other-pw")
        token = _sign_in(client, manager)

        r = _grant(client, token, uuid.uuid4(), other["id"])
        assert r.status_code == 403, (
            "a role rejection must not depend on whether the store exists -- "
            f"got {r.status_code}"
        )

    def test_admin_cannot_grant_even_though_admin_may_change_store_settings(
        self, client, migrated_database
    ):
        """The trap: an implementation that reuses
        ``require_can_change_store_settings`` (Owner-or-Admin) for this
        capability would wrongly let an Admin assign managers too --
        the matrix draws these as two separate rows."""
        with _committed(migrated_database) as c:
            store_id = str(_create_store_db(c, "Role Admin Grant Store"))
            admin = _create_user(c, role="admin", password="role-admin-grant-pw")
            manager = _create_user(c, role="manager", password="role-admin-target-pw")
            _assign_directly(c, admin["id"], store_id)
        token = _sign_in(client, admin, with_mfa=True)

        r = _grant(client, token, store_id, manager["id"])
        assert r.status_code == 403
        assert r.status_code != 404

    def test_manager_cannot_revoke_against_a_store_they_are_assigned_to(
        self, client, migrated_database
    ):
        with _committed(migrated_database) as c:
            store_id = str(_create_store_db(c, "Role Manager Revoke Store"))
            manager = _create_user(c, role="manager", password="role-mgr-revoke-pw")
            target = _create_user(c, role="manager", password="role-mgr-revoke-target-pw")
            _assign_directly(c, manager["id"], store_id)
            _assign_directly(c, target["id"], store_id)
        token = _sign_in(client, manager)

        r = _revoke(client, token, store_id, target["id"])
        assert r.status_code == 403
        assert r.status_code != 404

        # And the targeted assignment is genuinely untouched by the
        # rejected attempt.
        assert _count_assignment_rows(
            migrated_database, user_id=target["id"], store_id=store_id
        ) == 1

    def test_admin_cannot_revoke_even_in_scope(self, client, migrated_database):
        with _committed(migrated_database) as c:
            store_id = str(_create_store_db(c, "Role Admin Revoke Store"))
            admin = _create_user(c, role="admin", password="role-admin-revoke-pw")
            target = _create_user(c, role="manager", password="role-admin-revoke-target-pw")
            _assign_directly(c, admin["id"], store_id)
            _assign_directly(c, target["id"], store_id)
        token = _sign_in(client, admin, with_mfa=True)

        r = _revoke(client, token, store_id, target["id"])
        assert r.status_code == 403
        assert r.status_code != 404

    def test_owner_can_grant_and_revoke(self, client, migrated_database):
        """Control: the four rejections above must not be the product of
        an authorization check that rejects everyone."""
        with _committed(migrated_database) as c:
            store_id = str(_create_store_db(c, "Role Owner Control Store"))
            owner = _create_user(c, role="owner", password="role-owner-control-pw")
            manager = _create_user(c, role="manager", password="role-owner-control-mgr-pw")
        token = _sign_in(client, owner, with_mfa=True)

        granted = _grant(client, token, store_id, manager["id"])
        assert granted.status_code == 201, granted.text

        revoked = _revoke(client, token, store_id, manager["id"])
        assert revoked.status_code == 200, revoked.text

    def test_owner_targeting_a_nonexistent_store_gets_404_not_403(
        self, client, migrated_database
    ):
        """The Owner's rejection is unlike Manager/Admin's: the Owner DOES
        hold this capability, so a nonexistent store_id is not an
        authorization question at all -- it is a plain 'no such resource',
        the same 404 every other endpoint gives an Owner for a store that
        does not exist (``repository.get_store`` raises ``OutOfScope`` for
        a missing row regardless of ``scope.all_stores``).

        A bare 404-for-a-made-up-id assertion would pass vacuously against
        a route that simply does not exist at all yet -- every response
        would be 404 regardless of what this test is actually trying to
        pin. The control grant against a REAL store, in the same test, is
        what forces this to mean something: it must succeed (201) so that
        the nonexistent-store 404 right after it is known to come from a
        real "no such store" judgement, not from the whole endpoint being
        unreachable.
        """
        with _committed(migrated_database) as c:
            store_id = str(_create_store_db(c, "Role Owner NX Control Store"))
            owner = _create_user(c, role="owner", password="role-owner-nx-pw")
            manager = _create_user(c, role="manager", password="role-owner-nx-mgr-pw")
        token = _sign_in(client, owner, with_mfa=True)

        control = _grant(client, token, store_id, manager["id"])
        assert control.status_code == 201, control.text

        r = _grant(client, token, uuid.uuid4(), manager["id"])
        assert r.status_code == 404
        assert r.status_code != 403


# ---------------------------------------------------------------------------
# Idempotency / replay -- the F-55 lesson: write the honest retry FIRST.
# ---------------------------------------------------------------------------


class TestIdempotencyAndReplay:
    def test_grant_requires_an_idempotency_key(self, client, migrated_database):
        with _committed(migrated_database) as c:
            store_id = str(_create_store_db(c, "Idem No Key Grant Store"))
            owner = _create_user(c, role="owner", password="idem-nokey-owner-pw")
            manager = _create_user(c, role="manager", password="idem-nokey-mgr-pw")
        token = _sign_in(client, owner, with_mfa=True)

        r = client.post(
            f"/stores/{store_id}/managers",
            json={"user_id": str(manager["id"])},
            headers=_auth(token),
        )
        assert r.status_code == 422

    def test_revoke_requires_an_idempotency_key(self, client, migrated_database):
        with _committed(migrated_database) as c:
            store_id = str(_create_store_db(c, "Idem No Key Revoke Store"))
            owner = _create_user(c, role="owner", password="idem-nokey-revoke-owner-pw")
            manager = _create_user(c, role="manager", password="idem-nokey-revoke-mgr-pw")
        token = _sign_in(client, owner, with_mfa=True)
        assert _grant(client, token, store_id, manager["id"]).status_code == 201

        r = client.delete(
            f"/stores/{store_id}/managers/{manager['id']}", headers=_auth(token)
        )
        assert r.status_code == 422

    def test_replaying_the_same_grant_operation_id_is_the_honest_retry_not_a_conflict(
        self, client, migrated_database
    ):
        """THE F-55 lesson, written first and named: an identical grant
        request, resent under the identical ``Idempotency-Key`` -- the
        exact shape of a client that did not see the first response --
        must return the ORIGINAL outcome (200, replayed), never be
        punished as 'already assigned' (409). A fix that checks the
        composite-key uniqueness before the operation ledger would get
        this backwards, exactly as it did for ``walmart_store_key`` in
        US-003.
        """
        with _committed(migrated_database) as c:
            store_id = str(_create_store_db(c, "Idem Honest Retry Store"))
            owner = _create_user(c, role="owner", password="idem-honest-owner-pw")
            manager = _create_user(c, role="manager", password="idem-honest-mgr-pw")
        token = _sign_in(client, owner, with_mfa=True)

        op_id = _idempotency_key()
        first = _grant(client, token, store_id, manager["id"], operation_id=op_id)
        second = _grant(client, token, store_id, manager["id"], operation_id=op_id)

        assert first.status_code == 201, first.text
        assert second.status_code == 200, second.text
        assert second.json()["meta"]["replayed"] is True
        assert second.json()["data"]["user_id"] == str(manager["id"])

        # Exactly one row, never a duplicate-key error surfaced to the
        # caller, and exactly one audit row for this operation_id.
        assert _count_assignment_rows(
            migrated_database, user_id=manager["id"], store_id=store_id
        ) == 1
        assert len(_fetch_audit_rows(migrated_database, op_id)) == 1

    def test_a_different_payload_reusing_the_same_grant_key_is_a_conflict_not_a_200(
        self, client, migrated_database
    ):
        """The other half of F-55: an ``Idempotency-Key`` spent on one
        (user, store) pair must never be honoured for a genuinely
        different one -- that is a collision, not a replay, and must be
        409, never 200 with either the old or the new target quietly
        substituted."""
        with _committed(migrated_database) as c:
            store_id = str(_create_store_db(c, "Idem Conflict Store"))
            owner = _create_user(c, role="owner", password="idem-conflict-owner-pw")
            manager_a = _create_user(c, role="manager", password="idem-conflict-a-pw")
            manager_b = _create_user(c, role="manager", password="idem-conflict-b-pw")
        token = _sign_in(client, owner, with_mfa=True)

        op_id = _idempotency_key()
        first = _grant(client, token, store_id, manager_a["id"], operation_id=op_id)
        assert first.status_code == 201, first.text

        second = _grant(client, token, store_id, manager_b["id"], operation_id=op_id)
        assert second.status_code == 409, second.text

        # manager_b was never granted by the colliding call.
        assert _count_assignment_rows(
            migrated_database, user_id=manager_b["id"], store_id=store_id
        ) == 0

    def test_replaying_the_same_revoke_operation_id_is_the_honest_retry(
        self, client, migrated_database
    ):
        with _committed(migrated_database) as c:
            store_id = str(_create_store_db(c, "Idem Honest Revoke Store"))
            owner = _create_user(c, role="owner", password="idem-honest-revoke-owner-pw")
            manager = _create_user(c, role="manager", password="idem-honest-revoke-mgr-pw")
        token = _sign_in(client, owner, with_mfa=True)
        assert _grant(client, token, store_id, manager["id"]).status_code == 201

        op_id = _idempotency_key()
        first = _revoke(client, token, store_id, manager["id"], operation_id=op_id)
        second = _revoke(client, token, store_id, manager["id"], operation_id=op_id)

        assert first.status_code == 200, first.text
        assert second.status_code == 200, second.text
        assert second.json()["meta"]["replayed"] is True

        assert _count_assignment_rows(
            migrated_database, user_id=manager["id"], store_id=store_id
        ) == 0
        assert len(_fetch_audit_rows(migrated_database, op_id)) == 1

    def test_double_grant_with_a_fresh_key_against_an_already_assigned_pair_is_a_named_conflict(
        self, client, migrated_database
    ):
        """Not a replay at all -- a genuinely NEW ``Idempotency-Key``
        pointed at a pair that is already assigned. This must be a clean,
        named 409 ('already assigned'), never a raw 500 from the
        composite-primary-key's IntegrityError leaking out, and never a
        silent 200/201 that pretends a second, redundant grant occurred.
        """
        with _committed(migrated_database) as c:
            store_id = str(_create_store_db(c, "Idem Double Grant Store"))
            owner = _create_user(c, role="owner", password="idem-double-owner-pw")
            manager = _create_user(c, role="manager", password="idem-double-mgr-pw")
        token = _sign_in(client, owner, with_mfa=True)

        first = _grant(client, token, store_id, manager["id"])
        assert first.status_code == 201, first.text

        second = _grant(client, token, store_id, manager["id"])  # fresh key, no reuse
        assert second.status_code == 409, second.text
        assert second.json()["detail"]["code"] == "ALREADY_ASSIGNED"

        assert _count_assignment_rows(
            migrated_database, user_id=manager["id"], store_id=store_id
        ) == 1


# ---------------------------------------------------------------------------
# Not-found semantics
# ---------------------------------------------------------------------------


class TestNotFoundSemantics:
    def test_owner_revoking_against_a_nonexistent_store_is_404(
        self, client, migrated_database
    ):
        """A control grant/revoke pair against a REAL store precedes the
        nonexistent-store attempt, same reasoning as
        ``test_owner_targeting_a_nonexistent_store_gets_404_not_403``
        above: without it, a bare 404 assertion here would hold just as
        well against a route that does not exist at all yet."""
        with _committed(migrated_database) as c:
            store_id = str(_create_store_db(c, "NF Owner Revoke Control Store"))
            owner = _create_user(c, role="owner", password="nf-owner-revoke-pw")
            manager = _create_user(c, role="manager", password="nf-owner-revoke-mgr-pw")
        token = _sign_in(client, owner, with_mfa=True)

        control_grant = _grant(client, token, store_id, manager["id"])
        assert control_grant.status_code == 201, control_grant.text
        control_revoke = _revoke(client, token, store_id, manager["id"])
        assert control_revoke.status_code == 200, control_revoke.text

        r = _revoke(client, token, uuid.uuid4(), manager["id"])
        assert r.status_code == 404

    def test_granting_a_user_id_that_does_not_exist_is_a_named_404_not_a_500(
        self, client, migrated_database
    ):
        """A dangling ``user_id`` -- not a store scope question at all, a
        plain bad reference -- must not leak as a raw foreign-key
        IntegrityError. Translated the same way ``DuplicateStoreKey`` was
        translated in US-003: a named, disclosed status, never a 500.
        """
        with _committed(migrated_database) as c:
            store_id = str(_create_store_db(c, "NF Unknown User Store"))
            owner = _create_user(c, role="owner", password="nf-unknown-user-owner-pw")
        token = _sign_in(client, owner, with_mfa=True)

        bogus_user_id = uuid.uuid4()
        r = _grant(client, token, store_id, bogus_user_id)
        assert r.status_code == 404, r.text
        detail = r.json()["detail"]
        assert isinstance(detail, dict) and detail.get("code") == "USER_NOT_FOUND", (
            f"expected a named {{'code': 'USER_NOT_FOUND'}} detail, got {detail!r}"
        )

        assert _count_assignment_rows(
            migrated_database, user_id=bogus_user_id, store_id=store_id
        ) == 0

    # NOTE: a 'Manager hits an unassigned store vs. a nonexistent one via
    # GET /stores/{id}, both 404, identical body' test was deliberately NOT
    # added here. US-001's own test_ac_03_returning_to_a_saved_destination_
    # rechecks_authorization already pins that exact rule against that
    # exact (pre-existing) endpoint; repeating it here would exercise no
    # code this story adds and would pass before and after implementation
    # alike -- a duplicate assertion dressed up as new coverage.


# ---------------------------------------------------------------------------
# The genuine, unnamed gap: who may assign an ADMIN to a store.
# ---------------------------------------------------------------------------


class TestGapAdminAssignmentAuthorityIsUndefined:
    """spec-oracle confirmed: ``user_store_assignments`` holds Admin rows
    too (database-schema.md, 002_access.sql), but no capability-matrix row
    and no AC anywhere names who may create or remove an Admin's store
    assignment -- every AC in this story names Managers specifically.
    Inventing an answer (Owner-only, by analogy to 'Administer users'?
    Something else?) would be exactly the guessed rule AGENTS.md forbids.
    Left skipped and visible rather than silently decided here.
    """

    @pytest.mark.skip(
        reason=(
            "No document (walmart-user-stories.md's capability matrix, "
            "api-service-design.md, or database-schema.md) names who may "
            "assign or remove an Admin's store assignment -- only Manager "
            "assignment is specified by any AC in US-004. Report to the "
            "Owner/spec-oracle rather than inventing an authorization rule."
        )
    )
    def test_who_may_assign_an_admin_to_a_store_is_undefined(self):
        raise NotImplementedError


# ---------------------------------------------------------------------------
# Cross-cutting: unauthenticated callers are prompted to authenticate, not
# told "not found" (F-01's discipline, re-pinned at this story's own
# surface rather than assumed to be inherited for free).
# ---------------------------------------------------------------------------


class TestUnauthenticatedCallerIsPromptedNotFound:
    def test_grant_and_revoke_require_authentication(self, client, migrated_database):
        with _committed(migrated_database) as c:
            store_id = str(_create_store_db(c, "Unauth Store"))
            manager = _create_user(c, role="manager", password="unauth-mgr-pw")

        unauth_grant = client.post(
            f"/stores/{store_id}/managers",
            json={"user_id": str(manager["id"])},
            headers={"Idempotency-Key": _idempotency_key()},
        )
        assert unauth_grant.status_code == 401
        assert unauth_grant.json()["detail"]["code"] == "AUTHENTICATION_REQUIRED"

        unauth_revoke = client.delete(
            f"/stores/{store_id}/managers/{manager['id']}",
            headers={"Idempotency-Key": _idempotency_key()},
        )
        assert unauth_revoke.status_code == 401
        assert unauth_revoke.json()["detail"]["code"] == "AUTHENTICATION_REQUIRED"
