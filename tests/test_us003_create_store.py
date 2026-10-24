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

        owner_token = _sign_in(client, owner, with_mfa=True)
        manager_token = _sign_in(client, manager, with_mfa=False)

        before = client.get("/stores", headers=_auth(manager_token))
        assert store_id in {row["id"] for row in before.json()["data"]}

        renamed = _patch_store(
            client, owner_token, store_id, {"name": "AC04 Assignment After Rename"}
        )
        assert renamed.status_code == 200, renamed.text

        after = client.get("/stores", headers=_auth(manager_token))
        after_rows = {row["id"]: row["name"] for row in after.json()["data"]}
        assert store_id in after_rows
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
        assert row["actor_id"] is not None
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
