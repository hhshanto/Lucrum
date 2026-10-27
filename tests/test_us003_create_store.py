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
        # the original outcome, not a generic error. F-52 (US-003 audit):
        # this was a disjunctive assertion that accepted both the right
        # answer and the wrong one -- entity_id and event_type genuinely
        # match here (same store, same create), so this is unambiguously a
        # replay and must be exactly 200, never 201 (nothing was created on
        # this call), and meta must say so explicitly.
        assert second.status_code == 200, second.text
        assert second.json()["meta"]["replayed"] is True
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
        # F-52 (US-003 audit): same store, same computed event_type both
        # times (only ``name`` differs) -- genuinely a replay, never a
        # conflict, so this is exactly 200, not merely "not 200 and not
        # something worse". meta must say so explicitly too.
        assert second.status_code == 200, second.text
        assert second.json()["meta"]["replayed"] is True

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


# ---------------------------------------------------------------------------
# invariant-auditor pass 2 on d0c0d17 -- F-44 through F-53.
# ---------------------------------------------------------------------------


class TestF44UnknownFieldsAreRejectedNotIgnored:
    """F-44 (US-003 audit). Pydantic's default ``extra='ignore'`` silently
    dropped any field it did not recognise -- most dangerously
    ``reporting_currency_code`` on PATCH, the column's REAL name
    (database-schema.md:191, this module's own ``_IDENTITY_COLUMNS``),
    which the F-39 no-op fix then reported back as a confident 200
    "unchanged" rather than even bumping ``updated_at``.
    """

    def test_f44_post_stores_rejects_an_unknown_field(self, client, migrated_database):
        with _committed(migrated_database) as c:
            owner = _create_user(c, role="owner", password="f44-post-owner-pw")
        token = _sign_in(client, owner, with_mfa=True)

        payload = _store_payload()
        payload["time_zone"] = "America/New_York"  # not a field this API accepts
        r = _post_store(client, token, payload)
        assert r.status_code == 422, r.text

    def test_f44_patch_store_rejects_the_real_column_name_reporting_currency_code(
        self, client, migrated_database
    ):
        with _committed(migrated_database) as c:
            store_id = _create_store_db(
                c, "F44 Real Column Name Store", reporting_currency_code="USD"
            )
            owner = _create_user(c, role="owner", password="f44-patch-col-owner-pw")
        token = _sign_in(client, owner, with_mfa=True)

        r = _patch_store(client, token, store_id, {"reporting_currency_code": "EUR"})
        assert r.status_code == 422, r.text

        row = _fetch_store_row(migrated_database, store_id)
        assert row["reporting_currency_code"] == "USD", (
            "an unrecognised field must be rejected, not silently ignored while "
            "reporting 200 'unchanged'"
        )

    def test_f44_patch_store_rejects_is_active(self, client, migrated_database):
        with _committed(migrated_database) as c:
            store_id = _create_store_db(c, "F44 Is Active Store", is_active=True)
            owner = _create_user(c, role="owner", password="f44-patch-active-owner-pw")
        token = _sign_in(client, owner, with_mfa=True)

        r = _patch_store(client, token, store_id, {"is_active": False})
        assert r.status_code == 422, r.text

        row = _fetch_store_row(migrated_database, store_id)
        assert row["is_active"] is True


class TestF45DuplicateWalmartStoreKeyIsAConflictNotA500:
    def test_f45_creating_a_store_with_an_existing_walmart_store_key_returns_409(
        self, client, migrated_database
    ):
        with _committed(migrated_database) as c:
            owner = _create_user(c, role="owner", password="f45-dup-owner-pw")
        token = _sign_in(client, owner, with_mfa=True)

        shared_key = f"F45-DUP-{uuid.uuid4().hex[:12]}"
        first = _post_store(client, token, _store_payload(walmart_store_key=shared_key))
        assert first.status_code == 201, first.text

        second = _post_store(
            client,
            token,
            _store_payload(name="F45 Second Attempt", walmart_store_key=shared_key),
        )
        assert second.status_code == 409, second.text
        assert second.json()["detail"]["field"] == "walmart_store_key"

        assert _count_stores_matching(migrated_database, walmart_store_key=shared_key) == 1


class TestF46AddStoreNoteUsesTheSameReplayDiscipline:
    """F-46 (US-003 audit). ``add_store_note``'s own docstring claims it
    "exists so that every write endpoint in slices 1 onward has a correct
    pattern to copy" -- it must actually carry every rule the pattern
    claims, including the one introduced in this same commit for the two
    newer endpoints and left out of the reference one.
    """

    def test_f46_a_key_spent_on_a_patch_cannot_be_replayed_as_a_note(
        self, client, migrated_database
    ):
        with _committed(migrated_database) as c:
            store_id = _create_store_db(c, "F46 Patch Then Note Store")
            owner = _create_user(c, role="owner", password="f46-cross-owner-pw")
        token = _sign_in(client, owner, with_mfa=True)

        op_id = _idempotency_key()
        patched = _patch_store(
            client, token, store_id, {"name": "F46 Renamed"}, operation_id=op_id
        )
        assert patched.status_code == 200, patched.text

        note = client.post(
            f"/stores/{store_id}/notes",
            json={"note": "F46 should not be recorded"},
            headers={**_auth(token), "Idempotency-Key": op_id},
        )
        assert note.status_code == 409, note.text

        audit_rows = _fetch_audit_rows(migrated_database, op_id)
        assert len(audit_rows) == 1
        assert audit_rows[0]["event_type"] == "store_settings_updated"
        assert "F46 should not be recorded" not in json.dumps(
            dict(audit_rows[0]), default=str
        )


class TestF48CheckReplayTargetEntityTypeIsPinned:
    """F-48 (US-003 audit). Every scenario anywhere else in this file
    happens to carry ``entity_type="store"`` on both sides of the
    comparison, so deleting that leg of ``audit.check_replay_target``
    survives the whole HTTP-level suite. A direct unit test against the
    pure function is what actually pins it -- not more HTTP coverage.
    """

    def test_f48_a_different_entity_type_is_a_conflict_even_with_a_matching_event_type(
        self,
    ):
        from src import audit

        existing_event = {
            "operation_id": "f48-op",
            "event_type": "store_created",
            "entity_type": "user",  # deliberately not "store"
            "entity_id": uuid.uuid4(),
        }
        with pytest.raises(audit.ReplayConflict):
            audit.check_replay_target(
                existing_event, event_type="store_created", entity_type="store"
            )


class TestF49EmptyPatchStillGoesThroughRoleAndScope:
    """F-49 (US-003 audit). The F-39 no-op-for-an-empty-patch return sits
    after the role check and the scope fetch today -- untested, and
    exactly the kind of ordering a later refactor moves without noticing.
    """

    def test_f49_manager_empty_patch_is_still_403_not_200(self, client, migrated_database):
        with _committed(migrated_database) as c:
            store_id = _create_store_db(c, "F49 Manager Empty Patch Store")
            manager = _create_user(c, role="manager", password="f49-manager-empty-pw")
            _assign(c, manager["id"], store_id)
        token = _sign_in(client, manager, with_mfa=False)

        r = _patch_store(client, token, store_id, {})
        assert r.status_code == 403, r.text

    def test_f49_out_of_scope_empty_patch_is_still_404_not_200(
        self, client, migrated_database
    ):
        with _committed(migrated_database) as c:
            other_store = _create_store_db(c, "F49 Out Of Scope Empty Patch Store")
            admin = _create_user(c, role="admin", password="f49-admin-oos-empty-pw")
            # Deliberately no _assign() call for this admin.
            decoy = _create_user(c, role="manager", password="f49-decoy-pw")
            _assign(c, decoy["id"], other_store)
        token = _sign_in(client, admin, with_mfa=True)

        r = _patch_store(client, token, other_store, {})
        assert r.status_code == 404, r.text


class TestF50CreateStoreReplayUsesTheGivenScope:
    """F-50 (US-003 audit). Reverting the replay fetch in ``create_store``
    to a fabricated ``StoreScope.owner(...)`` survives the whole HTTP
    suite, because the only role permitted to create a store IS the Owner
    -- the fabricated scope and the real caller's scope agree on every
    HTTP-reachable path. This pins it directly at the service layer with a
    scope that does NOT in fact cover the store just created, which only a
    scope genuinely threaded through can satisfy.
    """

    def test_f50_replay_fetch_is_scoped_to_the_caller_not_a_fabricated_owner_scope(
        self, conn
    ):
        from src.scoping import OutOfScope, StoreScope
        from src.stores import service as stores_service

        fields = {
            "name": f"F50 Store {uuid.uuid4().hex[:8]}",
            "walmart_store_key": f"F50-{uuid.uuid4().hex[:12]}",
            "timezone": "America/New_York",
            "reporting_currency_code": "USD",
            "managed_from_year": 2026,
            "managed_from_month": 7,
        }
        op_id = str(uuid.uuid4())

        store, replayed = stores_service.create_store(
            conn,
            StoreScope.owner(actor_id=uuid.uuid4()),
            actor_id=None,
            role="owner",
            fields=fields,
            operation_id=op_id,
        )
        assert replayed is False

        # A scope that does NOT include the store just created -- the real
        # Owner's own scope always would (StoreScope.owner() covers every
        # store unconditionally), so this is only reachable by threading
        # the *given* scope through, never by substituting a fabricated
        # Owner scope back in on the replay path.
        restricted_scope = StoreScope.assigned(actor_id=uuid.uuid4(), store_ids=[])

        with pytest.raises(OutOfScope):
            stores_service.create_store(
                conn,
                restricted_scope,
                actor_id=None,
                role="owner",
                # A different walmart_store_key (F-66, US-003 audit): the
                # F-55 fix means the operation_id pre-check now runs before
                # any insert, so a repeated key would no longer trip the
                # F-45 duplicate-key path first either way. The differing
                # key here instead proves the refusal below comes from the
                # operation_id-replay path (F-50) -- the only path it could
                # possibly come from, since a fresh key rules out F-45.
                fields={
                    **fields,
                    "name": "F50 Should Not Apply",
                    "walmart_store_key": f"F50-second-{uuid.uuid4().hex[:12]}",
                },
                operation_id=op_id,
            )


class TestF53ValidatorsAreActuallyExercised:
    """F-53 (US-003 audit). ``currency_code``'s pattern and the
    ``managed_from`` pairing validator both ship on create AND update with
    no test pinning either -- deleting the pattern validator would turn a
    422 into an unhandled 500 via ``stores_currency_fmt``, silently. Also:
    no test anywhere creates a store via ``POST /stores`` with
    ``managed_from`` omitted -- the HTTP path for a null ``managed_from``
    (as opposed to the DB-layer path, already covered) was unproven.
    """

    def test_f53_create_rejects_a_malformed_currency_code(self, client, migrated_database):
        with _committed(migrated_database) as c:
            owner = _create_user(c, role="owner", password="f53-create-cur-owner-pw")
        token = _sign_in(client, owner, with_mfa=True)

        r = _post_store(client, token, _store_payload(currency_code="usd"))
        assert r.status_code == 422, r.text

    def test_f53_update_rejects_a_malformed_currency_code(self, client, migrated_database):
        with _committed(migrated_database) as c:
            store_id = _create_store_db(
                c, "F53 Update Currency Store", reporting_currency_code="USD"
            )
            owner = _create_user(c, role="owner", password="f53-update-cur-owner-pw")
        token = _sign_in(client, owner, with_mfa=True)

        r = _patch_store(client, token, store_id, {"currency_code": "US"})
        assert r.status_code == 422, r.text

        row = _fetch_store_row(migrated_database, store_id)
        assert row["reporting_currency_code"] == "USD"

    def test_f53_create_rejects_an_unpaired_managed_from(self, client, migrated_database):
        with _committed(migrated_database) as c:
            owner = _create_user(c, role="owner", password="f53-create-mf-owner-pw")
        token = _sign_in(client, owner, with_mfa=True)

        r = _post_store(client, token, _without(_store_payload(), "managed_from_month"))
        assert r.status_code == 422, r.text

    def test_f53_update_rejects_an_unpaired_managed_from(self, client, migrated_database):
        with _committed(migrated_database) as c:
            store_id = _create_store_db(c, "F53 Update Managed From Store")
            owner = _create_user(c, role="owner", password="f53-update-mf-owner-pw")
        token = _sign_in(client, owner, with_mfa=True)

        r = _patch_store(client, token, store_id, {"managed_from_year": 2027})
        assert r.status_code == 422, r.text

        row = _fetch_store_row(migrated_database, store_id)
        assert row["managed_from_year"] == 2026
        assert row["managed_from_month"] == 7

    def test_f53_post_stores_with_managed_from_omitted_is_permitted_and_stored_as_null(
        self, client, migrated_database
    ):
        with _committed(migrated_database) as c:
            owner = _create_user(c, role="owner", password="f53-omit-mf-owner-pw")
        token = _sign_in(client, owner, with_mfa=True)

        payload = _without(_store_payload(), "managed_from_year", "managed_from_month")
        r = _post_store(client, token, payload)
        assert r.status_code == 201, r.text
        assert r.json()["data"]["managed_from_year"] is None
        assert r.json()["data"]["managed_from_month"] is None

        row = _fetch_store_row(migrated_database, r.json()["data"]["id"])
        assert row["managed_from_year"] is None
        assert row["managed_from_month"] is None


# ---------------------------------------------------------------------------
# invariant-auditor pass 3 on 321302a -- F-55 through F-59.
# ---------------------------------------------------------------------------


class TestF55IdenticalPayloadIsAReplayNotADuplicateKeyConflict:
    """F-55 (US-003 audit). The F-45 fix let ``repository.create_store``'s
    insert run BEFORE ``audit.record`` -- an ordinary retry (same body,
    same Idempotency-Key) hits ``stores_walmart_store_key_key`` first and
    is reported as a 409 naming someone else's registration, never reaching
    the replay path at all. This is the case idempotency exists to serve,
    and it was the confidently wrong answer.
    """

    def test_f55_identical_payload_same_operation_id_is_a_replay_not_a_409(
        self, client, migrated_database
    ):
        with _committed(migrated_database) as c:
            owner = _create_user(c, role="owner", password="f55-identical-owner-pw")
        token = _sign_in(client, owner, with_mfa=True)

        op_id = _idempotency_key()
        # The SAME payload object, sent twice under the SAME operation_id --
        # an honest retry, not an adversarial one. walmart_store_key is
        # identical both times, which is exactly the input the F-50 test's
        # own comment named and steered away from.
        payload = _store_payload(name="F55 Identical Retry Store")

        first = _post_store(client, token, payload, operation_id=op_id)
        assert first.status_code == 201, first.text
        first_id = first.json()["data"]["id"]

        second = _post_store(client, token, payload, operation_id=op_id)
        assert second.status_code == 200, second.text
        assert second.json()["meta"]["replayed"] is True
        assert second.json()["data"]["id"] == first_id

        assert (
            _count_stores_matching(
                migrated_database, walmart_store_key=payload["walmart_store_key"]
            )
            == 1
        )


class TestF56TimezoneIsValidatedAgainstTheOneImplementation:
    """F-56 (US-003 audit). ``currency_code`` is checked at both layers
    (the request-model pattern and ``stores_currency_fmt``); ``timezone``
    was checked at neither. ``clock.store_timezone`` is the one existing
    implementation of "is this a real IANA zone" (001_baseline.sql:31 ties
    the column to it by name) -- reused here, not duplicated.
    """

    def test_f56_create_rejects_an_unrecognised_timezone(self, client, migrated_database):
        with _committed(migrated_database) as c:
            owner = _create_user(c, role="owner", password="f56-create-owner-pw")
        token = _sign_in(client, owner, with_mfa=True)

        r = _post_store(client, token, _store_payload(timezone="Mars/Olympus"))
        assert r.status_code == 422, r.text

    def test_f56_create_accepts_a_valid_non_utc_iana_zone(self, client, migrated_database):
        with _committed(migrated_database) as c:
            owner = _create_user(c, role="owner", password="f56-create-valid-owner-pw")
        token = _sign_in(client, owner, with_mfa=True)

        r = _post_store(client, token, _store_payload(timezone="Asia/Tokyo"))
        assert r.status_code == 201, r.text
        assert r.json()["data"]["timezone"] == "Asia/Tokyo"

    def test_f56_update_rejects_an_unrecognised_timezone(self, client, migrated_database):
        with _committed(migrated_database) as c:
            store_id = _create_store_db(
                c, "F56 Update Timezone Store", timezone="America/New_York"
            )
            owner = _create_user(c, role="owner", password="f56-update-owner-pw")
        token = _sign_in(client, owner, with_mfa=True)

        r = _patch_store(client, token, store_id, {"timezone": "Mars/Olympus"})
        assert r.status_code == 422, r.text

        row = _fetch_store_row(migrated_database, store_id)
        assert row["timezone"] == "America/New_York"

    def test_f56_update_accepts_a_valid_non_utc_iana_zone(self, client, migrated_database):
        with _committed(migrated_database) as c:
            store_id = _create_store_db(
                c, "F56 Update Valid Timezone Store", timezone="America/New_York"
            )
            owner = _create_user(c, role="owner", password="f56-update-valid-owner-pw")
        token = _sign_in(client, owner, with_mfa=True)

        r = _patch_store(client, token, store_id, {"timezone": "Europe/Berlin"})
        assert r.status_code == 200, r.text
        assert r.json()["data"]["timezone"] == "Europe/Berlin"


class TestF57RoleCheckedBeforeDuplicateKeyCheck:
    """F-57 (US-003 audit). ``require_can_create_store`` runs before any
    database write is attempted -- unpinned, so moving it below the insert
    would hand a non-Owner a 409 (confirming a key exists) rather than a
    403, reachable before any scope check could intervene. Every other
    AC-03 creation test uses a fresh key and would stay green regardless.
    """

    def test_f57_non_owner_posting_an_already_taken_key_gets_403_not_409(
        self, client, migrated_database
    ):
        with _committed(migrated_database) as c:
            owner = _create_user(c, role="owner", password="f57-owner-pw")
            manager = _create_user(c, role="manager", password="f57-manager-pw")
        owner_token = _sign_in(client, owner, with_mfa=True)
        manager_token = _sign_in(client, manager, with_mfa=False)

        shared_key = f"F57-DUP-{uuid.uuid4().hex[:12]}"
        first = _post_store(
            client, owner_token, _store_payload(walmart_store_key=shared_key)
        )
        assert first.status_code == 201, first.text

        second = _post_store(
            client,
            manager_token,
            _store_payload(name="F57 Should Not Matter", walmart_store_key=shared_key),
        )
        assert second.status_code == 403, second.text
        assert second.status_code != 409


class TestF58StoreNoteRejectsUnknownFields:
    """F-58 (US-003 audit). F-46's shape recurring one commit later:
    ``StoreNote`` still has no ``extra='forbid'`` while both store models
    do, on the endpoint whose own docstring claims to be the reference
    pattern.
    """

    def test_f58_add_store_note_rejects_an_unknown_field(self, client, migrated_database):
        with _committed(migrated_database) as c:
            store_id = _create_store_db(c, "F58 Note Unknown Field Store")
            owner = _create_user(c, role="owner", password="f58-note-owner-pw")
        token = _sign_in(client, owner, with_mfa=True)

        r = client.post(
            f"/stores/{store_id}/notes",
            json={"note": "hello", "visibility": "internal"},
            headers={**_auth(token), "Idempotency-Key": _idempotency_key()},
        )
        assert r.status_code == 422, r.text


class TestF59UniqueViolationReraisesOnAnUnrelatedConstraint:
    """F-59 (US-003 audit). Neither re-raise branch (``service.py``'s
    walmart_store_key check, ``audit.py``'s operation_id check) has a
    reachable negative case in the HTTP suite -- treating *any*
    ``UniqueViolation`` as the named special case would still pass every
    existing test. Both are pinned directly, by monkeypatching the
    statement that raises.
    """

    def test_f59_create_store_reraises_a_unique_violation_on_a_different_constraint(
        self, conn
    ):
        from src.scoping import StoreScope
        from src.stores import repository, service as stores_service

        real_create_store = repository.create_store

        def _boom(*args, **kwargs):
            raise psycopg.errors.UniqueViolation("some other constraint fired")

        repository.create_store = _boom
        try:
            with pytest.raises(psycopg.errors.UniqueViolation):
                stores_service.create_store(
                    conn,
                    StoreScope.owner(actor_id=uuid.uuid4()),
                    actor_id=None,
                    role="owner",
                    fields={
                        "name": "F59 Store",
                        "walmart_store_key": f"F59-{uuid.uuid4().hex[:12]}",
                        "timezone": "America/New_York",
                        "reporting_currency_code": "USD",
                        "managed_from_year": 2026,
                        "managed_from_month": 7,
                    },
                    operation_id=str(uuid.uuid4()),
                )
        finally:
            repository.create_store = real_create_store

    def test_f59_audit_record_reraises_a_unique_violation_on_a_different_constraint(
        self, conn
    ):
        from src import audit

        op_id = str(uuid.uuid4())
        # A real row already exists under this operation_id, from a genuine
        # prior write -- without this, find_by_operation's own "existing is
        # None" fallback would re-raise the injected exception anyway for an
        # unrelated reason, and the guard being tested could be deleted
        # without this test ever noticing. With a real row present, removing
        # the guard changes what gets raised: DuplicateOperation instead of
        # the injected UniqueViolation.
        audit.record(
            conn,
            audit.AuditEvent(
                event_type="f59_probe_original",
                entity_type="store",
                operation_id=op_id,
            ),
        )

        real_execute = conn.execute

        def _spy_execute(sql, *args, **kwargs):
            if "insert into audit_events" in str(sql):
                raise psycopg.errors.UniqueViolation("some other constraint fired")
            return real_execute(sql, *args, **kwargs)

        conn.execute = _spy_execute
        try:
            with pytest.raises(psycopg.errors.UniqueViolation):
                audit.record(
                    conn,
                    audit.AuditEvent(
                        event_type="f59_probe_second",
                        entity_type="store",
                        operation_id=op_id,
                    ),
                )
        finally:
            conn.execute = real_execute


# ---------------------------------------------------------------------------
# invariant-auditor pass 4 on 770012d -- F-62 through F-65.
# ---------------------------------------------------------------------------


class TestF62StoreCreatedAuditEventIsPinned:
    """F-62 (US-003 audit). F-37 pinned the rename event's audit row;
    nobody pinned creation's. ``event_type="store_created"`` ->
    ``"store_settings_updated"`` survived entirely -- and would re-open
    F-31, since a key already spent on a rename would then satisfy
    ``check_replay_target`` on a later create.
    """

    def test_f62_create_store_is_an_audited_write_with_the_correct_event_type(
        self, client, migrated_database
    ):
        with _committed(migrated_database) as c:
            owner = _create_user(c, role="owner", password="f62-create-owner-pw")
        token = _sign_in(client, owner, with_mfa=True)

        op_id = _idempotency_key()
        payload = _store_payload(name="F62 Audited Create Store")
        r = _post_store(client, token, payload, operation_id=op_id)
        assert r.status_code == 201, r.text
        store_id = r.json()["data"]["id"]

        rows = _fetch_audit_rows(migrated_database, op_id)
        assert len(rows) == 1
        row = rows[0]
        assert row["event_type"] == "store_created"
        assert str(row["entity_id"]) == store_id
        assert str(row["store_id"]) == store_id
        # F-37's identity-pinning, mirrored here for the create side.
        assert str(row["actor_id"]) == str(owner["id"])
        assert row["after_state"]["walmart_store_key"] == payload["walmart_store_key"]
        assert row["after_state"]["name"] == payload["name"]

    def test_f62_a_key_spent_on_a_rename_cannot_be_replayed_as_a_create(
        self, client, migrated_database
    ):
        """F-31's guard, pinned on the create side specifically: a key
        already recorded as ``store_settings_updated`` must not satisfy a
        later ``POST /stores`` as if it were a ``store_created`` replay --
        the gap ``event_type="store_created"`` -> ``"store_settings_updated"``
        would otherwise open back up."""
        with _committed(migrated_database) as c:
            store_id = _create_store_db(c, "F62 Patch Then Create Store")
            owner = _create_user(c, role="owner", password="f62-cross-owner-pw")
        token = _sign_in(client, owner, with_mfa=True)

        op_id = _idempotency_key()
        patched = _patch_store(
            client, token, store_id, {"name": "F62 Renamed"}, operation_id=op_id
        )
        assert patched.status_code == 200, patched.text

        create_payload = _store_payload(name="F62 Should Not Be Created")
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
        assert audit_rows[0]["event_type"] == "store_settings_updated"


class TestF63TheConcurrencyBackstopIsStillReachable:
    """F-63 (US-003 audit). The F-55 pre-check sees the connection's own
    prior writes and runs before the insert, so nothing single-threaded
    reaches the ``except audit.DuplicateOperation`` backstop any more --
    before the refactor, that made the backstop's own independent copy of
    F-31's guard and F-34/F-50's caller-scoped fetch untested, and a
    mutation to either could survive undetected. Collapsing both branches
    into ``_replay_created_store`` means a mutation there is caught by
    *any* test that reaches *either* branch -- this one specifically
    forces the backstop branch, deterministically, without threads.
    """

    def test_f63_backstop_replays_when_the_pre_check_misses_a_concurrent_write(
        self, conn
    ):
        from src import audit
        from src.scoping import StoreScope
        from src.stores import service as stores_service

        scope = StoreScope.owner(actor_id=uuid.uuid4())
        op_id = str(uuid.uuid4())
        fields = {
            "name": "F63 Backstop Store",
            "walmart_store_key": f"F63-{uuid.uuid4().hex[:12]}",
            "timezone": "America/New_York",
            "reporting_currency_code": "USD",
            "managed_from_year": 2026,
            "managed_from_month": 7,
        }

        # The "concurrent" write that has already committed under this
        # operation_id by the time the second call below is made.
        first_store, first_replayed = stores_service.create_store(
            conn, scope, actor_id=None, role="owner", fields=fields, operation_id=op_id
        )
        assert first_replayed is False

        real_find_by_operation = audit.find_by_operation
        calls = {"n": 0}

        def _miss_on_the_pre_check_only(conn_, operation_id_):
            calls["n"] += 1
            if calls["n"] == 1:
                # Simulates the race: this row is not visible to the
                # pre-check yet, forcing create_store to actually attempt
                # the insert below.
                return None
            return real_find_by_operation(conn_, operation_id_)

        audit.find_by_operation = _miss_on_the_pre_check_only
        try:
            second_store, second_replayed = stores_service.create_store(
                conn,
                scope,
                actor_id=None,
                role="owner",
                # A different walmart_store_key and name: this call must be
                # refused by the operation_id ledger (audit_events), not by
                # F-45's duplicate-key path.
                fields={
                    **fields,
                    "name": "F63 Should Not Apply",
                    "walmart_store_key": f"F63-second-{uuid.uuid4().hex[:12]}",
                },
                operation_id=op_id,
            )
        finally:
            audit.find_by_operation = real_find_by_operation

        assert second_replayed is True
        assert second_store["id"] == first_store["id"]

        # The second call's own payload must never have been inserted as a
        # row of its own -- the backstop replayed, it did not also create.
        rows = conn.execute(
            "select count(*) from stores where name = %s", ("F63 Should Not Apply",)
        ).fetchone()
        assert rows["count"] == 0


class TestF64UpdatingCurrencyCodeActuallyWorks:
    """F-64 (US-003 audit). Every existing ``currency_code`` test on PATCH
    was either a creation assertion or a rejection -- the one request field
    whose name differs from its column (F-44's exact asymmetry) had never
    been exercised in the direction that must actually work.
    """

    def test_f64_patch_currency_code_changes_reporting_currency_code_only(
        self, client, migrated_database
    ):
        with _committed(migrated_database) as c:
            store_id = _create_store_db(
                c,
                "F64 Currency Update Store",
                timezone="America/Chicago",
                reporting_currency_code="USD",
            )
            owner = _create_user(c, role="owner", password="f64-owner-pw")
        token = _sign_in(client, owner, with_mfa=True)

        r = _patch_store(client, token, store_id, {"currency_code": "EUR"})
        assert r.status_code == 200, r.text
        assert r.json()["data"]["currency_code"] == "EUR"
        assert r.json()["data"]["timezone"] == "America/Chicago"

        row = _fetch_store_row(migrated_database, store_id)
        assert row["reporting_currency_code"] == "EUR"
        assert row["timezone"] == "America/Chicago"


class TestF65RoleCheckedBeforeTheReplayPreCheck:
    """F-65 (US-003 audit). Every AC-03 creation test uses a fresh
    ``operation_id``, so moving ``require_can_create_store`` below the
    F-55 pre-check would survive the suite -- and would let a non-Owner
    presenting an ``operation_id`` already spent on a ``store_created``
    get 200 ``replayed: true`` instead of 403, which falsifies AC-03's own
    sentence for exactly that caller.
    """

    def test_f65_manager_presenting_a_spent_create_operation_id_still_gets_403(
        self, client, migrated_database
    ):
        with _committed(migrated_database) as c:
            owner = _create_user(c, role="owner", password="f65-owner-pw")
            manager = _create_user(c, role="manager", password="f65-manager-pw")
        owner_token = _sign_in(client, owner, with_mfa=True)
        manager_token = _sign_in(client, manager, with_mfa=False)

        op_id = _idempotency_key()
        created = _post_store(
            client, owner_token, _store_payload(name="F65 Owner Created"), operation_id=op_id
        )
        assert created.status_code == 201, created.text

        attempt = _post_store(
            client,
            manager_token,
            _store_payload(name="F65 Manager Should Not Replay"),
            operation_id=op_id,
        )
        assert attempt.status_code == 403, attempt.text
        assert attempt.status_code != 200
