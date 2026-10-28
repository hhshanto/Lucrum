"""US-005a -- Import orders (AC-01, AC-02 only). Module: Orders.

docs/walmart-user-stories.md:782-790, US-005 "Import and refresh orders"
(verbatim, the two criteria this story covers):

    AC-01: Given a supported order payload, when processing it, then store
    identity, source order identifier, SKU, order date, quantity, selling
    amount, currency, and source fulfillment state are preserved.
    AC-02: Given the same source order is processed again, when refreshing,
    then existing order identity is retained without duplicate orders or
    loss of manual costs. Source order identifiers are unique within a
    store, not globally.

**US-005 was split three ways. This file covers AC-01 and AC-02 ONLY.**
AC-03 (US-005b: failed-refresh visibility, staleness) and AC-04 through
AC-08 (US-005c: protected-period re-import, PERIOD_NOT_PROVISIONED,
deferral, posting-period display) are deliberately NOT tested here --
there is no `orders` row anywhere in this file whose `posting_status_code`
or `accounting_month_id` is asserted to a *specific* value, and no test
closes an accounting month or imports across one. Where a row necessarily
has SOME posting_status_code (it is NOT NULL per the schema), this file
reads it only to the extent needed to prove it is not NULL -- never to
pin which of the four values it is.

Nothing in `src/` implements any of this yet: no `orders` table (only
`stores` and `audit_events` exist, per `src/db/migrations/001_baseline.sql`
and `002_access.sql`), no `accounting_months` table, and no
`POST /imports/orders` route. Every test below is expected to fail -- see
each test's own docstring for the specific, expected failure.

-------------------------------------------------------------------------
Spec citations relied on (reproduced from the spec-oracle brief so this
file can be checked against them directly, not re-derived):

* `database-schema.md:429-456` -- the full `orders` column shape. The
  eight AC-01 fields map to: `store_id`, `walmart_order_id`, `sku`,
  `order_date`, `quantity`, `selling_amount`, `currency_code`,
  `fulfillment_status_code` -- all NOT NULL. `order_placed_at_utc` sits
  behind `order_date` (also NOT NULL): the importer receives the raw
  instant and derives `order_date` via the store's timezone
  (`src/clock.py:to_store_date`), never the other way around.
* `database-schema.md:458-468` -- fulfillment is EXACTLY four values:
  `acknowledged`, `shipped_in_transit`, `delivered`, `cancelled`.
  `refunded`, `settlement_pending`, `reconciled` belong to a DIFFERENT
  state dimension (refund activity / reconciliation) and are explicitly
  NOT fulfillment values -- collapsing dimensions is AGENTS.md's
  non-negotiable #1.
* `database-schema.md:1055` / `INVARIANTS.md:49` (DB-12) --
  `orders (store_id, walmart_order_id)` is a *composite*, per-store
  uniqueness constraint. Two different stores may legitimately carry the
  same external order id.
* `database-schema.md:1102-1103` (DB-06) -- `accounting_month_id` is
  nullable, NOT a generated column, and no trigger may derive it from
  `order_date`. `accounting_months` must exist (empty) to satisfy the FK;
  nothing in this story populates it.
* `api-service-design.md:772-783` -- `POST /imports/orders` "upserts
  orders by (store_id, walmart_order_id)". No request/response body shape
  is specified. This file's own reasoned, disclosed assumption (the same
  discipline `GrantManagerRequest` used for US-004's unspecified body):

      POST /imports/orders
      Headers: Authorization, Idempotency-Key (every write in this
               codebase requires one; this is not itself an AC-01/02
               criterion, just the project-wide write pattern)
      Body: {"store_id": "<uuid>", "orders": [ {order fields...} ]}

  A batch of exactly one order is used in every test here, deliberately:
  the *shape* (a list) is kept so a later story's genuine multi-row batch
  does not need a different endpoint, but nothing about multi-row
  behaviour (partial failure, batch visibility -- AC-03/US-005b) is
  tested or assumed. Response body shape is intentionally NOT pinned
  beyond "some indication of success/failure" -- every substantive
  assertion reads the `orders` table directly over a second, committed
  connection, the same `_fetch_*` convention `test_us004_*.py` and
  `test_us002_*.py` already use. Status code IS pinned, following the
  `create_store`/`grant_manager` precedent: 201 the first time a row is
  created, 200 when refreshing an existing one -- disclosed as this
  file's own choice, not derived from any document.
* `walmart-user-stories.md:674` -- capability matrix, "Import orders":
  Owner Yes / Admin Yes / Manager Yes, store-scoped. This is an explicit
  row, not a gap: a Manager assigned to a store must be able to import
  into it, and the only authorization control at all is store *scope*
  (not role) -- there is no role-based rejection test in this file for
  that reason, unlike US-003's Owner-only creation or US-004's
  Owner-only assignment.
* US-002 AC-02 / BT-08 posture, reapplied here: a store outside the
  caller's scope must be indistinguishable from one that does not exist
  (404, never 403) -- the same discipline `test_us002_select_store.py`
  already proves for reads, now pinned for this write too.
* **B-9, interim** (`PROGRESS.md`): whether an inactive store accepts
  writes at all is an open Owner question. One test here pins the
  narrowest, explicitly-labelled-interim answer: an import into an
  inactive store is refused, visibly -- never silently accepted, never
  silently skipped. If the Owner later rules the other way, THAT test is
  the one to revisit, not AC-01/AC-02 or the capability matrix.
* **Owner ruling, not re-litigated**: "supported order payload" names no
  "unsupported payload" rejection branch (C-02 governs settlement
  reports, not orders -- `walmart-user-stories.md:1175`). A payload
  missing a required field fails as an ordinary missing-field 422,
  nothing more; this file includes exactly one such test, framed that
  way, and deliberately does NOT add a bespoke "unsupported payload"
  error path.
* Month provisioning (B-17, unowned gap) is not touched: no test
  provisions, expects, or asserts anything about an `accounting_months`
  row existing for any order's date.

-------------------------------------------------------------------------
Conventions carried over from `test_us004_manage_store_assignments.py` /
`test_us002_select_store.py`: `src.*` imports happen inside tests that
need them (none do here at HTTP level -- only the DB-level schema class
imports `psycopg.errors` at module scope, which already exists); HTTP
tests seed rows on a second, committed connection the API's own pool can
see; a decoy store/user is present wherever scoping is being asserted, so
the assertion still means something on a fresh database (F-23); 403 is
never confused with 404, in either direction; money is asserted as
`Decimal`, never as a float literal (`src/money.py`); schema-level (DDL)
tests use the rolled-back `conn` fixture directly, same as
`test_audit_and_migrations.py`.
"""

from __future__ import annotations

import uuid
from contextlib import contextmanager
from datetime import date
from decimal import Decimal

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


def _new_order_id(label: str) -> str:
    return f"US005A-{label}-{uuid.uuid4().hex[:12]}"


def _totp_code(secret: str = TOTP_TEST_SECRET) -> str:
    return pyotp.TOTP(secret).now()


def _create_user(c, *, role, password, mfa_enabled=None, mfa_secret=None):
    """Seed a user, committing-connection style. Mirrors test_us004/002a."""
    from src.access import passwords

    if mfa_enabled is None:
        mfa_enabled = role in ("owner", "admin")
    if mfa_enabled and mfa_secret is None:
        mfa_secret = TOTP_TEST_SECRET

    email = f"us005a-{role}-{uuid.uuid4().hex}@example.test"
    row = c.execute(
        """
        insert into users (email, role, password_hash, mfa_enabled, mfa_secret)
        values (%s, %s, %s, %s, %s)
        returning id
        """,
        (email, role, passwords.hash_password(password), mfa_enabled, mfa_secret),
    ).fetchone()
    return {"id": row["id"], "email": email, "password": password, "role": role}


def _create_store_db(
    c,
    name,
    *,
    timezone="America/New_York",
    reporting_currency_code="USD",
    managed_from_year=2026,
    managed_from_month=7,
    is_active=True,
):
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
            f"US005A-{uuid.uuid4().hex[:12]}",
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


def _idempotency_key() -> str:
    return str(uuid.uuid4())


def _order_payload(**overrides) -> dict:
    """A complete, valid order payload carrying every AC-01 field plus the
    two nullable extras (`walmart_purchase_order_id`, `item_description`).
    Every test either uses this unmodified or overrides exactly the field
    it is pinning, so a missing default never silently changes what a test
    not focused on that field actually exercises.
    """
    payload = {
        "walmart_order_id": _new_order_id("default"),
        "walmart_purchase_order_id": None,
        "sku": "SKU-DEFAULT-100",
        "item_description": "Test item",
        "order_placed_at_utc": "2026-07-15T14:30:00Z",
        "quantity": 1,
        "selling_amount": "9.99",
        "currency_code": "USD",
        "fulfillment_status_code": "delivered",
    }
    payload.update(overrides)
    return payload


@pytest.fixture
def client(migrated_database):
    with TestClient(main.app) as c:
        yield c


def _import(client, token, store_id, orders: list[dict], *, operation_id=None):
    headers = {**_auth(token), "Idempotency-Key": operation_id or _idempotency_key()}
    return client.post(
        "/imports/orders",
        json={"store_id": str(store_id), "orders": orders},
        headers=headers,
    )


def _fetch_order_row(url: str, store_id, walmart_order_id: str) -> dict | None:
    with psycopg.connect(url, row_factory=dict_row) as c:
        return c.execute(
            "select * from orders where store_id = %s and walmart_order_id = %s",
            (str(store_id), walmart_order_id),
        ).fetchone()


def _count_orders(url: str, store_id, walmart_order_id: str) -> int:
    with psycopg.connect(url) as c:
        return c.execute(
            "select count(*) from orders where store_id = %s and walmart_order_id = %s",
            (str(store_id), walmart_order_id),
        ).fetchone()[0]


def _fetch_audit_rows(url: str, operation_id: str) -> list[dict]:
    """Same convention as `test_us003_create_store.py`'s own
    `_fetch_audit_rows` -- a second, committed connection reading the
    `audit_events` ledger directly by `operation_id`.
    """
    with psycopg.connect(url, row_factory=dict_row) as c:
        return c.execute(
            "select * from audit_events where operation_id = %s", (operation_id,)
        ).fetchall()


# ---------------------------------------------------------------------------
# Schema-level invariants -- direct SQL against `orders`, independent of any
# import endpoint or service. These fail right now with
# `psycopg.errors.UndefinedTable: relation "orders" does not exist` (or, for
# the information_schema-based assertions, a plain `AssertionError` reporting
# the table/column is missing), because no migration creates the table yet.
# ---------------------------------------------------------------------------


class TestOrdersSchemaInvariants:
    """DDL/CATALOG-shaped tests, using the rolled-back `conn` fixture
    directly -- the same pattern `test_audit_and_migrations.py`'s
    `TestBaselineSchemaConventions` already uses for `stores`.
    """

    def _insert_order(self, conn, *, store_id, walmart_order_id, **overrides):
        fields = {
            "sku": "SKU-SCHEMA",
            "order_placed_at_utc": "2026-07-15 10:00:00+00",
            "order_date": "2026-07-15",
            "scope_status_code": "managed",
            "posting_status_code": "unpostable_no_period",
            "quantity": 1,
            "selling_amount": "10.00",
            "currency_code": "USD",
            "fulfillment_status_code": "delivered",
            "last_imported_at": "2026-07-15 10:00:00+00",
        }
        fields.update(overrides)
        columns = ["store_id", "walmart_order_id", *fields.keys()]
        values = [store_id, walmart_order_id, *fields.values()]
        placeholders = ", ".join(["%s"] * len(columns))
        return conn.execute(
            f"insert into orders ({', '.join(columns)}) values ({placeholders}) "
            f"returning id, created_at",
            values,
        ).fetchone()

    def test_orders_table_has_the_documented_not_null_columns(self, conn):
        """AC-01's eight fields, plus the instant behind order_date, must
        all exist and be NOT NULL. Mutation this catches: any one of them
        declared nullable (a row could then "preserve" a field as NULL,
        silently satisfying no test that only checks presence of the
        column) -- or the column renamed/dropped outright.
        """
        rows = conn.execute(
            "select column_name, is_nullable from information_schema.columns "
            "where table_name = 'orders'"
        ).fetchall()
        assert rows, "orders table does not exist yet"
        by_name = {r["column_name"]: r["is_nullable"] for r in rows}
        for col in [
            "store_id",
            "walmart_order_id",
            "sku",
            "order_placed_at_utc",
            "order_date",
            "quantity",
            "selling_amount",
            "currency_code",
            "fulfillment_status_code",
        ]:
            assert col in by_name, f"missing column: {col}"
            assert by_name[col] == "NO", f"{col} must be NOT NULL (AC-01)"

        # DB-06: accounting_month_id must exist and be NULLABLE -- posting
        # period arrives later (US-005c) and must never be guessed.
        assert by_name.get("accounting_month_id") == "YES"

    def test_db04_currency_code_has_no_database_default(self, conn):
        """BR-12/DB-04. Mutation this catches: `currency_code char(3)
        default 'USD'` -- a column default is exactly how a currency gets
        silently assumed instead of rejected when absent.
        """
        row = conn.execute(
            "select column_default from information_schema.columns "
            "where table_name = 'orders' and column_name = 'currency_code'"
        ).fetchone()
        assert row is not None, "orders.currency_code does not exist yet"
        assert row["column_default"] is None

    def test_db11_currency_code_rejects_a_malformed_code(self, conn, store_id):
        """DB-11: three-letter, uppercase, nothing else. Mutation this
        catches: a missing or loosened CHECK constraint (e.g. no
        constraint at all, or one that accepts lowercase).
        """
        for bad in ["usd", "US", "USDD", "123"]:
            with pytest.raises(psycopg.errors.CheckViolation):
                with conn.transaction():
                    self._insert_order(
                        conn,
                        store_id=store_id,
                        walmart_order_id=f"DB11-{bad}",
                        currency_code=bad,
                    )

    def test_db01_fulfillment_status_accepts_each_of_the_four_documented_values(
        self, conn, store_id
    ):
        """Mutation this catches: a CHECK constraint that is too narrow
        (rejects a value it must accept) -- the complement of the next
        test, which catches one that is too wide.
        """
        for code in ["acknowledged", "shipped_in_transit", "delivered", "cancelled"]:
            row = self._insert_order(
                conn,
                store_id=store_id,
                walmart_order_id=f"DB01-OK-{code}",
                fulfillment_status_code=code,
            )
            assert row is not None

    def test_db01_fulfillment_status_rejects_reconciliation_and_refund_values(
        self, conn, store_id
    ):
        """database-schema.md:468 -- `refunded`, `settlement_pending`, and
        `reconciled` belong to a DIFFERENT state dimension and are
        explicitly NOT fulfillment values; `nonsense` is the ordinary
        out-of-enumeration case. Mutation this catches: no CHECK
        constraint at all (AGENTS.md non-negotiable #1 -- no component
        takes a bare `status` prop that happily accepts any string).
        """
        for bad in ["refunded", "settlement_pending", "reconciled", "nonsense"]:
            with pytest.raises(psycopg.errors.CheckViolation):
                with conn.transaction():
                    self._insert_order(
                        conn,
                        store_id=store_id,
                        walmart_order_id=f"DB01-BAD-{bad}",
                        fulfillment_status_code=bad,
                    )

    def test_db06_accounting_month_id_is_nullable_and_not_a_generated_column(
        self, conn, store_id
    ):
        """Mutation this catches: `accounting_month_id` declared `not
        null`, or as `generated always as (...) stored` deriving it from
        `order_date` -- either would destroy deferral (US-005c) before
        that story is even reached, because the two columns could no
        longer be set independently.
        """
        row = self._insert_order(conn, store_id=store_id, walmart_order_id="DB06-NULL")
        assert row is not None  # NULL accounting_month_id is accepted

        col = conn.execute(
            "select is_generated, generation_expression from information_schema.columns "
            "where table_name = 'orders' and column_name = 'accounting_month_id'"
        ).fetchone()
        assert col is not None, "orders.accounting_month_id does not exist yet"
        assert col["is_generated"] == "NEVER"
        assert col["generation_expression"] is None

        triggers = conn.execute(
            "select pg_get_triggerdef(oid) as def from pg_trigger "
            "where tgrelid = 'orders'::regclass and not tgisinternal"
        ).fetchall()
        assert not any("accounting_month_id" in t["def"] for t in triggers), (
            "a user-defined trigger references accounting_month_id -- DB-06 "
            "forbids deriving it from order_date"
        )

    def test_db06_accounting_month_id_is_a_real_foreign_key(self, conn, store_id):
        """Mutation this catches: `accounting_month_id` declared as a bare
        `uuid` with no `references accounting_months(id)` -- an
        unconstrained column would silently accept any value at all,
        including one that names no real month.
        """
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            with conn.transaction():
                self._insert_order(
                    conn,
                    store_id=store_id,
                    walmart_order_id="DB06-FK",
                    accounting_month_id=str(uuid.uuid4()),
                )

    def test_db08_no_order_status_column_exists_anywhere(self, conn):
        """SC-01/DB-08: the identifier `order_status` must appear in no
        table at all. Written so it fails NOW for the right reason (the
        table doesn't exist yet) rather than passing vacuously -- a bare
        "no such column anywhere" query would trivially pass on an empty
        database and prove nothing.
        """
        exists = conn.execute("select to_regclass('public.orders')").fetchone()["to_regclass"]
        assert exists is not None, "orders table does not exist yet"
        rows = conn.execute(
            "select table_name from information_schema.columns "
            "where column_name = 'order_status'"
        ).fetchall()
        assert rows == [], f"a column named order_status exists: {rows!r}"

    def test_db12_same_external_id_twice_in_one_store_is_rejected(self, conn, store_id):
        """DB-12, first half. Mutation this catches: the per-store
        uniqueness constraint dropped entirely -- this insert would then
        succeed instead of raising.
        """
        self._insert_order(conn, store_id=store_id, walmart_order_id="DB12-DUP")
        with pytest.raises(psycopg.errors.UniqueViolation):
            self._insert_order(conn, store_id=store_id, walmart_order_id="DB12-DUP")

    def test_db12_same_external_id_in_two_different_stores_is_allowed(
        self, conn, store_id
    ):
        """DB-12, second half -- the complementary mutation. If the
        constraint were made GLOBAL (`unique (walmart_order_id)` alone,
        dropping `store_id`), this insert would wrongly raise
        UniqueViolation instead of succeeding. Together with the test
        above, a constraint that is either missing OR too broad is
        caught; neither test alone would catch both.
        """
        other_store = conn.execute(
            """
            insert into stores (name, walmart_store_key, timezone,
                                reporting_currency_code, managed_from_year,
                                managed_from_month)
            values ('DB12 Other Store', %s, 'America/New_York', 'USD', 2026, 7)
            returning id
            """,
            (f"DB12-OTHER-{uuid.uuid4().hex[:12]}",),
        ).fetchone()["id"]

        self._insert_order(conn, store_id=store_id, walmart_order_id="DB12-SHARED")
        row = self._insert_order(
            conn, store_id=other_store, walmart_order_id="DB12-SHARED"
        )
        assert row is not None


# ---------------------------------------------------------------------------
# AC-01 -- fields preserved on import
# ---------------------------------------------------------------------------


class TestAC01FieldsArePreservedOnImport:
    def test_ac01_all_eight_named_fields_are_preserved_exactly(
        self, client, migrated_database
    ):
        """The whole criterion in one pass. A decoy store carries the SAME
        external order id, so "store identity" is pinned specifically:
        an importer that forgot to scope the write by store_id at all (or
        collapsed two stores' rows into one) would still pass a version of
        this test that checked only one store.

        Mutation this catches: dropping any one of the eight columns from
        the insert/upsert, or crossing two source fields (e.g. storing
        quantity into selling_amount).

        Expected failure right now: 404 Not Found (no POST /imports/orders
        route exists), so `r_main.status_code == 201` fails first.
        """
        with _committed(migrated_database) as c:
            store_id = str(_create_store_db(c, "AC01 Import Store"))
            decoy_store_id = str(_create_store_db(c, "AC01 Import Decoy Store"))
            owner = _create_user(c, role="owner", password="ac01-import-owner-pw")
        token = _sign_in(client, owner, with_mfa=True)

        order_id = _new_order_id("ac01-main")
        payload = _order_payload(
            walmart_order_id=order_id,
            sku="SKU-AC01-100",
            order_placed_at_utc="2026-07-15T14:30:00Z",
            quantity=3,
            selling_amount="44.97",
            currency_code="USD",
            fulfillment_status_code="shipped_in_transit",
        )
        decoy_payload = _order_payload(walmart_order_id=order_id, sku="SKU-DECOY")

        r_main = _import(client, token, store_id, [payload])
        assert r_main.status_code == 201, r_main.text
        r_decoy = _import(client, token, decoy_store_id, [decoy_payload])
        assert r_decoy.status_code == 201, r_decoy.text

        row = _fetch_order_row(migrated_database, store_id, order_id)
        assert row is not None
        assert str(row["store_id"]) == store_id  # store identity
        assert row["walmart_order_id"] == order_id  # source order identifier
        assert row["sku"] == "SKU-AC01-100"  # SKU
        assert row["order_date"] == date(2026, 7, 15)  # order date
        assert row["quantity"] == 3  # quantity
        assert row["selling_amount"] == Decimal("44.97")  # selling amount, exact
        assert row["currency_code"] == "USD"  # currency
        assert row["fulfillment_status_code"] == "shipped_in_transit"  # fulfillment

        decoy_row = _fetch_order_row(migrated_database, decoy_store_id, order_id)
        assert decoy_row is not None
        assert decoy_row["sku"] == "SKU-DECOY"
        assert decoy_row["id"] != row["id"]

    def test_ac01_order_date_crosses_midnight_via_the_stores_timezone_not_utc(
        self, client, migrated_database
    ):
        """Mutation this catches: an importer that takes the UTC calendar
        date of `order_placed_at_utc` verbatim instead of converting
        through the store's own timezone (`src/clock.py:to_store_date`).
        America/New_York is UTC-4 in July (EDT): an order placed at
        2026-07-15T02:30:00Z is 2026-07-14T22:30:00-04:00 local --
        `order_date` must be 2026-07-14, never 2026-07-15.
        """
        with _committed(migrated_database) as c:
            store_id = str(
                _create_store_db(
                    c, "AC01 Timezone Store", timezone="America/New_York"
                )
            )
            owner = _create_user(c, role="owner", password="ac01-tz-owner-pw")
        token = _sign_in(client, owner, with_mfa=True)

        order_id = _new_order_id("ac01-tz")
        payload = _order_payload(
            walmart_order_id=order_id, order_placed_at_utc="2026-07-15T02:30:00Z"
        )

        r = _import(client, token, store_id, [payload])
        assert r.status_code == 201, r.text

        row = _fetch_order_row(migrated_database, store_id, order_id)
        assert row["order_date"] == date(2026, 7, 14)
        assert row["order_date"] != date(2026, 7, 15)

    def test_ac01_selling_amount_is_stored_as_exact_decimal_not_a_rounded_float(
        self, client, migrated_database
    ):
        """SC-04/profit-v1: a value whose nearest binary float is not exact
        must still round-trip exactly. Mutation this catches: the amount
        passed through a Python `float` anywhere on the way into the
        database (e.g. `float(payload["selling_amount"])`), which would
        corrupt a value like this one before it reached the column.
        """
        with _committed(migrated_database) as c:
            store_id = str(_create_store_db(c, "AC01 Decimal Store"))
            owner = _create_user(c, role="owner", password="ac01-decimal-owner-pw")
        token = _sign_in(client, owner, with_mfa=True)

        order_id = _new_order_id("ac01-decimal")
        payload = _order_payload(
            walmart_order_id=order_id, quantity=1, selling_amount="19.10"
        )
        r = _import(client, token, store_id, [payload])
        assert r.status_code == 201, r.text

        row = _fetch_order_row(migrated_database, store_id, order_id)
        assert isinstance(row["selling_amount"], Decimal)
        assert row["selling_amount"] == Decimal("19.10")
        assert str(row["selling_amount"]) == "19.10"

    @pytest.mark.parametrize(
        "code", ["acknowledged", "shipped_in_transit", "delivered", "cancelled"]
    )
    def test_ac01_each_of_the_four_fulfillment_values_is_accepted(
        self, client, migrated_database, code
    ):
        """Mutation this catches: an importer-level allowlist narrower than
        the documented four values (e.g. one that forgot `cancelled`).
        """
        with _committed(migrated_database) as c:
            store_id = str(_create_store_db(c, f"AC01 Fulfillment Store {code}"))
            owner = _create_user(c, role="owner", password=f"ac01-fulfil-{code}-pw")
        token = _sign_in(client, owner, with_mfa=True)

        order_id = _new_order_id(f"ac01-fulfil-{code}")
        payload = _order_payload(walmart_order_id=order_id, fulfillment_status_code=code)

        r = _import(client, token, store_id, [payload])
        assert r.status_code == 201, r.text

        row = _fetch_order_row(migrated_database, store_id, order_id)
        assert row["fulfillment_status_code"] == code

    @pytest.mark.parametrize(
        "bad_code", ["refunded", "settlement_pending", "reconciled", "nonsense"]
    )
    def test_ac01_a_non_fulfillment_value_is_rejected_not_silently_stored(
        self, client, migrated_database, bad_code
    ):
        """BR-10's binding interpretation: `refunded`, `settlement_pending`,
        and `reconciled` belong to a different state dimension entirely
        (refund activity / reconciliation) and must never be accepted here
        -- collapsing dimensions is AGENTS.md's non-negotiable #1.
        `nonsense` is the ordinary out-of-enumeration case. Status code is
        deliberately not pinned to one exact value (400 vs 422 is an
        implementation choice no document makes) -- what IS pinned is that
        it is never 2xx and the row is never written.

        Mutation this catches: a fulfillment value from the request
        reaching the `orders` row unchecked.
        """
        with _committed(migrated_database) as c:
            store_id = str(_create_store_db(c, "AC01 Bad Fulfillment Store"))
            owner = _create_user(c, role="owner", password="ac01-badfulfil-owner-pw")
        token = _sign_in(client, owner, with_mfa=True)

        order_id = _new_order_id(f"ac01-badfulfil-{bad_code}")
        payload = _order_payload(
            walmart_order_id=order_id, fulfillment_status_code=bad_code
        )

        r = _import(client, token, store_id, [payload])
        assert r.status_code in (400, 422), r.text
        assert _fetch_order_row(migrated_database, store_id, order_id) is None

    def test_ac01_currency_code_is_required_and_never_defaulted(
        self, client, migrated_database
    ):
        """BR-12/SC-05: omitting currency_code must be refused -- never
        silently defaulted to the store's own reporting_currency_code
        (deliberately EUR here, away from the most tempting hardcoded
        fallback of USD) and never to a hardcoded value of any kind.

        Mutation this catches: `payload.get("currency_code") or
        store["reporting_currency_code"]` or any other default-filling.
        """
        with _committed(migrated_database) as c:
            store_id = str(
                _create_store_db(
                    c, "AC01 Currency Store", reporting_currency_code="EUR"
                )
            )
            owner = _create_user(c, role="owner", password="ac01-currency-owner-pw")
        token = _sign_in(client, owner, with_mfa=True)

        order_id = _new_order_id("ac01-currency")
        payload = _order_payload(walmart_order_id=order_id)
        del payload["currency_code"]

        r = _import(client, token, store_id, [payload])
        assert r.status_code == 422, r.text
        assert _fetch_order_row(migrated_database, store_id, order_id) is None

    def test_ac01_missing_required_field_fails_as_a_plain_validation_error(
        self, client, migrated_database
    ):
        """Owner ruling (not re-litigated here): 'supported order payload'
        names no bespoke 'unsupported payload' rejection branch -- C-02
        governs settlement reports, not orders. A payload missing a
        required field (`sku`, here) fails as an ordinary missing-field
        422, nothing more.
        """
        with _committed(migrated_database) as c:
            store_id = str(_create_store_db(c, "AC01 Missing Field Store"))
            owner = _create_user(c, role="owner", password="ac01-missing-owner-pw")
        token = _sign_in(client, owner, with_mfa=True)

        order_id = _new_order_id("ac01-missing")
        payload = _order_payload(walmart_order_id=order_id)
        del payload["sku"]

        r = _import(client, token, store_id, [payload])
        assert r.status_code == 422, r.text
        assert _fetch_order_row(migrated_database, store_id, order_id) is None

    def test_manager_assigned_to_the_store_can_import(self, client, migrated_database):
        """Capability matrix (walmart-user-stories.md:674) -- 'Import
        orders' is Yes/Yes/Yes. A Manager, specifically (not Owner),
        must succeed. Mutation this catches: an implementation that
        reused an Owner/Admin-only gate (the 'Create store' shape, or
        US-004's grant/revoke gate) for this endpoint by mistake.
        """
        with _committed(migrated_database) as c:
            store_id = str(_create_store_db(c, "Role Manager Import Store"))
            manager = _create_user(c, role="manager", password="role-mgr-import-pw")
            _assign(c, manager["id"], store_id)
        token = _sign_in(client, manager)

        order_id = _new_order_id("role-mgr")
        r = _import(client, token, store_id, [_order_payload(walmart_order_id=order_id)])
        assert r.status_code == 201, r.text
        assert _fetch_order_row(migrated_database, store_id, order_id) is not None

    def test_import_into_an_out_of_scope_store_is_refused_indistinguishably_from_nonexistent(
        self, client, migrated_database
    ):
        """BT-08/US-002 AC-02's posture, one layer up. Mutation this
        catches: translating "store not assigned" to 403 instead of 404
        -- which would itself disclose that the store exists, exactly the
        leak `src/scoping.py`'s module docstring describes.

        A control import against a store the SAME Manager genuinely IS
        assigned to comes first, and must succeed (201). Without it, a
        bare "both calls got 404" assertion would pass vacuously against
        a route that does not exist at all yet -- every request returns
        FastAPI's own generic routing 404 regardless of what this test is
        trying to pin, which is exactly what happened the first time this
        test was run (confirmed empirically: it passed with zero
        production code written, the same trap
        `test_us004_manage_store_assignments.py`'s
        `test_owner_targeting_a_nonexistent_store_gets_404_not_403`
        documents and guards against). The control forces the later 404s
        to mean "a real importer judged this store out of scope", not
        "no such endpoint exists".
        """
        with _committed(migrated_database) as c:
            in_scope_store = str(_create_store_db(c, "Role OOS Control Store"))
            out_of_scope_store = str(_create_store_db(c, "Role OOS Import Store"))
            manager = _create_user(c, role="manager", password="role-oos-mgr-pw")
            _assign(c, manager["id"], in_scope_store)
            # deliberately NOT assigned to out_of_scope_store
        token = _sign_in(client, manager)

        control = _import(
            client, token, in_scope_store, [_order_payload(walmart_order_id=_new_order_id("oos-control"))]
        )
        assert control.status_code == 201, control.text

        unassigned = _import(client, token, out_of_scope_store, [_order_payload()])
        nonexistent = _import(client, token, str(uuid.uuid4()), [_order_payload()])

        assert unassigned.status_code == nonexistent.status_code == 404
        assert unassigned.json() == nonexistent.json()

    def test_import_into_an_inactive_store_is_refused_interim_b9(
        self, client, migrated_database
    ):
        """INTERIM, B-9 (PROGRESS.md): whether an inactive store accepts
        writes at all is an open Owner question. This pins the
        narrowest, explicitly-interim answer for THIS endpoint: an import
        into an inactive store is refused, visibly -- never silently
        accepted (2xx with a row written) and never silently skipped (2xx
        with no visible indication anything was refused). If the Owner
        later rules the other way, THIS test is the one to revisit, not
        AC-01/AC-02 or the capability matrix.

        Status code is deliberately NOT pinned to one exact value -- only
        that it is not 200/201, and that the response body visibly
        contains SOMETHING (an `errors` list or a `detail` key), and that
        no row is written.
        """
        with _committed(migrated_database) as c:
            store_id = str(_create_store_db(c, "Inactive Import Store", is_active=False))
            owner = _create_user(c, role="owner", password="inactive-import-owner-pw")
        token = _sign_in(client, owner, with_mfa=True)

        order_id = _new_order_id("inactive")
        r = _import(client, token, store_id, [_order_payload(walmart_order_id=order_id)])

        assert r.status_code not in (200, 201), (
            "an import into an inactive store must not silently succeed -- "
            f"got {r.status_code}: {r.text}"
        )
        body = r.json()
        assert body.get("errors") or body.get("detail"), (
            "the refusal must be visible in the response body, not a bare "
            f"empty success-shaped envelope: {body!r}"
        )
        assert _fetch_order_row(migrated_database, store_id, order_id) is None


# ---------------------------------------------------------------------------
# AC-02 -- refresh retains identity, no duplicates, per-store uniqueness
# ---------------------------------------------------------------------------


class TestAC02RefreshRetainsIdentityNoDuplicates:
    def test_ac02_reimporting_the_same_source_order_does_not_create_a_duplicate_row(
        self, client, migrated_database
    ):
        """BT-09's behavioural shape, at the business level named by
        AC-02: a genuinely later refresh (a fresh Idempotency-Key, the
        shape of a real second import run, not a retried HTTP request)
        for the SAME source order must still leave exactly one row.

        Mutation this catches: the upsert's ON CONFLICT target omitted or
        wrong (e.g. conflicting on `id` instead of
        `(store_id, walmart_order_id)`), which would insert a second row
        instead of updating the first.
        """
        with _committed(migrated_database) as c:
            store_id = str(_create_store_db(c, "AC02 Duplicate Store"))
            owner = _create_user(c, role="owner", password="ac02-dup-owner-pw")
        token = _sign_in(client, owner, with_mfa=True)

        order_id = _new_order_id("ac02-dup")
        first = _import(
            client,
            token,
            store_id,
            [_order_payload(walmart_order_id=order_id, quantity=1, selling_amount="10.00")],
        )
        assert first.status_code == 201, first.text

        second = _import(
            client,
            token,
            store_id,
            [_order_payload(walmart_order_id=order_id, quantity=2, selling_amount="19.98")],
        )
        assert second.status_code == 200, second.text

        assert _count_orders(migrated_database, store_id, order_id) == 1

    def test_ac02_reimport_retains_identity_and_updates_in_place_not_delete_then_insert(
        self, client, migrated_database
    ):
        """The structural half of 'without ... loss of manual costs': no
        cost tables exist yet (order_cost_profiles arrives in US-008), so
        the strongest honest pin available today is that re-import is an
        UPDATE over the existing row's identity, never a delete-then-
        insert. A delete-then-insert would assign a new primary key and a
        new `created_at` -- and once a future child table holds a foreign
        key to `orders.id` (costs, settlement links, reconciliation),
        that would silently orphan or destroy everything attached to the
        original row. This also proves the SECOND call is a genuine
        refresh, not a no-op: the changed fields must actually change.

        Mutation this catches: an importer that deletes the existing row
        and inserts a fresh one on every re-import (same external
        behaviour -- one row survives -- but a different, fragile
        implementation that this story's own identity guarantee forbids).
        """
        with _committed(migrated_database) as c:
            store_id = str(_create_store_db(c, "AC02 Identity Store"))
            owner = _create_user(c, role="owner", password="ac02-identity-owner-pw")
        token = _sign_in(client, owner, with_mfa=True)

        order_id = _new_order_id("ac02-identity")
        first = _import(
            client,
            token,
            store_id,
            [_order_payload(walmart_order_id=order_id, quantity=1, selling_amount="10.00")],
        )
        assert first.status_code == 201, first.text
        before = _fetch_order_row(migrated_database, store_id, order_id)

        second = _import(
            client,
            token,
            store_id,
            [_order_payload(walmart_order_id=order_id, quantity=5, selling_amount="50.00")],
        )
        assert second.status_code == 200, second.text
        after = _fetch_order_row(migrated_database, store_id, order_id)

        # Identity: the same row, not a new one.
        assert after["id"] == before["id"]
        assert after["created_at"] == before["created_at"]
        assert after["store_id"] == before["store_id"]
        assert after["walmart_order_id"] == before["walmart_order_id"]

        # Genuine refresh: the changed source fields actually changed.
        assert after["quantity"] == 5
        assert after["selling_amount"] == Decimal("50.00")
        assert before["quantity"] == 1

    def test_ac02_same_external_id_in_two_different_stores_are_two_independent_orders(
        self, client, migrated_database
    ):
        """'Source order identifiers are unique within a store, not
        globally' -- the business-facing half of DB-12, pinned through
        the import endpoint itself rather than only at the raw schema
        layer above.

        Mutation this catches: an importer that keys its upsert on
        `walmart_order_id` alone (ignoring `store_id`), which would make
        the second store's import silently overwrite the first store's
        row instead of creating its own.
        """
        with _committed(migrated_database) as c:
            store_a = str(_create_store_db(c, "AC02 Shared ID Store A"))
            store_b = str(_create_store_db(c, "AC02 Shared ID Store B"))
            owner = _create_user(c, role="owner", password="ac02-shared-owner-pw")
        token = _sign_in(client, owner, with_mfa=True)

        order_id = _new_order_id("ac02-shared")
        r_a = _import(
            client, token, store_a, [_order_payload(walmart_order_id=order_id, sku="SKU-A")]
        )
        assert r_a.status_code == 201, r_a.text
        r_b = _import(
            client, token, store_b, [_order_payload(walmart_order_id=order_id, sku="SKU-B")]
        )
        assert r_b.status_code == 201, r_b.text

        row_a = _fetch_order_row(migrated_database, store_a, order_id)
        row_b = _fetch_order_row(migrated_database, store_b, order_id)
        assert row_a is not None and row_b is not None
        assert row_a["id"] != row_b["id"]
        assert row_a["sku"] == "SKU-A"
        assert row_b["sku"] == "SKU-B"
        # Neither store's count was inflated by the other's import.
        assert _count_orders(migrated_database, store_a, order_id) == 1
        assert _count_orders(migrated_database, store_b, order_id) == 1


# ---------------------------------------------------------------------------
# Write discipline -- not an AC-01/AC-02 criterion itself, but the
# project-wide "every write requires an Idempotency-Key" pattern every
# other write endpoint in this codebase already carries
# (add_store_note, create_store, grant_manager, revoke_manager). Kept to
# a single test so this file stays focused on AC-01/AC-02's own content.
# ---------------------------------------------------------------------------


class TestWriteRequiresAnIdempotencyKey:
    def test_import_requires_an_idempotency_key(self, client, migrated_database):
        with _committed(migrated_database) as c:
            store_id = str(_create_store_db(c, "Idem No Key Import Store"))
            owner = _create_user(c, role="owner", password="idem-nokey-owner-pw")
        token = _sign_in(client, owner, with_mfa=True)

        r = client.post(
            "/imports/orders",
            json={"store_id": store_id, "orders": [_order_payload()]},
            headers=_auth(token),
        )
        assert r.status_code == 422


# ---------------------------------------------------------------------------
# F-88 (invariant-auditor, round 1 on US-005a): the idempotent-replay path
# was entirely unproven. No test above ever reuses an Idempotency-Key, or
# omits one beyond the single 422 check above. Proven here by deletion, the
# same way the auditor itself found the gap: deleting the pre-check
# (`service.py:233-237`), the `store_id` guard inside
# `_check_import_replay_target` (`service.py:190-193`), the concurrency
# backstop (`service.py:280-288`), or the `audit.record` call itself
# (`service.py:268-279`) must each break at least one test below.
#
# Conventions follow `test_us003_create_store.py`'s own
# `TestF31ReplayIsBoundToEntityAndEventType` directly -- same
# `_fetch_audit_rows` helper, same "control call succeeds, reused key is
# refused" shape, same "no row leaked across the boundary the key was
# reused across" assertion.
# ---------------------------------------------------------------------------


class TestF88ReplayPathIsProven:
    def test_ac02_an_honest_retry_under_the_same_key_replays_without_a_second_row(
        self, client, migrated_database
    ):
        """Honest replay: the SAME operation_id, the SAME store, the SAME
        payload, sent twice -- the shape of a client that genuinely did not
        see the first response and retried, not a second, later import run
        (that is `test_ac02_reimporting_the_same_source_order_does_not_
        create_a_duplicate_row`, a DIFFERENT operation_id, above).

        Mutation this catches: deleting the pre-check
        (`audit.find_by_operation` in `import_orders`) would run the
        upsert a second time -- still only one row survives, because the
        upsert itself is keyed on `(store_id, walmart_order_id)` (AC-02),
        but a SECOND audit_events row would exist for the same
        operation_id, which `audit_events`'s own unique constraint on
        `operation_id` forbids outright (a raw `UniqueViolation`, not a
        clean 200). Deleting `audit.record` itself would make
        `find_by_operation` return `None` forever, so the pre-check branch
        would never fire and this test would see the SAME failure mode.
        Either way, this test's three assertions together close both
        holes: `_count_orders == 1` catches a second upsert surviving at
        all; `len(audit_rows) == 1` catches a second audit row (or the
        absence of the one real write); `order_id` equality catches the
        replay response fabricating a DIFFERENT id instead of handing back
        the original.
        """
        with _committed(migrated_database) as c:
            store_id = str(_create_store_db(c, "F88 Honest Replay Store"))
            owner = _create_user(c, role="owner", password="f88-honest-owner-pw")
        token = _sign_in(client, owner, with_mfa=True)

        order_id = _new_order_id("f88-honest")
        op_id = _idempotency_key()
        payload = [_order_payload(walmart_order_id=order_id, quantity=2, selling_amount="20.00")]

        first = _import(client, token, store_id, payload, operation_id=op_id)
        assert first.status_code == 201, first.text
        first_order_id = first.json()["data"]["orders"][0]["order_id"]
        assert first.json()["meta"]["replayed"] is False

        second = _import(client, token, store_id, payload, operation_id=op_id)
        assert second.status_code in (200, 201), second.text
        assert second.json()["meta"]["replayed"] is True
        second_order_id = second.json()["data"]["orders"][0]["order_id"]

        # Same order, not a newly-created one.
        assert second_order_id == first_order_id

        # Exactly one row written, not two.
        assert _count_orders(migrated_database, store_id, order_id) == 1

        # Exactly one audit row for this operation_id -- proves
        # `audit.record` actually ran on the first call (if it had been
        # deleted, `find_by_operation` would never find a match and the
        # second call would attempt a second upsert instead of replaying).
        audit_rows = _fetch_audit_rows(migrated_database, op_id)
        assert len(audit_rows) == 1

    def test_ac02_the_same_key_reused_against_a_different_store_is_a_conflict_not_a_replay(
        self, client, migrated_database
    ):
        """F-88's sharpest case, the one that makes the `store_id` guard
        inside `_check_import_replay_target` (`service.py:190-193`) load-
        bearing rather than decorative. An `Idempotency-Key` spent on an
        import into store X, reused on an import into store Y, must be
        refused as a conflict (409) -- never silently replayed as if it
        were a genuine write into Y (which would hand the caller store X's
        order ids while writing nothing into Y, a success report for a
        request that did not do what it said).

        Mutation this catches: deleting the `if existing_event["store_id"]
        != store_id` guard in `_check_import_replay_target` -- without it,
        `audit.check_replay_target` alone is satisfied (same `event_type`,
        same `entity_type`, `entity_id` is always `None` for a batch import,
        so there is nothing else to disambiguate on), and this call would
        return 200 with store X's results instead of 409.
        """
        with _committed(migrated_database) as c:
            store_x = str(_create_store_db(c, "F88 Cross-Store Replay Store X"))
            store_y = str(_create_store_db(c, "F88 Cross-Store Replay Store Y"))
            owner = _create_user(c, role="owner", password="f88-crossstore-owner-pw")
        token = _sign_in(client, owner, with_mfa=True)

        op_id = _idempotency_key()
        order_in_x = _new_order_id("f88-cross-x")
        first = _import(
            client, token, store_x, [_order_payload(walmart_order_id=order_in_x)], operation_id=op_id
        )
        assert first.status_code == 201, first.text

        order_in_y = _new_order_id("f88-cross-y")
        second = _import(
            client, token, store_y, [_order_payload(walmart_order_id=order_in_y)], operation_id=op_id
        )
        assert second.status_code == 409, second.text

        # Store Y's import must not have silently "succeeded" by replaying
        # store X's result -- zero rows for the payload that was actually
        # sent to Y.
        assert _count_orders(migrated_database, store_y, order_in_y) == 0

        audit_rows = _fetch_audit_rows(migrated_database, op_id)
        assert len(audit_rows) == 1
        assert str(audit_rows[0]["store_id"]) == store_x

    def test_f88_a_key_spent_on_store_creation_cannot_be_replayed_as_an_import(
        self, client, migrated_database
    ):
        """Pins the `event_type` arm of `audit.check_replay_target`
        specifically for this entity: an Idempotency-Key already spent on
        a `store_created` event (a completely different write, a
        completely different entity_type) must be refused, not treated as
        an import replay. `entity_id` alone cannot disambiguate a batch
        import (it is always `None`), so this is the one test in this file
        that proves `event_type`/`entity_type` -- not `entity_id` -- are
        doing the disambiguating work for imports.
        """
        with _committed(migrated_database) as c:
            owner = _create_user(c, role="owner", password="f88-crossop-owner-pw")
            import_store_id = str(_create_store_db(c, "F88 Cross-Op Import Store"))
        token = _sign_in(client, owner, with_mfa=True)

        op_id = _idempotency_key()
        create_store = client.post(
            "/stores",
            json={
                "name": "F88 Cross-Op Created Store",
                "walmart_store_key": f"F88-CROSSOP-{uuid.uuid4().hex[:12]}",
            },
            headers={**_auth(token), "Idempotency-Key": op_id},
        )
        assert create_store.status_code == 201, create_store.text

        order_id = _new_order_id("f88-crossop")
        reused = _import(
            client,
            token,
            import_store_id,
            [_order_payload(walmart_order_id=order_id)],
            operation_id=op_id,
        )
        assert reused.status_code == 409, reused.text
        assert _fetch_order_row(migrated_database, import_store_id, order_id) is None
