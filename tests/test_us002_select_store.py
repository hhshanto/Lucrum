"""US-002a — Select an authorized store (server half). Module: Access/Stores.

"Given several assigned stores, when opening the selector, then only
authorized stores are offered." (AC-01)
"Given an unassigned store identifier, when requesting its records directly
through an API or deep link, then no store data is disclosed." (AC-02)

AC-03 and AC-04 (persisting the current selection, surfacing it in the UI
shell) are explicitly out of scope here -- they are UI-shell criteria for
US-002b and are blocked on an Owner decision about where the current store
selection is persisted. Pinning them now would invent a rule nobody made.

Conventions carried over from ``test_us001_sign_in.py``:

* database tests run inside the ``conn`` fixture's rolled-back transaction;
* HTTP tests need rows the API's own pooled connection can see, so they are
  seeded on a second, committed connection (``_committed``);
* imports of ``src.*`` happen inside each test, not at module scope, so a
  genuinely missing module reports as a single failing test rather than a
  collection error that hides every other assertion in the file.

Real sign-in (``POST /auth/session``) is used wherever the test is about
scope *resolution* -- ``main.request_scope`` is never overridden here. That
override exists in ``test_api.py``'s ``owner_client`` fixture for tests about
something else entirely; using it here would bypass the very code path these
tests exist to pin.
"""

from __future__ import annotations

import uuid

import psycopg
import pytest
import pyotp
from contextlib import contextmanager
from fastapi.testclient import TestClient
from psycopg.rows import dict_row

from src import main

# Same RFC 4648 base32 test vector as test_us001_sign_in.py. MFA enrolment is
# out of scope here too; the secret is seeded directly.
TOTP_TEST_SECRET = "JBSWY3DPEHPK3PXP"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _new_email(label: str) -> str:
    return f"us002-{label}-{uuid.uuid4().hex}@example.test"


def _totp_code(secret: str = TOTP_TEST_SECRET) -> str:
    return pyotp.TOTP(secret).now()


def _create_user(c, *, role, password, mfa_enabled=None, mfa_secret=None):
    """Seed a user, committing-connection style. Mirrors test_us001_sign_in."""
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
    return {"id": row["id"], "email": email, "password": password, "role": role}


def _create_store(c, name, *, is_active=True):
    row = c.execute(
        """
        insert into stores (name, walmart_store_key, timezone,
                            reporting_currency_code, managed_from_year,
                            managed_from_month, is_active)
        values (%s, %s, 'America/New_York', 'USD', 2026, 7, %s)
        returning id
        """,
        (name, f"US002-{uuid.uuid4().hex[:12]}", is_active),
    ).fetchone()
    return row["id"]


def _assign(c, user_id, store_id):
    c.execute(
        "insert into user_store_assignments (user_id, store_id) values (%s, %s)",
        (user_id, store_id),
    )


@contextmanager
def _committed(url):
    """A connection whose work is visible to the API's own pool."""
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


@pytest.fixture
def client(migrated_database):
    with TestClient(main.app) as c:
        yield c


# ---------------------------------------------------------------------------
# AC-01 -- only authorized stores are offered
# ---------------------------------------------------------------------------


class TestAC01OnlyAuthorizedStoresAreOffered:
    def test_ac01_selector_offers_only_assigned_stores(self, client, migrated_database):
        """A Manager assigned to A and B, not C, must see exactly {A, B} from
        GET /stores. Asserted as a set of ids, not a count: a count assertion
        of 2 is satisfied just as well by the wrong two stores coming back.
        """
        with _committed(migrated_database) as c:
            store_a = _create_store(c, "AC01 Store A")
            store_b = _create_store(c, "AC01 Store B")
            store_c = _create_store(c, "AC01 Store C")
            manager = _create_user(c, role="manager", password="ac01-manager-pw")
            _assign(c, manager["id"], store_a)
            _assign(c, manager["id"], store_b)

            # F-23: a *different* user holds the only assignment to store_c.
            # Without this, a scope resolver that dropped `where user_id = %s`
            # entirely (returning every assignment row in the table) would
            # still pass against a fresh database, because the only
            # assignment rows present would happen to be the subject's own.
            # This decoy is what forces the mutation to surface regardless of
            # what earlier tests or earlier runs left committed.
            decoy = _create_user(c, role="manager", password="ac01-decoy-pw")
            _assign(c, decoy["id"], store_c)

        token = _sign_in(client, manager, with_mfa=False)

        body = client.get("/stores", headers=_auth(token)).json()
        returned_ids = {row["id"] for row in body["data"]}

        assert returned_ids == {str(store_a), str(store_b)}
        assert str(store_c) not in returned_ids

    def test_ac01_admin_is_scoped_by_assignment_not_by_role(self, client, migrated_database):
        """The schema scopes Admins by assignment exactly like Managers
        (database-schema.md:276); only the Owner has implicit access to
        every store. An implementation that special-cases 'admin' to mean
        'sees everything' is the specific bug this pins -- so the Admin here
        is assigned to exactly one of three stores, and the other two,
        including the one belonging to a different owner-only reach, must be
        absent.
        """
        with _committed(migrated_database) as c:
            store_a = _create_store(c, "AC01 Admin Store A")
            store_b = _create_store(c, "AC01 Admin Store B")
            store_c = _create_store(c, "AC01 Admin Store C")
            admin = _create_user(c, role="admin", password="ac01-admin-pw")
            _assign(c, admin["id"], store_a)

            # F-23: store_b and store_c are each held by a different user, not
            # merely unassigned to anyone. A fresh database with no prior
            # committed assignments would otherwise let a scope resolver that
            # forgot `where user_id = %s` pass by accident, because there
            # would be no *other* assignment row for it to wrongly return.
            decoy = _create_user(c, role="manager", password="ac01-admin-decoy-pw")
            _assign(c, decoy["id"], store_b)
            _assign(c, decoy["id"], store_c)

        token = _sign_in(client, admin, with_mfa=True)

        body = client.get("/stores", headers=_auth(token)).json()
        returned_ids = {row["id"] for row in body["data"]}

        assert returned_ids == {str(store_a)}
        assert str(store_b) not in returned_ids
        assert str(store_c) not in returned_ids

    def test_ac01_owner_is_offered_every_store(self, client, migrated_database):
        """A user with role 'owner' and *zero* rows in
        user_store_assignments must still be offered every store. This pins
        that the Owner's access is implicit and does not depend on any
        assignment row existing -- a scope resolver that forgot the special
        case would show this Owner nothing at all.
        """
        with _committed(migrated_database) as c:
            store_a = _create_store(c, "AC01 Owner Store A")
            store_b = _create_store(c, "AC01 Owner Store B")
            store_c = _create_store(c, "AC01 Owner Store C")
            owner = _create_user(c, role="owner", password="ac01-owner-pw")
            # Deliberately no _assign() calls at all.

        token = _sign_in(client, owner, with_mfa=True)

        body = client.get("/stores", headers=_auth(token)).json()
        returned_ids = {row["id"] for row in body["data"]}

        # Not equality: the test database is shared and disposable (per
        # conftest.py's committed_store_id docstring) and accumulates stores
        # committed by earlier tests and earlier runs, which the Owner must
        # also see -- that is the point being pinned. A subset check still
        # fails if even one of these three specific stores is missing, which
        # is the only way "implicit access, no assignment row required"
        # could break.
        assert {str(store_a), str(store_b), str(store_c)}.issubset(returned_ids)

    def test_ac01_selector_query_layer_requires_a_scope(self, conn):
        """AGENTS.md: scope is a required argument, not an optional filter.
        Calling list_stores without one must be a TypeError at the call
        site -- a forgotten scope must fail loudly in code review / CI, not
        silently return every row.
        """
        from src.stores import repository

        with pytest.raises(TypeError):
            repository.list_stores(conn)  # missing the required `scope` arg


# ---------------------------------------------------------------------------
# AC-01 / B-7 -- is_active is not a scope or visibility filter
#
# Owner decision (pending amendment to access-v1, after
# docs/walmart-user-stories.md:109): an inactive but assigned store IS
# offered in the selector. is_active governs nothing about scope or
# visibility -- only (separately, and NOT settled here) whether an
# inactive store accepts writes.
#
# GAP: whether an inactive store accepts writes (imports, cost edits, notes,
# close) is explicitly not decided by this ruling and is not tested here.
# ---------------------------------------------------------------------------


class TestAC01B7InactiveIsNotAScopeFilter:
    def test_ac01_b7_inactive_but_assigned_store_is_offered_to_a_manager(
        self, client, migrated_database
    ):
        """A Manager assigned to an active store A and an inactive store D
        must see both from GET /stores. Asserted as exact set equality: the
        current repository.list_stores appends `and is_active` unless told
        otherwise, so D is silently dropped today -- this must fail against
        that code.
        """
        with _committed(migrated_database) as c:
            store_a = _create_store(c, "B7 Manager Store A (active)", is_active=True)
            store_d = _create_store(c, "B7 Manager Store D (inactive)", is_active=False)
            manager = _create_user(c, role="manager", password="b7-manager-pw")
            _assign(c, manager["id"], store_a)
            _assign(c, manager["id"], store_d)

            # F-23: a decoy holds the only assignment to a third store, so a
            # scope resolver that dropped `where user_id = %s` would still be
            # caught even on a database with no other prior committed rows.
            decoy = _create_user(c, role="manager", password="b7-manager-decoy-pw")
            decoy_store = _create_store(c, "B7 Manager Decoy Store", is_active=True)
            _assign(c, decoy["id"], decoy_store)

        token = _sign_in(client, manager, with_mfa=False)

        body = client.get("/stores", headers=_auth(token)).json()
        returned_ids = {row["id"] for row in body["data"]}

        assert returned_ids == {str(store_a), str(store_d)}

    def test_ac01_b7_owner_is_offered_inactive_stores_too(self, client, migrated_database):
        """An Owner (role 'owner', zero assignment rows) must be offered an
        inactive store exactly as they are offered any other store. Subset,
        not equality -- the test database is shared and disposable (see
        test_ac01_owner_is_offered_every_store) -- but the specific inactive
        store id created here must be present.
        """
        with _committed(migrated_database) as c:
            inactive_store = _create_store(
                c, "B7 Owner Inactive Store", is_active=False
            )
            owner = _create_user(c, role="owner", password="b7-owner-pw")
            # Deliberately no _assign() calls: Owner access is implicit.

        token = _sign_in(client, owner, with_mfa=True)

        body = client.get("/stores", headers=_auth(token)).json()
        returned_ids = {row["id"] for row in body["data"]}

        assert str(inactive_store) in returned_ids

    def test_ac01_b7_list_and_detail_agree_for_an_inactive_assigned_store(
        self, client, migrated_database
    ):
        """F-21, pinned directly: GET /stores and GET /stores/{id} must agree
        about an inactive assigned store. Today get_store has no is_active
        filter (200, full body) while list_stores excludes it by default --
        an asymmetry a future change could reintroduce in either direction, so
        both paths are asserted here, not just one.
        """
        with _committed(migrated_database) as c:
            store_d = _create_store(c, "B7 Agree Store D (inactive)", is_active=False)
            manager = _create_user(c, role="manager", password="b7-agree-manager-pw")
            _assign(c, manager["id"], store_d)

        token = _sign_in(client, manager, with_mfa=False)
        headers = _auth(token)

        list_body = client.get("/stores", headers=headers).json()
        list_ids = {row["id"] for row in list_body["data"]}
        assert str(store_d) in list_ids

        detail = client.get(f"/stores/{store_d}", headers=headers)
        assert detail.status_code == 200
        assert detail.json()["data"]["id"] == str(store_d)

    def test_ac01_b7_inactive_does_not_widen_scope(self, client, migrated_database):
        """The ruling cuts both ways: is_active is not a scope filter, so it
        must not grant access either. A Manager assigned only to active store
        A, and NOT assigned to inactive store E, must still get a 404 for E
        on the detail route, and E must be absent from the list -- "inactive
        is not filtered" must not be misread as "inactive stores are exempt
        from scoping".
        """
        with _committed(migrated_database) as c:
            store_a = _create_store(c, "B7 Widen Store A (active, assigned)", is_active=True)
            store_e = _create_store(
                c, "B7 Widen Store E (inactive, NOT assigned)", is_active=False
            )
            manager = _create_user(c, role="manager", password="b7-widen-manager-pw")
            _assign(c, manager["id"], store_a)

            # F-23 decoy: someone else holds the only assignment to store_e,
            # so an unscoped "return every row" bug cannot pass by accident.
            decoy = _create_user(c, role="manager", password="b7-widen-decoy-pw")
            _assign(c, decoy["id"], store_e)

        token = _sign_in(client, manager, with_mfa=False)
        headers = _auth(token)

        detail = client.get(f"/stores/{store_e}", headers=headers)
        assert detail.status_code == 404

        list_body = client.get("/stores", headers=headers).json()
        list_ids = {row["id"] for row in list_body["data"]}
        assert str(store_e) not in list_ids

    def test_ac01_b7_repository_lists_inactive_assigned_stores_by_default(self, conn):
        """Query layer, not HTTP. repository.list_stores(conn, scope) called
        with NO keyword arguments at all must include an inactive in-scope
        store. The default is what is under test: a fix that merely changes
        what one call site passes (e.g. the /stores route starts passing
        include_inactive=True) would leave every other, or future, call site
        that omits the flag still silently excluding inactive stores.
        """
        from src.scoping import StoreScope
        from src.stores import repository

        active_row = conn.execute(
            """
            insert into stores (name, walmart_store_key, timezone,
                                reporting_currency_code, managed_from_year,
                                managed_from_month, is_active)
            values (%s, %s, 'America/New_York', 'USD', 2026, 7, true)
            returning id
            """,
            (
                f"B7 Repo Active {uuid.uuid4().hex[:8]}",
                f"US002-B7A-{uuid.uuid4().hex[:12]}",
            ),
        ).fetchone()
        inactive_row = conn.execute(
            """
            insert into stores (name, walmart_store_key, timezone,
                                reporting_currency_code, managed_from_year,
                                managed_from_month, is_active)
            values (%s, %s, 'America/New_York', 'USD', 2026, 7, false)
            returning id
            """,
            (
                f"B7 Repo Inactive {uuid.uuid4().hex[:8]}",
                f"US002-B7I-{uuid.uuid4().hex[:12]}",
            ),
        ).fetchone()

        scope = StoreScope.assigned(
            actor_id=uuid.uuid4(),
            store_ids=[active_row["id"], inactive_row["id"]],
        )

        # No keyword arguments at all -- the default is the thing under test.
        rows = repository.list_stores(conn, scope)
        returned_ids = {row["id"] for row in rows}

        assert inactive_row["id"] in returned_ids
        assert active_row["id"] in returned_ids


# ---------------------------------------------------------------------------
# AC-02 -- no disclosure on the read path
# ---------------------------------------------------------------------------


class TestAC02NoDisclosureOnRead:
    def test_ac02_unassigned_and_nonexistent_are_identical_on_read(
        self, client, migrated_database
    ):
        """BT-08 on the *read* path -- the one AC-02 actually names
        ("deep link"). A Manager assigned only to store A requests an
        existing-but-unassigned store B and a store id that does not exist
        at all. Both must come back with identical status, identical parsed
        body, and identical headers -- not merely both-404, and not merely a
        body-level match that a differing header could still undercut (F-24).
        """
        with _committed(migrated_database) as c:
            store_a = _create_store(c, "AC02 Store A")
            store_b = _create_store(c, "AC02 Store B (unassigned)")
            manager = _create_user(c, role="manager", password="ac02-manager-pw")
            _assign(c, manager["id"], store_a)

        token = _sign_in(client, manager, with_mfa=False)
        headers = _auth(token)

        unassigned = client.get(f"/stores/{store_b}", headers=headers)
        nonexistent = client.get(f"/stores/{uuid.uuid4()}", headers=headers)

        assert unassigned.status_code == nonexistent.status_code
        assert unassigned.json() == nonexistent.json()

        # F-24: BT-08 says "identical status and body", but a header that
        # differs between the two cases (an ETag or Last-Modified present
        # only for the row that actually exists, say) would disclose exactly
        # the distinction AC-02 exists to hide, while leaving both of the
        # assertions above untouched. Compare the header sets directly.
        #
        # `date` is excluded: it is the wall-clock moment each response was
        # generated, not app-controlled, and will differ between these two
        # sequential requests even when nothing else does. No other header is
        # excluded -- in particular `content-length` is deliberately left in:
        # two equal JSON bodies must serialise to equal lengths, and a
        # divergence there is a real leak, not noise.
        excluded_headers = {"date"}
        unassigned_headers = {
            k.lower(): v
            for k, v in unassigned.headers.items()
            if k.lower() not in excluded_headers
        }
        nonexistent_headers = {
            k.lower(): v
            for k, v in nonexistent.headers.items()
            if k.lower() not in excluded_headers
        }
        assert unassigned_headers == nonexistent_headers
        assert unassigned.headers["content-length"] == nonexistent.headers["content-length"]

    def test_ac02_response_does_not_leak_the_store_identifier_or_name(
        self, client, migrated_database
    ):
        """The 404 body (and headers) for an out-of-scope store must contain
        neither its name nor its id anywhere in the raw response text --
        not just absent from the parsed 'data' key.
        """
        distinctive_name = f"Totally Secret Store {uuid.uuid4().hex[:8]}"
        with _committed(migrated_database) as c:
            store_a = _create_store(c, "AC02 Leak Store A")
            store_b = _create_store(c, distinctive_name)
            manager = _create_user(c, role="manager", password="ac02-leak-manager-pw")
            _assign(c, manager["id"], store_a)

        token = _sign_in(client, manager, with_mfa=False)

        response = client.get(f"/stores/{store_b}", headers=_auth(token))

        assert distinctive_name not in response.text
        assert str(store_b) not in response.text
        for header_value in response.headers.values():
            assert distinctive_name not in header_value
            assert str(store_b) not in header_value

    def test_ac02_out_of_scope_read_is_404_never_403(self, client, migrated_database):
        """api-service-design.md:1261 names 'returning STORE_OUT_OF_SCOPE in
        a way that confirms the store exists' as an anti-pattern -- which a
        403 would be, because a 403 (as opposed to a 404) itself discloses
        that a resource is there to be forbidden. The status must be exactly
        404, and explicitly not 403.
        """
        with _committed(migrated_database) as c:
            store_a = _create_store(c, "AC02 403 Store A")
            store_b = _create_store(c, "AC02 403 Store B (unassigned)")
            manager = _create_user(c, role="manager", password="ac02-403-manager-pw")
            _assign(c, manager["id"], store_a)

        token = _sign_in(client, manager, with_mfa=False)

        response = client.get(f"/stores/{store_b}", headers=_auth(token))

        assert response.status_code == 404
        assert response.status_code != 403

    def test_ac02_repository_get_store_raises_out_of_scope_for_both(self, conn):
        """Query layer, not HTTP: repository.get_store raises OutOfScope for
        an existing-but-unassigned store and for a nonexistent id alike, and
        the exception raised for the existing store must not carry the
        store's name anywhere in its representation -- a leak here would
        surface the moment any caller logs the exception instead of
        translating it.
        """
        from src.scoping import OutOfScope, StoreScope
        from src.stores import repository

        distinctive_name = f"Repo Secret Store {uuid.uuid4().hex[:8]}"
        row = conn.execute(
            """
            insert into stores (name, walmart_store_key, timezone,
                                reporting_currency_code, managed_from_year,
                                managed_from_month, is_active)
            values (%s, %s, 'America/New_York', 'USD', 2026, 7, true)
            returning id
            """,
            (distinctive_name, f"US002-REPO-{uuid.uuid4().hex[:12]}"),
        ).fetchone()
        unassigned_store_id = row["id"]

        # A scope that permits nothing at all -- nothing is assigned here.
        empty_scope = StoreScope.assigned(actor_id=uuid.uuid4(), store_ids=[])

        with pytest.raises(OutOfScope) as excinfo_existing:
            repository.get_store(conn, empty_scope, unassigned_store_id)
        assert distinctive_name not in str(excinfo_existing.value)
        assert distinctive_name not in repr(excinfo_existing.value)

        with pytest.raises(OutOfScope):
            repository.get_store(conn, empty_scope, uuid.uuid4())

    def test_ac02_scope_is_applied_in_sql_not_after_the_fetch(self, conn):
        """F-22: a rewrite of repository.get_store that selects by id alone,
        fetches the row, then checks ``scope.permits(...)`` in Python before
        raising OutOfScope passes every *behavioural* assertion in this file
        -- the row was still read before being discarded, which is exactly
        the leak src/stores/repository.py:3-7 names ("waiting for a logging
        statement or an error message to expose it"). AGENTS.md requires the
        scope filter to be composed into the query, not applied afterwards.

        This spies on the connection actually passed to the repository
        function and asserts that the statement sent to Postgres carries the
        scope's own WHERE fragment and its own bound parameters -- not that
        some Python code elsewhere also happens to call ``scope.permits``.
        The spy is local to this test; no production hook is added.
        """
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
            (f"SQL-layer store {uuid.uuid4().hex[:8]}", f"US002-SQL-{uuid.uuid4().hex[:12]}"),
        ).fetchone()
        unassigned_store_id = row["id"]

        # A non-empty, non-trivial scope, so the composed WHERE fragment is
        # distinctive ("id = any(%(scope_store_ids)s)") rather than the
        # degenerate "false" an empty scope would produce -- a fragment that
        # specific could coincidentally appear in unrelated SQL.
        permitted_store_id = uuid.uuid4()
        scope = StoreScope.assigned(actor_id=uuid.uuid4(), store_ids=[permitted_store_id])

        captured: list[tuple[str, object]] = []
        real_execute = conn.execute

        def _spy_execute(query, params=None, **kwargs):
            captured.append((str(query), params))
            return real_execute(query, params, **kwargs)

        conn.execute = _spy_execute
        try:
            with pytest.raises(OutOfScope):
                repository.get_store(conn, scope, unassigned_store_id)
        finally:
            conn.execute = real_execute

        assert captured, "get_store must query the database, not reason in Python alone"

        where_clause, scope_params = scope.sql_filter("id")
        matching = [
            (sql, params) for sql, params in captured if where_clause in sql
        ]
        assert matching, (
            "the scope predicate was never composed into any statement sent "
            f"to the database; statements sent were: {[s for s, _ in captured]!r}"
        )

        # The scope's own parameter must have actually been bound into that
        # statement -- not merely textually present as a coincidence. A
        # Python-side `if not scope.permits(row["id"]): raise` would fail
        # this: the WHERE clause sent would be `id = %(store_id)s` alone,
        # with no trace of `scope_store_ids` anywhere in the params, because
        # the unauthorized row was already fetched by the time the check ran.
        sql, params = matching[0]
        assert params is not None
        for key, value in scope_params.items():
            assert params.get(key) == value

        # F-22, tightened. The assertions above only show that *some*
        # statement carried the scope predicate -- they pass unchanged
        # against a get_store that first issues an unscoped
        # `select ... from stores where id = %(store_id)s`, fetches the
        # out-of-scope row, and *only then* issues a second, scoped
        # statement before raising OutOfScope. That row was still read
        # before being discarded; a second, merely-decorative scoped query
        # does not undo the unscoped read that already happened. So: every
        # statement that touches `stores` must itself carry the scope
        # predicate, and get_store must issue exactly one statement, not two.
        stores_statements = [sql for sql, _ in captured if "stores" in sql]
        assert stores_statements, (
            "no captured statement referenced the stores table at all; "
            f"statements sent were: {[s for s, _ in captured]!r}"
        )
        unscoped_stores_statements = [
            sql for sql in stores_statements if where_clause not in sql
        ]
        assert not unscoped_stores_statements, (
            "a statement touching stores was issued WITHOUT the scope "
            "predicate composed into it -- an unscoped read followed by a "
            "second, scoped one still reads the out-of-scope row before "
            f"discarding it: {unscoped_stores_statements!r}"
        )
        assert len(captured) == 1, (
            "get_store issued more than one statement against the "
            "database; a second, scoped statement does not excuse an "
            "earlier unscoped fetch of the same row -- statements sent "
            f"were: {[s for s, _ in captured]!r}"
        )
