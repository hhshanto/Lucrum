"""US-003 -- Create and configure a store. Module: Stores.

docs/walmart-user-stories.md:756-766 (verbatim):

    As the Owner, I want to register a store and its reporting context so
    that its orders and financial records remain isolated from other stores.

    AC-01: Given valid approved store configuration, when creating a store,
    then it receives a stable internal identifier distinct from its display
    name.
    AC-02: Given missing reporting currency, reporting timezone, or required
    integration configuration, when activating financial reporting, then the
    missing configuration is identified rather than guessed.
    AC-03: Given a Manager, when attempting store creation or settings
    changes, then the server rejects the operation.
    AC-04: Given a store rename, when viewing historical records, then their
    store identity and links remain intact.

No implementation exists yet: there is no ``POST /stores``, no
``PATCH /stores/{storeId}``, and no store-creation function in
``src/stores/repository.py`` (it currently has only ``list_stores``,
``get_store`` and ``activation_blockers``). Every HTTP-level test below is
expected to fail against a route that does not exist yet -- see the session
report for which failures are "route absent" versus "behaviour absent".

-------------------------------------------------------------------------
Decisions made to write these tests, recorded so they can be checked rather
than silently assumed:

1. **Request/response field names.** Neither ``walmart-user-stories.md`` nor
   the API document (``api-service-design.md:218-236``) specifies a request
   body shape for ``POST /stores`` or ``PATCH /stores/{storeId}`` -- only
   that the endpoints exist. Field names here are taken directly from the
   column names in ``database-schema.md:189-198`` / ``001_baseline.sql``,
   with one adjustment: the *currency* field is called ``currency_code`` to
   match the key ``main.py``'s existing ``GET /stores`` and
   ``GET /stores/{id}`` already use in their response bodies
   (``"currency_code": r["reporting_currency_code"]``). This is an
   assumption, flagged as such in the session report, not a confirmed
   contract.

2. **``PATCH /stores/{storeId}/settings`` is deliberately NOT exercised
   here.** ``database-schema.md:200-213`` describes a *separate*,
   effective-dated ``store_settings`` table ("default_warehouse_cost...")
   that US-030 ("Change store policies prospectively", which *depends on*
   US-003) already has its own AC-01 for: "Given a Manager attempts a
   settings write ... it is rejected." Pinning that endpoint's authorization
   here would duplicate US-030's own test file once it exists, and more
   importantly would force this file to invent a monetary request payload
   this story has no business specifying (AGENTS.md: exact decimal money is
   real scope elsewhere, not here). AC-03's "settings changes" is tested
   instead against ``PATCH /stores/{storeId}`` (identity fields: name,
   timezone, currency, managed_from) -- squarely US-003's endpoint per
   ``database-schema.md:226-229`` ("Update store identity fields"), and
   still a "store settings change" in BR-11's and AC-03's own words.

3. **403 vs 404.** The capability matrix (``walmart-user-stories.md:659-695``)
   is a *role* check; store scope (``access-v1``, pinned throughout
   ``test_us002_select_store.py``) is a *separate* question. An Admin or
   Manager who lacks the *role* for an operation on a store they ARE
   assigned to must get 403 (the resource is visible to them; the action is
   not permitted). An actor acting on a store they are NOT assigned to must
   get 404 regardless of role, indistinguishable from a store that does not
   exist at all (US-002 AC-02, BT-08). Mixing these up in either direction
   is a real defect this file is written to catch, not a detail to gloss
   over -- see ``TestAC03AuthorizationMatrix``.

Conventions carried over from ``test_us002_select_store.py`` /
``test_us001_sign_in.py``: database tests use the rolled-back ``conn``
fixture; HTTP tests seed rows on a second, committed connection because the
API uses its own pool; ``src.*`` imports happen inside each test so a
genuinely missing module/attribute fails one test, not collection;
MFA is required for Owner and Admin, optional for Manager; decoys are
seeded in every scoping-shaped test so the assertion is still meaningful
when this file runs alone against a fresh database (finding F-23).
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
    return f"us003-{label}-{uuid.uuid4().hex}@example.test"


def _totp_code(secret: str = TOTP_TEST_SECRET) -> str:
    return pyotp.TOTP(secret).now()


def _create_user(c, *, role, password, mfa_enabled=None, mfa_secret=None):
    """Seed a user, committing-connection style. Mirrors test_us002."""
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


def _create_store_db(c, name, *, is_active=True, timezone="America/New_York",
                      reporting_currency_code="USD", managed_from_year=2026,
                      managed_from_month=7):
    """Seed a store directly, for decoys and assignment fixtures -- not the
    behaviour under test. Mirrors test_us002's ``_create_store``."""
    row = c.execute(
        """
        insert into stores (name, walmart_store_key, timezone,
                            reporting_currency_code, managed_from_year,
                            managed_from_month, is_active)
        values (%s, %s, %s, %s, %s, %s, %s)
        returning id
        """,
        (
            name,
            f"US003-{uuid.uuid4().hex[:12]}",
            timezone,
            reporting_currency_code,
            managed_from_year,
            managed_from_month,
            is_active,
        ),
    ).fetchone()
    return row["id"]


def _assign(c, user_id, store_id):
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


def _store_payload(name: str | None = None, **overrides) -> dict:
    """A fully valid store-creation body. See docstring point 1 above for
    why these particular field names were chosen."""
    payload = {
        "name": name or f"US003 Store {uuid.uuid4().hex[:8]}",
        "walmart_store_key": f"US003-API-{uuid.uuid4().hex[:12]}",
        "timezone": "America/New_York",
        "currency_code": "USD",
        "managed_from_year": 2026,
        "managed_from_month": 7,
    }
    payload.update(overrides)
    return payload


def _without(payload: dict, *keys: str) -> dict:
    """Omit keys entirely, rather than sending them as JSON null -- the
    scenario AC-02 actually describes is a field the caller never supplied."""
    return {k: v for k, v in payload.items() if k not in keys}


def _idempotency_key() -> str:
    return str(uuid.uuid4())


def _fetch_store_row(url: str, store_id) -> dict | None:
    with psycopg.connect(url, row_factory=dict_row) as c:
        return c.execute(
            "select * from stores where id = %s", (str(store_id),)
        ).fetchone()


def _fetch_audit_rows(url: str, operation_id: str) -> list[dict]:
    with psycopg.connect(url, row_factory=dict_row) as c:
        return c.execute(
            "select * from audit_events where operation_id = %s", (operation_id,)
        ).fetchall()


def _count_stores_matching(url: str, *, walmart_store_key: str) -> int:
    with psycopg.connect(url) as c:
        return c.execute(
            "select count(*) from stores where walmart_store_key = %s",
            (walmart_store_key,),
        ).fetchone()[0]


# F-28: session-breaking helpers, mirroring test_us001_sign_in.py's own
# direct-SQL manipulation of the same tables.


def _expire_session(url: str, user_id) -> None:
    with psycopg.connect(url, row_factory=dict_row) as c:
        c.execute(
            """
            update user_sessions
               set expires_at = now() - interval '1 minute'
             where user_id = %s and revoked_at is null
            """,
            (user_id,),
        )
        c.commit()


def _revoke_session(url: str, user_id) -> None:
    with psycopg.connect(url, row_factory=dict_row) as c:
        c.execute(
            """
            update user_sessions
               set revoked_at = now()
             where user_id = %s and revoked_at is null
            """,
            (user_id,),
        )
        c.commit()


def _deactivate_user(url: str, user_id) -> None:
    with psycopg.connect(url, row_factory=dict_row) as c:
        c.execute("update users set is_active = false where id = %s", (user_id,))
        c.commit()


@pytest.fixture
def client(migrated_database):
    with TestClient(main.app) as c:
        yield c


def _post_store(client, token, payload, *, operation_id=None):
    headers = {**_auth(token), "Idempotency-Key": operation_id or _idempotency_key()}
    return client.post("/stores", json=payload, headers=headers)


def _patch_store(client, token, store_id, body, *, operation_id=None):
    headers = {**_auth(token), "Idempotency-Key": operation_id or _idempotency_key()}
    return client.patch(f"/stores/{store_id}", json=body, headers=headers)


# ---------------------------------------------------------------------------
# AC-01 -- stable internal identifier distinct from the display name
# ---------------------------------------------------------------------------


class TestAC01StableIdentifier:
    def test_ac01_create_store_returns_a_uuid_identifier_distinct_from_the_display_name(
        self, client, migrated_database
    ):
        """The identifier must (a) exist, (b) parse as a UUID, (c) not equal
        the display name, and (d) actually be the primary key of the row
        persisted in the database -- not merely an echoed request field.
        """
        with _committed(migrated_database) as c:
            owner = _create_user(c, role="owner", password="ac01-owner-pw")
        token = _sign_in(client, owner, with_mfa=True)

        distinctive_name = f"AC01 Named Store {uuid.uuid4().hex[:8]}"
        payload = _store_payload(name=distinctive_name)

        r = _post_store(client, token, payload)
        assert r.status_code == 201, r.text
        data = r.json()["data"]

        assert "id" in data
        parsed = uuid.UUID(data["id"])  # raises if not a UUID at all
        assert str(parsed) != distinctive_name
        assert data.get("name") == distinctive_name

        row = _fetch_store_row(migrated_database, data["id"])
        assert row is not None, "the returned id must resolve to a real persisted row"
        assert row["name"] == distinctive_name
        assert str(row["id"]) == data["id"]

    def test_ac01_two_stores_with_identical_display_names_receive_distinct_ids(
        self, client, migrated_database
    ):
        """The id must not be a function of the name: two stores sharing a
        display name (distinct walmart_store_key, as the external identifier
        is what is actually required to be unique) must receive two
        different internal identifiers.
        """
        with _committed(migrated_database) as c:
            owner = _create_user(c, role="owner", password="ac01-dup-owner-pw")
        token = _sign_in(client, owner, with_mfa=True)

        shared_name = f"AC01 Duplicate Name {uuid.uuid4().hex[:8]}"
        first = _post_store(client, token, _store_payload(name=shared_name))
        second = _post_store(client, token, _store_payload(name=shared_name))

        assert first.status_code == 201, first.text
        assert second.status_code == 201, second.text
        id_a = first.json()["data"]["id"]
        id_b = second.json()["data"]["id"]
        assert id_a != id_b

    def test_ac01_identifier_is_stable_across_rename(self, client, migrated_database):
        """Renaming must not mint a new identifier. This deliberately
        overlaps AC-04 (see TestAC04RenamePreservesIdentityAndLinks) --
        AC-01's own "stable" and AC-04's "identity remains intact" are two
        different sentences in the spec and both need their own failure.
        """
        with _committed(migrated_database) as c:
            owner = _create_user(c, role="owner", password="ac01-stable-owner-pw")
        token = _sign_in(client, owner, with_mfa=True)

        created = _post_store(client, token, _store_payload(name="AC01 Before Rename"))
        assert created.status_code == 201, created.text
        original_id = created.json()["data"]["id"]

        renamed = _patch_store(client, token, original_id, {"name": "AC01 After Rename"})
        assert renamed.status_code == 200, renamed.text
        assert renamed.json()["data"]["id"] == original_id

        detail = client.get(f"/stores/{original_id}", headers=_auth(token))
        assert detail.status_code == 200
        assert detail.json()["data"]["id"] == original_id
        assert detail.json()["data"]["name"] == "AC01 After Rename"

    def test_ac01_create_store_requires_an_idempotency_key(self, client, migrated_database):
        """Write-path convention (AGENTS.md, pinned for the worked example in
        test_api.py's TestWritePathPattern): a missing Idempotency-Key is a
        422, not a silently-generated one."""
        with _committed(migrated_database) as c:
            owner = _create_user(c, role="owner", password="ac01-noidem-owner-pw")
        token = _sign_in(client, owner, with_mfa=True)

        r = client.post("/stores", json=_store_payload(), headers=_auth(token))
        assert r.status_code == 422

    def test_ac01_replaying_the_same_operation_id_creates_exactly_one_store(
        self, client, migrated_database
    ):
        """'Any write: the same operation_id replayed produces one effect.'
        The second call uses a DIFFERENT body under the SAME operation_id --
        a shallow idempotency check that only looks at the operation_id
        column but still re-runs the insert would create a second store (or
        silently apply the second payload); neither may happen. The caller
        gets back the original store, unmodified.
        """
        with _committed(migrated_database) as c:
            owner = _create_user(c, role="owner", password="ac01-replay-owner-pw")
        token = _sign_in(client, owner, with_mfa=True)

        op_id = _idempotency_key()
        first_payload = _store_payload(name="AC01 Replay Original")
        second_payload = _store_payload(name="AC01 Replay Should Not Apply")

        first = _post_store(client, token, first_payload, operation_id=op_id)
        second = _post_store(client, token, second_payload, operation_id=op_id)

        assert first.status_code == 201, first.text
        first_id = first.json()["data"]["id"]

        # The replay must not silently fail either -- it must report back
        # the original outcome, not a generic error.
        assert second.status_code in (200, 201), second.text
        assert second.json()["data"]["id"] == first_id

        # The second payload's walmart_store_key must never have been
        # inserted as a row of its own.
        second_key = second_payload["walmart_store_key"]
        assert _count_stores_matching(migrated_database, walmart_store_key=second_key) == 0

        row = _fetch_store_row(migrated_database, first_id)
        assert row["name"] == "AC01 Replay Original"


# ---------------------------------------------------------------------------
# AC-02 -- missing reporting currency / timezone is identified, not guessed
#
# Scope: only the two named-and-testable blockers, timezone and reporting
# currency. "required integration configuration" is an open gap (B-11, no
# document defines its contents) and is marked skipped below rather than
# invented. walmart_store_key is NOT tested as a blocker -- it is a required,
# NOT NULL column at creation time (001_baseline.sql:29), not one of the two
# AC-02 names, and inventing a third blocker is explicitly out of scope here.
# ---------------------------------------------------------------------------


class TestAC02MissingConfigurationIsIdentified:
    def test_ac02_store_created_without_timezone_or_currency_is_permitted_and_not_defaulted(
        self, client, migrated_database
    ):
        """Creation itself must succeed with both omitted (database-schema.md
        :191,198: both nullable at creation) -- and, critically, the absent
        values must land in the database as NULL, never as a guessed
        default such as 'UTC' or 'USD'. 'Unknown is not zero' applied to
        configuration: a silently-defaulted timezone produces months that
        are quietly wrong at the boundary (period-v1).
        """
        with _committed(migrated_database) as c:
            owner = _create_user(c, role="owner", password="ac02-missing-owner-pw")
        token = _sign_in(client, owner, with_mfa=True)

        payload = _without(_store_payload(), "timezone", "currency_code")
        r = _post_store(client, token, payload)
        assert r.status_code == 201, r.text

        row = _fetch_store_row(migrated_database, r.json()["data"]["id"])
        assert row["timezone"] is None
        assert row["reporting_currency_code"] is None

    def test_ac02_missing_timezone_alone_is_named_in_activation_blockers(
        self, client, migrated_database
    ):
        with _committed(migrated_database) as c:
            owner = _create_user(c, role="owner", password="ac02-tz-owner-pw")
        token = _sign_in(client, owner, with_mfa=True)

        payload = _without(_store_payload(), "timezone")
        r = _post_store(client, token, payload)
        assert r.status_code == 201, r.text
        store_id = r.json()["data"]["id"]

        detail = client.get(f"/stores/{store_id}", headers=_auth(token))
        assert detail.status_code == 200
        blockers = set(detail.json()["data"]["activation_blockers"])
        assert blockers == {"timezone"}, (
            "only the field actually missing (timezone) must be named -- "
            f"got {blockers!r}"
        )

    def test_ac02_missing_currency_alone_is_named_in_activation_blockers(
        self, client, migrated_database
    ):
        with _committed(migrated_database) as c:
            owner = _create_user(c, role="owner", password="ac02-cur-owner-pw")
        token = _sign_in(client, owner, with_mfa=True)

        payload = _without(_store_payload(), "currency_code")
        r = _post_store(client, token, payload)
        assert r.status_code == 201, r.text
        store_id = r.json()["data"]["id"]

        detail = client.get(f"/stores/{store_id}", headers=_auth(token))
        assert detail.status_code == 200
        blockers = set(detail.json()["data"]["activation_blockers"])
        assert blockers == {"reporting_currency_code"}

    def test_ac02_both_missing_fields_are_both_named_not_just_the_first(
        self, client, migrated_database
    ):
        """The trap a shallow implementation falls into: checking timezone,
        returning on the first miss, and never getting to currency. Both
        must be named in the same response.
        """
        with _committed(migrated_database) as c:
            owner = _create_user(c, role="owner", password="ac02-both-owner-pw")
        token = _sign_in(client, owner, with_mfa=True)

        payload = _without(_store_payload(), "timezone", "currency_code")
        r = _post_store(client, token, payload)
        assert r.status_code == 201, r.text
        store_id = r.json()["data"]["id"]

        detail = client.get(f"/stores/{store_id}", headers=_auth(token))
        assert detail.status_code == 200
        blockers = set(detail.json()["data"]["activation_blockers"])
        assert blockers == {"timezone", "reporting_currency_code"}

        # The list endpoint must agree -- not just the detail endpoint.
        listing = client.get("/stores", headers=_auth(token))
        assert listing.status_code == 200
        [row] = [r for r in listing.json()["data"] if r["id"] == store_id]
        assert set(row["activation_blockers"]) == {"timezone", "reporting_currency_code"}

    def test_ac02_fully_configured_store_has_no_activation_blockers(
        self, client, migrated_database
    ):
        """The control case: an explicit, fully-supplied configuration must
        not be flagged as incomplete. Mirrors the costing rule ('an explicit
        zero does not yield incomplete') applied to configuration instead of
        money: presence, not mere non-emptiness heuristics, is what clears a
        blocker.
        """
        with _committed(migrated_database) as c:
            owner = _create_user(c, role="owner", password="ac02-full-owner-pw")
        token = _sign_in(client, owner, with_mfa=True)

        r = _post_store(client, token, _store_payload())
        assert r.status_code == 201, r.text
        store_id = r.json()["data"]["id"]

        detail = client.get(f"/stores/{store_id}", headers=_auth(token))
        assert detail.json()["data"]["activation_blockers"] == []

    @pytest.mark.skip(
        reason=(
            "B-11: AC-02's third clause, 'required integration configuration', "
            "is not defined by any authoritative document -- no document names "
            "what it consists of. Inventing a blocker key for it would be "
            "exactly the kind of guessed rule AGENTS.md forbids. Placeholder "
            "kept visible in the test run rather than silently dropped; "
            "report to the Owner/spec-oracle, do not implement around it."
        )
    )
    def test_ac02_missing_integration_configuration_is_identified(self):
        raise NotImplementedError


# ---------------------------------------------------------------------------
# AC-03 -- the capability-matrix trap
#
# walmart-user-stories.md:668,671 (capability matrix):
#   | Create store           | Owner yes | Admin NO  | Manager no |
#   | Change store settings  | Owner yes | Admin YES | Manager no |
#
# AC-03's own sentence only names the Manager ("Given a Manager, ... the
# server rejects"), but the matrix draws TWO different lines. A test that
# only checks "Manager is rejected from both" passes an implementation that
# either lets Admin create stores, or locks Admin out of settings entirely.
# Both are real violations of BR-11 / access-v1 and are pinned separately
# below.
#
# See the module docstring (point 2) for why PATCH /stores/{storeId}/settings
# is not the endpoint used for the "settings changes" half -- PATCH
# /stores/{storeId} (identity fields) is, and still satisfies BR-11's text.
# ---------------------------------------------------------------------------


class TestAC03AuthorizationMatrix:
    # -- Create store: Owner yes, Admin NO, Manager no -----------------

    def test_ac03_manager_cannot_create_a_store(self, client, migrated_database):
        with _committed(migrated_database) as c:
            manager = _create_user(c, role="manager", password="ac03-mgr-create-pw")
        token = _sign_in(client, manager, with_mfa=False)

        r = _post_store(client, token, _store_payload())
        assert r.status_code == 403
        assert r.status_code != 404  # this is a role rejection, not a scope one

    def test_ac03_admin_cannot_create_a_store(self, client, migrated_database):
        """The trap. The matrix's 'Create store' row is Owner yes, Admin
        NO -- an implementation that treats 'privileged role' as one bucket
        (as the sign-in MFA requirement legitimately does) would wrongly
        let an Admin create a store too.
        """
        with _committed(migrated_database) as c:
            admin = _create_user(c, role="admin", password="ac03-admin-create-pw")
        token = _sign_in(client, admin, with_mfa=True)

        r = _post_store(client, token, _store_payload())
        assert r.status_code == 403
        assert r.status_code != 404

    def test_ac03_owner_can_create_a_store(self, client, migrated_database):
        """Control: the two rejections above must not be the product of an
        authorization check that rejects everyone."""
        with _committed(migrated_database) as c:
            owner = _create_user(c, role="owner", password="ac03-owner-create-pw")
        token = _sign_in(client, owner, with_mfa=True)

        r = _post_store(client, token, _store_payload())
        assert r.status_code == 201, r.text

    # -- Change store settings: Owner yes, Admin YES, Manager no --------

    def test_ac03_manager_cannot_change_store_settings(self, client, migrated_database):
        with _committed(migrated_database) as c:
            store_id = _create_store_db(c, "AC03 Manager Settings Store")
            manager = _create_user(c, role="manager", password="ac03-mgr-settings-pw")
            _assign(c, manager["id"], store_id)  # in scope; rejection must be role-based
        token = _sign_in(client, manager, with_mfa=False)

        r = _patch_store(client, token, store_id, {"name": "Manager Should Not Win"})
        assert r.status_code == 403
        assert r.status_code != 404  # the Manager IS assigned -- this is not a scope miss

    def test_ac03_admin_can_change_store_settings_in_scope(self, client, migrated_database):
        """The other half of the trap: an implementation that reuses the
        'Create store' check wholesale for 'Change store settings' would
        wrongly reject this Admin too.
        """
        with _committed(migrated_database) as c:
            store_id = _create_store_db(c, "AC03 Admin Settings Store")
            admin = _create_user(c, role="admin", password="ac03-admin-settings-pw")
            _assign(c, admin["id"], store_id)
        token = _sign_in(client, admin, with_mfa=True)

        r = _patch_store(client, token, store_id, {"name": "AC03 Admin Renamed It"})
        assert r.status_code == 200, r.text
        assert r.json()["data"]["name"] == "AC03 Admin Renamed It"

    def test_ac03_owner_can_change_store_settings(self, client, migrated_database):
        with _committed(migrated_database) as c:
            store_id = _create_store_db(c, "AC03 Owner Settings Store")
            owner = _create_user(c, role="owner", password="ac03-owner-settings-pw")
        token = _sign_in(client, owner, with_mfa=True)

        r = _patch_store(client, token, store_id, {"name": "AC03 Owner Renamed It"})
        assert r.status_code == 200, r.text

    # -- 403 (role) vs 404 (scope) must not be conflated in either direction --

    def test_ac03_admin_cannot_change_settings_for_an_out_of_scope_store(
        self, client, migrated_database
    ):
        """Role alone is not enough: BR-11 grants Admin the *capability*,
        access-v1 still requires the Admin to hold the *assignment*. An
        Admin assigned to nothing who targets someone else's store must get
        404 (indistinguishable from nonexistent), not 403 (which would
        confirm the store exists) and not 200.
        """
        with _committed(migrated_database) as c:
            other_store = _create_store_db(c, "AC03 Not This Admin's Store")
            admin = _create_user(c, role="admin", password="ac03-admin-oos-pw")
            # Deliberately no _assign() call for this admin.
            # F-23 decoy: someone else holds the only assignment to this
            # store, so an unscoped "return every row" bug cannot pass by
            # accident even on a fresh database.
            decoy = _create_user(c, role="manager", password="ac03-oos-decoy-pw")
            _assign(c, decoy["id"], other_store)
        token = _sign_in(client, admin, with_mfa=True)

        out_of_scope = _patch_store(client, token, other_store, {"name": "x"})
        nonexistent = _patch_store(client, token, uuid.uuid4(), {"name": "x"})

        assert out_of_scope.status_code == 404
        assert nonexistent.status_code == 404
        assert out_of_scope.json() == nonexistent.json()

        # F-41: the F-24 regression, reintroduced on the PATCH path -- status
        # and body matching is not enough; a header present only for the
        # store that actually exists (an ETag, say) would disclose exactly
        # the distinction this test exists to hide. `date` varies between
        # any two sequential responses regardless and is excluded for that
        # reason alone, exactly as test_us002's own F-24 fix does.
        excluded_headers = {"date"}
        out_of_scope_headers = {
            k.lower(): v
            for k, v in out_of_scope.headers.items()
            if k.lower() not in excluded_headers
        }
        nonexistent_headers = {
            k.lower(): v
            for k, v in nonexistent.headers.items()
            if k.lower() not in excluded_headers
        }
        assert out_of_scope_headers == nonexistent_headers

    def test_ac03_rejection_is_403_not_404_for_a_manager_assigned_to_the_store(
        self, client, migrated_database
    ):
        """The reverse mistake: an implementation that folds the capability
        check into the scope check (so 'not permitted' and 'not found' share
        one code path) would 404 a Manager who is genuinely in scope but
        simply lacks the role, which is BT-08's anti-pattern in the other
        direction -- ordinarily 404 is what protects against disclosure, but
        here the Manager already knows the store exists (they are assigned
        to it); collapsing the two would just be wrong, not protective.
        """
        with _committed(migrated_database) as c:
            store_id = _create_store_db(c, "AC03 403 Not 404 Store")
            manager = _create_user(c, role="manager", password="ac03-403-mgr-pw")
            _assign(c, manager["id"], store_id)
        token = _sign_in(client, manager, with_mfa=False)

        r = _patch_store(client, token, store_id, {"name": "x"})
        assert r.status_code == 403


# ---------------------------------------------------------------------------
# AC-04 -- rename preserves identity and links to historical records
# ---------------------------------------------------------------------------


class TestAC04RenamePreservesIdentityAndLinks:
    def test_ac04_rename_does_not_alter_the_stable_identifier(self, client, migrated_database):
        """Deliberately duplicates test_ac01_identifier_is_stable_across_rename's
        assertion -- AC-01 and AC-04 are two separate sentences in the spec,
        and a regression in either must fail its own named test."""
        with _committed(migrated_database) as c:
            owner = _create_user(c, role="owner", password="ac04-stable-owner-pw")
        token = _sign_in(client, owner, with_mfa=True)

        created = _post_store(client, token, _store_payload(name="AC04 Before"))
        store_id = created.json()["data"]["id"]

        renamed = _patch_store(client, token, store_id, {"name": "AC04 After"})
        assert renamed.status_code == 200, renamed.text
        assert renamed.json()["data"]["id"] == store_id

    def test_ac04_audit_event_written_before_rename_still_resolves_to_the_same_store(
        self, client, migrated_database
    ):
        """Write a note (an existing, trivial write against the store) BEFORE
        the rename, then rename, then confirm the historical audit row's
        store_id foreign key still points at the same, unchanged id, and
        that id still resolves to a (now differently-named) real store.
        """
        with _committed(migrated_database) as c:
            owner = _create_user(c, role="owner", password="ac04-link-owner-pw")
        token = _sign_in(client, owner, with_mfa=True)

        created = _post_store(client, token, _store_payload(name="AC04 Link Before"))
        store_id = created.json()["data"]["id"]

        note_op = _idempotency_key()
        note = client.post(
            f"/stores/{store_id}/notes",
            json={"note": "pre-rename historical note"},
            headers={**_auth(token), "Idempotency-Key": note_op},
        )
        assert note.status_code == 201, note.text

        renamed = _patch_store(client, token, store_id, {"name": "AC04 Link After"})
        assert renamed.status_code == 200, renamed.text

        audit_rows = _fetch_audit_rows(migrated_database, note_op)
        assert len(audit_rows) == 1
        assert str(audit_rows[0]["store_id"]) == store_id
        assert str(audit_rows[0]["entity_id"]) == store_id

        detail = client.get(f"/stores/{store_id}", headers=_auth(token))
        assert detail.json()["data"]["id"] == store_id
        assert detail.json()["data"]["name"] == "AC04 Link After"

    def test_ac04_assignment_recorded_before_rename_still_grants_scope_after_rename(
        self, client, migrated_database
    ):
        """A Manager's assignment is keyed on store_id, not store name. It
        must survive a rename performed by someone else (the Owner) with no
        action required from the Manager.
        """
        with _committed(migrated_database) as c:
            store_id = str(_create_store_db(c, "AC04 Assignment Before Rename"))
            owner = _create_user(c, role="owner", password="ac04-assign-owner-pw")
            manager = _create_user(c, role="manager", password="ac04-assign-mgr-pw")
            _assign(c, manager["id"], store_id)

            # F-38: the module docstring claims a decoy is seeded in every
            # scoping-shaped test; this one did not carry one, which is
            # exactly how F-23 happened elsewhere -- a false claim about
            # hermeticity. A different user holds the only assignment to a
            # second store, so a scope resolver that dropped
            # `where user_id = %s` cannot pass by accident even on a fresh
            # database.
            decoy_store = str(_create_store_db(c, "AC04 Assignment Decoy Store"))
            decoy = _create_user(c, role="manager", password="ac04-assign-decoy-pw")
            _assign(c, decoy["id"], decoy_store)

        owner_token = _sign_in(client, owner, with_mfa=True)
        manager_token = _sign_in(client, manager, with_mfa=False)

        before = client.get("/stores", headers=_auth(manager_token))
        before_ids = {row["id"] for row in before.json()["data"]}
        assert store_id in before_ids
        assert decoy_store not in before_ids

        renamed = _patch_store(
            client, owner_token, store_id, {"name": "AC04 Assignment After Rename"}
        )
        assert renamed.status_code == 200, renamed.text

        after = client.get("/stores", headers=_auth(manager_token))
        after_rows = {row["id"]: row["name"] for row in after.json()["data"]}
        assert store_id in after_rows
        assert decoy_store not in after_rows
        assert after_rows[store_id] == "AC04 Assignment After Rename"

        detail = client.get(f"/stores/{store_id}", headers=_auth(manager_token))
        assert detail.status_code == 200
        assert detail.json()["data"]["id"] == store_id

    def test_ac04_rename_does_not_alter_other_store_fields(self, client, migrated_database):
        """Only the name may change. timezone, reporting currency,
        managed_from, is_active, and created_at must all be byte-identical
        before and after -- a rename implemented as 'delete and recreate the
        row' or one that accidentally re-applies column defaults would
        disturb one of these silently.
        """
        with _committed(migrated_database) as c:
            owner = _create_user(c, role="owner", password="ac04-fields-owner-pw")
        token = _sign_in(client, owner, with_mfa=True)

        created = _post_store(
            client,
            token,
            _store_payload(
                name="AC04 Fields Before",
                timezone="America/Chicago",
                currency_code="CAD",
                managed_from_year=2025,
                managed_from_month=11,
            ),
        )
        assert created.status_code == 201, created.text
        store_id = created.json()["data"]["id"]

        before_row = _fetch_store_row(migrated_database, store_id)
        assert before_row["timezone"] == "America/Chicago"
        assert before_row["reporting_currency_code"] == "CAD"
        assert before_row["managed_from_year"] == 2025
        assert before_row["managed_from_month"] == 11
        assert before_row["is_active"] is True

        renamed = _patch_store(client, token, store_id, {"name": "AC04 Fields After"})
        assert renamed.status_code == 200, renamed.text

        after_row = _fetch_store_row(migrated_database, store_id)
        assert after_row["name"] == "AC04 Fields After"
        assert after_row["timezone"] == before_row["timezone"]
        assert after_row["reporting_currency_code"] == before_row["reporting_currency_code"]
        assert after_row["managed_from_year"] == before_row["managed_from_year"]
        assert after_row["managed_from_month"] == before_row["managed_from_month"]
        assert after_row["is_active"] == before_row["is_active"]
        assert after_row["created_at"] == before_row["created_at"]

    def test_ac04_rename_is_an_audited_write_with_an_operation_id(self, client, migrated_database):
        with _committed(migrated_database) as c:
            store_id = str(_create_store_db(c, "AC04 Audited Rename Store"))
            owner = _create_user(c, role="owner", password="ac04-audit-owner-pw")
        token = _sign_in(client, owner, with_mfa=True)

        op_id = _idempotency_key()
        r = _patch_store(client, token, store_id, {"name": "AC04 Audited New Name"}, operation_id=op_id)
        assert r.status_code == 200, r.text

        rows = _fetch_audit_rows(migrated_database, op_id)
        assert len(rows) == 1
        row = rows[0]
        assert str(row["store_id"]) == store_id
        assert str(row["entity_id"]) == store_id
        # F-37: identity-pinned, not merely "somebody" -- "is not None" alone
        # survives attributing the event to any other user at all.
        assert str(row["actor_id"]) == str(owner["id"])
        # F-37 / F-27: a plain rename (no timezone in the body) must be
        # recorded under the spec-named general-case event
        # (api-service-design.md:1188), never the timezone-specific one.
        assert row["event_type"] == "store_settings_updated"
        # The rename must be discoverable from the audit payload somehow --
        # not pinning an exact field name/shape, only that the new name is
        # actually represented in the record of what happened.
        assert "AC04 Audited New Name" in json.dumps(dict(row), default=str)

    def test_ac04_replaying_the_same_rename_operation_id_applies_once(
        self, client, migrated_database
    ):
        """'Any write: the same operation_id replayed produces one effect,'
        pinned for rename specifically. The second call carries a DIFFERENT
        target name under the SAME operation_id -- it must not take effect,
        and exactly one audit row for that operation_id must ever exist.
        """
        with _committed(migrated_database) as c:
            store_id = _create_store_db(c, "AC04 Replay Rename Store")
            owner = _create_user(c, role="owner", password="ac04-replay-owner-pw")
        token = _sign_in(client, owner, with_mfa=True)

        op_id = _idempotency_key()
        first = _patch_store(client, token, store_id, {"name": "AC04 Replay First"}, operation_id=op_id)
        second = _patch_store(client, token, store_id, {"name": "AC04 Replay Second"}, operation_id=op_id)

        assert first.status_code == 200, first.text
        assert second.status_code in (200, 409)  # must not silently succeed with the second name

        row = _fetch_store_row(migrated_database, store_id)
        assert row["name"] == "AC04 Replay First", (
            "a replayed operation_id must not re-apply a different payload"
        )

        audit_rows = _fetch_audit_rows(migrated_database, op_id)
        assert len(audit_rows) == 1


# ---------------------------------------------------------------------------
# Cross-cutting: UTC storage (AGENTS.md non-negotiable #5 /. project
# invariant), pinned once here because store creation is the first write in
# this file that produces its own timestamptz columns rather than reusing
# ones from a fixture.
# ---------------------------------------------------------------------------


class TestStoreTimestampsAreUtc:
    def test_created_at_and_updated_at_are_timezone_aware_utc(self, client, migrated_database):
        with _committed(migrated_database) as c:
            owner = _create_user(c, role="owner", password="utc-owner-pw")
        token = _sign_in(client, owner, with_mfa=True)

        r = _post_store(client, token, _store_payload(name="UTC Timestamp Store"))
        assert r.status_code == 201, r.text

        row = _fetch_store_row(migrated_database, r.json()["data"]["id"])
        assert row["created_at"].tzinfo is not None
        assert row["created_at"].utcoffset() == timedelta(0)
        assert row["updated_at"].tzinfo is not None
        assert row["updated_at"].utcoffset() == timedelta(0)


# ---------------------------------------------------------------------------
# invariant-auditor findings on commit d0c0d17 -- F-26 through F-41.
# ---------------------------------------------------------------------------


class TestF26ManagedFromIsOwnerOnlyInterim:
    """F-26 / B-13 (US-003 audit). The capability matrix alone
    (walmart-user-stories.md:671, "Change store settings: Owner yes, Admin
    yes") would let an Admin move managed_from. walmart-user-stories.md:171
    ("set explicitly by the Owner"), database-schema.md:193 ("Owner may
    change it, audited") and walmart-user-stories.md:693 (Admin may not
    "re-post an order into or out of a protected period", which is exactly
    the effect of moving managed_from across the pre_onboarding boundary)
    all point the other way. Interim handling, an Owner decision: moving
    managed_from is Owner-only, independent of the general
    settings-change permission -- a per-field check, not a second endpoint.
    """

    def test_f26_admin_can_change_name(self, client, migrated_database):
        """Control: the field-specific restriction must not have widened
        into a blanket Admin lockout -- Admin still has the general
        settings-change capability for every other field."""
        with _committed(migrated_database) as c:
            store_id = _create_store_db(c, "F26 Admin Name Store")
            admin = _create_user(c, role="admin", password="f26-admin-name-pw")
            _assign(c, admin["id"], store_id)
        token = _sign_in(client, admin, with_mfa=True)

        r = _patch_store(client, token, store_id, {"name": "F26 Admin Renamed"})
        assert r.status_code == 200, r.text

    def test_f26_admin_cannot_change_managed_from(self, client, migrated_database):
        with _committed(migrated_database) as c:
            store_id = _create_store_db(c, "F26 Admin Managed From Store")
            admin = _create_user(c, role="admin", password="f26-admin-mf-pw")
            _assign(c, admin["id"], store_id)
        token = _sign_in(client, admin, with_mfa=True)

        r = _patch_store(
            client, token, store_id, {"managed_from_year": 2027, "managed_from_month": 1}
        )
        assert r.status_code == 403, r.text

        row = _fetch_store_row(migrated_database, store_id)
        assert row["managed_from_year"] == 2026
        assert row["managed_from_month"] == 7

    def test_f26_admin_cannot_change_managed_from_even_combined_with_an_allowed_field(
        self, client, migrated_database
    ):
        """The whole request is rejected, not partially applied -- a
        request that also renames must not let the name change through
        while the managed_from change is merely dropped, or vice versa."""
        with _committed(migrated_database) as c:
            store_id = _create_store_db(c, "F26 Admin Combined Store")
            admin = _create_user(c, role="admin", password="f26-admin-combined-pw")
            _assign(c, admin["id"], store_id)
        token = _sign_in(client, admin, with_mfa=True)

        r = _patch_store(
            client,
            token,
            store_id,
            {
                "name": "F26 Should Not Apply",
                "managed_from_year": 2027,
                "managed_from_month": 1,
            },
        )
        assert r.status_code == 403, r.text

        row = _fetch_store_row(migrated_database, store_id)
        assert row["name"] == "F26 Admin Combined Store"
        assert row["managed_from_year"] == 2026

    def test_f26_owner_can_change_managed_from(self, client, migrated_database):
        with _committed(migrated_database) as c:
            store_id = _create_store_db(c, "F26 Owner Managed From Store")
            owner = _create_user(c, role="owner", password="f26-owner-mf-pw")
        token = _sign_in(client, owner, with_mfa=True)

        r = _patch_store(
            client, token, store_id, {"managed_from_year": 2027, "managed_from_month": 1}
        )
        assert r.status_code == 200, r.text

        row = _fetch_store_row(migrated_database, store_id)
        assert row["managed_from_year"] == 2027
        assert row["managed_from_month"] == 1

    def test_f26_manager_cannot_change_managed_from(self, client, migrated_database):
        with _committed(migrated_database) as c:
            store_id = _create_store_db(c, "F26 Manager Managed From Store")
            manager = _create_user(c, role="manager", password="f26-manager-mf-pw")
            _assign(c, manager["id"], store_id)
        token = _sign_in(client, manager, with_mfa=False)

        r = _patch_store(
            client, token, store_id, {"managed_from_year": 2027, "managed_from_month": 1}
        )
        assert r.status_code == 403, r.text


class TestF27EventTypeDistinguishesTimezoneChange:
    """F-27 (US-003 audit). api-service-design.md names
    store_timezone_changed (:1195) and store_settings_updated (:1188)
    explicitly -- neither is a B-12 gap. A timezone change moves every
    month boundary already resolved against the old value (BR-14); a
    cosmetic rename does not, and the audit log must be able to tell the
    two apart without reading before/after state.
    """

    def test_f27_timezone_change_emits_store_timezone_changed(self, client, migrated_database):
        with _committed(migrated_database) as c:
            store_id = _create_store_db(
                c, "F27 Timezone Change Store", timezone="America/New_York"
            )
            owner = _create_user(c, role="owner", password="f27-tz-owner-pw")
        token = _sign_in(client, owner, with_mfa=True)

        op_id = _idempotency_key()
        r = _patch_store(
            client, token, store_id, {"timezone": "America/Chicago"}, operation_id=op_id
        )
        assert r.status_code == 200, r.text

        rows = _fetch_audit_rows(migrated_database, op_id)
        assert len(rows) == 1
        assert rows[0]["event_type"] == "store_timezone_changed"

    def test_f27_name_change_emits_the_general_settings_event_not_the_timezone_one(
        self, client, migrated_database
    ):
        with _committed(migrated_database) as c:
            store_id = _create_store_db(c, "F27 Name Only Change Store")
            owner = _create_user(c, role="owner", password="f27-name-owner-pw")
        token = _sign_in(client, owner, with_mfa=True)

        op_id = _idempotency_key()
        r = _patch_store(
            client, token, store_id, {"name": "F27 Name Only Renamed"}, operation_id=op_id
        )
        assert r.status_code == 200, r.text

        rows = _fetch_audit_rows(migrated_database, op_id)
        assert len(rows) == 1
        assert rows[0]["event_type"] == "store_settings_updated"
        assert rows[0]["event_type"] != "store_timezone_changed"

    def test_f27_timezone_changed_together_with_name_still_emits_the_timezone_event(
        self, client, migrated_database
    ):
        """A timezone move is the more significant fact and must not be
        lost inside a generic settings-updated entry just because the
        caller also renamed the store in the same request."""
        with _committed(migrated_database) as c:
            store_id = _create_store_db(
                c, "F27 Combined Change Store", timezone="America/New_York"
            )
            owner = _create_user(c, role="owner", password="f27-combined-owner-pw")
        token = _sign_in(client, owner, with_mfa=True)

        op_id = _idempotency_key()
        r = _patch_store(
            client,
            token,
            store_id,
            {"name": "F27 Combined Renamed", "timezone": "America/Denver"},
            operation_id=op_id,
        )
        assert r.status_code == 200, r.text

        rows = _fetch_audit_rows(migrated_database, op_id)
        assert len(rows) == 1
        assert rows[0]["event_type"] == "store_timezone_changed"


class TestF28AuthenticationGateForWrites:
    """F-28 (US-003 audit). F-01 (US-001) all over again, on two endpoints
    that had no authentication test of their own at all -- deleting
    ``if not header: raise SessionInvalid(...)`` from the shared gate
    (``main._authenticate_request``, formerly duplicated verbatim inside
    ``request_actor``) turned an anonymous ``POST /stores`` into an
    unhandled 500 while the rest of the suite stayed green. See the session
    report for the mutation run against these tests specifically.
    """

    # -- POST /stores -----------------------------------------------------

    def test_f28_post_stores_rejects_no_credential(self, client, migrated_database):
        r = client.post(
            "/stores", json=_store_payload(), headers={"Idempotency-Key": _idempotency_key()}
        )
        assert r.status_code == 401
        assert r.json()["detail"]["code"] == "AUTHENTICATION_REQUIRED"

    def test_f28_post_stores_rejects_a_malformed_credential(self, client, migrated_database):
        r = client.post(
            "/stores",
            json=_store_payload(),
            headers={
                "Authorization": "Basic not-a-bearer-token",
                "Idempotency-Key": _idempotency_key(),
            },
        )
        assert r.status_code == 401
        assert r.json()["detail"]["code"] == "AUTHENTICATION_REQUIRED"

    def test_f28_post_stores_rejects_an_expired_session(self, client, migrated_database):
        with _committed(migrated_database) as c:
            owner = _create_user(c, role="owner", password="f28-post-expired-owner-pw")
        token = _sign_in(client, owner, with_mfa=True)
        _expire_session(migrated_database, owner["id"])

        r = _post_store(client, token, _store_payload())
        assert r.status_code == 401
        assert r.json()["detail"]["code"] == "AUTHENTICATION_REQUIRED"

    def test_f28_post_stores_rejects_a_revoked_session(self, client, migrated_database):
        with _committed(migrated_database) as c:
            owner = _create_user(c, role="owner", password="f28-post-revoked-owner-pw")
        token = _sign_in(client, owner, with_mfa=True)
        _revoke_session(migrated_database, owner["id"])

        r = _post_store(client, token, _store_payload())
        assert r.status_code == 401
        assert r.json()["detail"]["code"] == "AUTHENTICATION_REQUIRED"

    def test_f28_post_stores_rejects_a_deactivated_user(self, client, migrated_database):
        with _committed(migrated_database) as c:
            owner = _create_user(c, role="owner", password="f28-post-deact-owner-pw")
        token = _sign_in(client, owner, with_mfa=True)
        _deactivate_user(migrated_database, owner["id"])

        r = _post_store(client, token, _store_payload())
        assert r.status_code == 401
        assert r.json()["detail"]["code"] == "AUTHENTICATION_REQUIRED"

    # -- PATCH /stores/{id} -------------------------------------------------

    def test_f28_patch_store_rejects_no_credential(self, client, migrated_database):
        with _committed(migrated_database) as c:
            store_id = _create_store_db(c, "F28 Patch No Credential Store")

        r = client.patch(
            f"/stores/{store_id}",
            json={"name": "x"},
            headers={"Idempotency-Key": _idempotency_key()},
        )
        assert r.status_code == 401
        assert r.json()["detail"]["code"] == "AUTHENTICATION_REQUIRED"

    def test_f28_patch_store_rejects_a_malformed_credential(self, client, migrated_database):
        with _committed(migrated_database) as c:
            store_id = _create_store_db(c, "F28 Patch Malformed Store")

        r = client.patch(
            f"/stores/{store_id}",
            json={"name": "x"},
            headers={"Authorization": "Token garbage", "Idempotency-Key": _idempotency_key()},
        )
        assert r.status_code == 401
        assert r.json()["detail"]["code"] == "AUTHENTICATION_REQUIRED"

    def test_f28_patch_store_rejects_an_expired_session(self, client, migrated_database):
        with _committed(migrated_database) as c:
            store_id = _create_store_db(c, "F28 Patch Expired Store")
            owner = _create_user(c, role="owner", password="f28-patch-expired-owner-pw")
        token = _sign_in(client, owner, with_mfa=True)
        _expire_session(migrated_database, owner["id"])

        r = _patch_store(client, token, store_id, {"name": "x"})
        assert r.status_code == 401
        assert r.json()["detail"]["code"] == "AUTHENTICATION_REQUIRED"

    def test_f28_patch_store_rejects_a_revoked_session(self, client, migrated_database):
        with _committed(migrated_database) as c:
            store_id = _create_store_db(c, "F28 Patch Revoked Store")
            owner = _create_user(c, role="owner", password="f28-patch-revoked-owner-pw")
        token = _sign_in(client, owner, with_mfa=True)
        _revoke_session(migrated_database, owner["id"])

        r = _patch_store(client, token, store_id, {"name": "x"})
        assert r.status_code == 401
        assert r.json()["detail"]["code"] == "AUTHENTICATION_REQUIRED"

    def test_f28_patch_store_rejects_a_deactivated_user(self, client, migrated_database):
        with _committed(migrated_database) as c:
            store_id = _create_store_db(c, "F28 Patch Deactivated Store")
            owner = _create_user(c, role="owner", password="f28-patch-deact-owner-pw")
        token = _sign_in(client, owner, with_mfa=True)
        _deactivate_user(migrated_database, owner["id"])

        r = _patch_store(client, token, store_id, {"name": "x"})
        assert r.status_code == 401
        assert r.json()["detail"]["code"] == "AUTHENTICATION_REQUIRED"


class TestF29ResponseNeverDefaultsMissingConfiguration:
    """F-29 (US-003 audit). AC-02 was proved at the database
    (TestAC02MissingConfigurationIsIdentified above) but never at the HTTP
    response -- mutating ``_store_body`` to
    ``store["timezone"] or "UTC"`` kept all 135 tests green beforehand,
    because every existing assertion read the database row directly and
    none inspected these response keys. SC-05 does not catch this either:
    it is scoped to currency. These assertions check for explicit
    presence of a null value, not merely its absence -- an omitted key is
    ambiguous ('did not ask' vs 'actively unset'); a present ``null`` is
    not.
    """

    def test_f29_list_detail_create_and_update_all_report_null_explicitly(
        self, client, migrated_database
    ):
        with _committed(migrated_database) as c:
            owner = _create_user(c, role="owner", password="f29-owner-pw")
        token = _sign_in(client, owner, with_mfa=True)

        created = _post_store(
            client, token, _without(_store_payload(), "timezone", "currency_code")
        )
        assert created.status_code == 201, created.text
        store_id = created.json()["data"]["id"]

        detail = client.get(f"/stores/{store_id}", headers=_auth(token))
        assert detail.status_code == 200

        for label, body in (
            ("create", created.json()["data"]),
            ("detail", detail.json()["data"]),
        ):
            assert "timezone" in body, f"{label}: timezone key must be present, not omitted"
            assert body["timezone"] is None, f"{label}: timezone must be null, not defaulted"
            assert "currency_code" in body, f"{label}: currency_code key must be present"
            assert (
                body["currency_code"] is None
            ), f"{label}: currency_code must be null, not defaulted"

        listing = client.get("/stores", headers=_auth(token))
        assert listing.status_code == 200
        [list_row] = [r for r in listing.json()["data"] if r["id"] == store_id]
        assert "timezone" in list_row and list_row["timezone"] is None
        assert "currency_code" in list_row and list_row["currency_code"] is None

        # PATCH touching an unrelated field (name) must still report the
        # still-missing configuration as null, not as a value invented by
        # the update path.
        patched = _patch_store(client, token, store_id, {"name": "F29 Renamed"})
        assert patched.status_code == 200, patched.text
        patched_body = patched.json()["data"]
        assert "timezone" in patched_body and patched_body["timezone"] is None
        assert "currency_code" in patched_body and patched_body["currency_code"] is None


class TestF30ScopeIsRequiredForTheIdentityUpdate:
    """F-30 (US-003 audit). ``scoping.py``'s own module docstring advertises
    "scope is a required argument ... forgetting a scope becomes a type
    error at the call site", and ``repository.py``'s advertises the filter
    being "composed into the SQL rather than applied afterwards" --
    ``update_store_identity`` honoured neither before this fix.
    """

    def test_f30_update_store_identity_requires_a_scope(self, conn):
        from src.stores import repository

        with pytest.raises(TypeError):
            repository.update_store_identity(conn, uuid.uuid4(), {"name": "x"})  # no scope

    def test_f30_scope_predicate_is_composed_into_the_update_statement(self, conn):
        """A rewrite that drops the scope filter from the UPDATE's WHERE
        (trusting only the caller's earlier, separate get_store check)
        passes every *behavioural* assertion elsewhere in this file, since
        the service layer always calls get_store first -- this spies on the
        statement actually sent to Postgres and pins that the UPDATE itself
        carries the scope predicate.
        """
        from src.scoping import StoreScope
        from src.stores import repository

        row = conn.execute(
            """
            insert into stores (name, walmart_store_key, timezone,
                                reporting_currency_code, managed_from_year,
                                managed_from_month, is_active)
            values (%s, %s, 'America/New_York', 'USD', 2026, 7, true)
            returning id
            """,
            (f"F30 store {uuid.uuid4().hex[:8]}", f"F30-{uuid.uuid4().hex[:12]}"),
        ).fetchone()
        store_id = row["id"]

        # A scope that genuinely permits this store, so the statement
        # captured is the real one and the update actually succeeds.
        scope = StoreScope.assigned(actor_id=uuid.uuid4(), store_ids=[store_id])

        captured: list[tuple[str, object]] = []
        real_execute = conn.execute

        def _spy_execute(query, params=None, **kwargs):
            captured.append((str(query), params))
            return real_execute(query, params, **kwargs)

        conn.execute = _spy_execute
        try:
            updated = repository.update_store_identity(
                conn, scope, store_id, {"name": "F30 Renamed"}
            )
        finally:
            conn.execute = real_execute

        assert updated["name"] == "F30 Renamed"

        where_clause, scope_params = scope.sql_filter("id")
        update_statements = [
            (sql, params) for sql, params in captured if "update stores" in sql.lower()
        ]
        assert update_statements, (
            "no UPDATE statement against stores was captured; statements sent "
            f"were: {[s for s, _ in captured]!r}"
        )
        matching = [(sql, params) for sql, params in update_statements if where_clause in sql]
        assert matching, (
            "the scope predicate was never composed into the UPDATE statement; "
            f"statements sent were: {[s for s, _ in captured]!r}"
        )
        sql, params = matching[0]
        for key, value in scope_params.items():
            assert params.get(key) == value

    def test_f30_update_raises_out_of_scope_for_a_store_outside_the_given_scope(self, conn):
        """Defence in depth: even if a future caller skipped the earlier
        get_store check, the UPDATE itself must still refuse the row."""
        from src.scoping import OutOfScope, StoreScope
        from src.stores import repository

        row = conn.execute(
            """
            insert into stores (name, walmart_store_key, timezone,
                                reporting_currency_code, managed_from_year,
                                managed_from_month, is_active)
            values (%s, %s, 'America/New_York', 'USD', 2026, 7, true)
            returning id
            """,
            (f"F30 oos store {uuid.uuid4().hex[:8]}", f"F30-OOS-{uuid.uuid4().hex[:12]}"),
        ).fetchone()
        store_id = row["id"]

        empty_scope = StoreScope.assigned(actor_id=uuid.uuid4(), store_ids=[])

        with pytest.raises(OutOfScope):
            repository.update_store_identity(
                conn, empty_scope, store_id, {"name": "should not apply"}
            )

        unchanged = conn.execute(
            "select name from stores where id = %s", (store_id,)
        ).fetchone()
        assert unchanged["name"] != "should not apply"


class TestF31ReplayIsBoundToEntityAndEventType:
    """F-31 (US-003 audit). ``DuplicateOperation`` alone only says "this
    operation_id has been used before" -- not "for this request". Both
    scenarios below are adversarial replays the test author deliberately
    left ``assert second.status_code in (200, 409)`` open for; the
    pre-fix implementation always took the 200 branch.
    """

    def test_f31_same_key_against_two_different_stores_is_a_conflict_not_a_replay(
        self, client, migrated_database
    ):
        with _committed(migrated_database) as c:
            store_a = _create_store_db(c, "F31 Store A")
            store_b = _create_store_db(c, "F31 Store B")
            owner = _create_user(c, role="owner", password="f31-two-stores-owner-pw")
        token = _sign_in(client, owner, with_mfa=True)

        op_id = _idempotency_key()
        first = _patch_store(client, token, store_a, {"name": "F31 A Renamed"}, operation_id=op_id)
        assert first.status_code == 200, first.text

        second = _patch_store(
            client, token, store_b, {"name": "F31 B Should Not Apply"}, operation_id=op_id
        )
        assert second.status_code == 409, second.text

        row_b = _fetch_store_row(migrated_database, store_b)
        assert row_b["name"] == "F31 Store B", "a key spent on store A must not touch store B"

        audit_rows = _fetch_audit_rows(migrated_database, op_id)
        assert len(audit_rows) == 1
        assert str(audit_rows[0]["entity_id"]) == str(store_a)

    def test_f31_same_key_reused_across_a_different_operation_type_is_a_conflict(
        self, client, migrated_database
    ):
        with _committed(migrated_database) as c:
            store_id = _create_store_db(c, "F31 Note Then Create Store")
            owner = _create_user(c, role="owner", password="f31-cross-op-owner-pw")
        token = _sign_in(client, owner, with_mfa=True)

        op_id = _idempotency_key()
        note = client.post(
            f"/stores/{store_id}/notes",
            json={"note": "F31 cross-operation key"},
            headers={**_auth(token), "Idempotency-Key": op_id},
        )
        assert note.status_code == 201, note.text

        create_payload = _store_payload(name="F31 Should Not Be Created")
        reused = _post_store(client, token, create_payload, operation_id=op_id)
        assert reused.status_code == 409, reused.text

        assert (
            _count_stores_matching(
                migrated_database, walmart_store_key=create_payload["walmart_store_key"]
            )
            == 0
        )

        audit_rows = _fetch_audit_rows(migrated_database, op_id)
        assert len(audit_rows) == 1
        assert audit_rows[0]["event_type"] == "store_note_added"


class TestF32UpdateValidationMatchesCreate:
    """F-32 (US-003 audit). ``{"name": null}`` was "set" under
    ``exclude_unset=True`` -> ``set name = NULL`` against a ``NOT NULL``
    column -> an unhandled 500 on a syntactically valid request.
    ``{"name": ""}`` renamed to empty string, which create already forbids.
    Decided: explicit null is rejected for every field -- no document
    grants any role the power to blank a store's timezone, currency or
    managed_from once a value exists. Omit the field entirely to leave it
    unchanged; that path is unaffected (see the control test below).
    """

    def test_f32_explicit_null_name_is_rejected_not_a_500(self, client, migrated_database):
        with _committed(migrated_database) as c:
            store_id = _create_store_db(c, "F32 Null Name Store")
            owner = _create_user(c, role="owner", password="f32-null-name-owner-pw")
        token = _sign_in(client, owner, with_mfa=True)

        r = _patch_store(client, token, store_id, {"name": None})
        assert r.status_code == 422, r.text

        row = _fetch_store_row(migrated_database, store_id)
        assert row["name"] == "F32 Null Name Store"

    def test_f32_empty_string_name_is_rejected(self, client, migrated_database):
        with _committed(migrated_database) as c:
            store_id = _create_store_db(c, "F32 Empty Name Store")
            owner = _create_user(c, role="owner", password="f32-empty-name-owner-pw")
        token = _sign_in(client, owner, with_mfa=True)

        r = _patch_store(client, token, store_id, {"name": ""})
        assert r.status_code == 422, r.text

        row = _fetch_store_row(migrated_database, store_id)
        assert row["name"] == "F32 Empty Name Store"

    def test_f32_explicit_null_timezone_is_rejected(self, client, migrated_database):
        with _committed(migrated_database) as c:
            store_id = _create_store_db(
                c, "F32 Null Timezone Store", timezone="America/Chicago"
            )
            owner = _create_user(c, role="owner", password="f32-null-tz-owner-pw")
        token = _sign_in(client, owner, with_mfa=True)

        r = _patch_store(client, token, store_id, {"timezone": None})
        assert r.status_code == 422, r.text

        row = _fetch_store_row(migrated_database, store_id)
        assert row["timezone"] == "America/Chicago"

    def test_f32_explicit_null_currency_code_is_rejected(self, client, migrated_database):
        with _committed(migrated_database) as c:
            store_id = _create_store_db(
                c, "F32 Null Currency Store", reporting_currency_code="CAD"
            )
            owner = _create_user(c, role="owner", password="f32-null-cur-owner-pw")
        token = _sign_in(client, owner, with_mfa=True)

        r = _patch_store(client, token, store_id, {"currency_code": None})
        assert r.status_code == 422, r.text

        row = _fetch_store_row(migrated_database, store_id)
        assert row["reporting_currency_code"] == "CAD"

    def test_f32_explicit_null_managed_from_is_rejected(self, client, migrated_database):
        with _committed(migrated_database) as c:
            store_id = _create_store_db(c, "F32 Null Managed From Store")
            owner = _create_user(c, role="owner", password="f32-null-mf-owner-pw")
        token = _sign_in(client, owner, with_mfa=True)

        r = _patch_store(
            client,
            token,
            store_id,
            {"managed_from_year": None, "managed_from_month": None},
        )
        assert r.status_code == 422, r.text

        row = _fetch_store_row(migrated_database, store_id)
        assert row["managed_from_year"] == 2026
        assert row["managed_from_month"] == 7

    def test_f32_omitting_a_field_entirely_still_leaves_it_unchanged(
        self, client, migrated_database
    ):
        """Control: the fix rejects explicit null without breaking the
        ordinary, already-tested 'omit to leave unchanged' path."""
        with _committed(migrated_database) as c:
            store_id = _create_store_db(
                c, "F32 Omit Control Store", timezone="America/Chicago"
            )
            owner = _create_user(c, role="owner", password="f32-omit-owner-pw")
        token = _sign_in(client, owner, with_mfa=True)

        r = _patch_store(client, token, store_id, {"name": "F32 Omit Control Renamed"})
        assert r.status_code == 200, r.text

        row = _fetch_store_row(migrated_database, store_id)
        assert row["name"] == "F32 Omit Control Renamed"
        assert row["timezone"] == "America/Chicago"


class TestF36NameLengthIsNotArbitrarilyCapped:
    def test_f36_a_long_name_is_accepted_on_create(self, client, migrated_database):
        """F-36 (US-003 audit): max_length=200 was invented -- ``name`` is
        an unconstrained ``text`` column (001_baseline.sql). A
        300-character name must be accepted, not rejected by a length
        nobody specified."""
        with _committed(migrated_database) as c:
            owner = _create_user(c, role="owner", password="f36-long-name-owner-pw")
        token = _sign_in(client, owner, with_mfa=True)

        long_name = "F36 Long Name " + ("x" * 300)
        r = _post_store(client, token, _store_payload(name=long_name))
        assert r.status_code == 201, r.text
        assert r.json()["data"]["name"] == long_name


class TestF39EmptyPatchIsANoOp:
    def test_f39_empty_patch_body_writes_no_audit_event_or_timestamp_bump(
        self, client, migrated_database
    ):
        with _committed(migrated_database) as c:
            store_id = _create_store_db(c, "F39 Empty Patch Store")
            owner = _create_user(c, role="owner", password="f39-empty-owner-pw")
        token = _sign_in(client, owner, with_mfa=True)

        before_row = _fetch_store_row(migrated_database, store_id)

        op_id = _idempotency_key()
        r = _patch_store(client, token, store_id, {}, operation_id=op_id)
        assert r.status_code == 200, r.text

        audit_rows = _fetch_audit_rows(migrated_database, op_id)
        assert audit_rows == [], "an empty patch must not write a spurious audit event"

        after_row = _fetch_store_row(migrated_database, store_id)
        assert after_row["updated_at"] == before_row["updated_at"], (
            "an empty patch must not bump updated_at for a change that did not happen"
        )
