"""US-008 -- Enter buying cost per unit. Module: Costing.

docs/walmart-user-stories.md:824-834 (verbatim):

    AC-01: Given an order, when entering buying cost, then the input is
    explicitly per unit and total buying cost is calculated from imported
    quantity (BR-02; one SKU per order).
    AC-02: Given an order with no entered cost, when calculating
    completeness, then the amount remains unknown; no SKU/store
    buying-cost default is substituted.
    AC-03: Given a known zero cost, when explicitly saved as zero, then
    it is distinguishable from a blank input.
    AC-04: Given a successful change to one order's unit cost, when
    recalculating, then other orders' costs do not change, including
    other orders carrying the same SKU.

THE FIRST MONEY CODE IN THE SYSTEM. Nothing in ``src/`` computes or stores
a cost today -- ``src/costing/`` does not exist, and neither does the
``order_cost_profiles`` table (fully specified at
``docs/walmart-store-manager-database-schema.md:544-576``, zero hits for
either in ``src/`` or any migration, confirmed by search before writing
this file). Every behavioural test below is expected to fail on
``ModuleNotFoundError`` (the import is deferred INSIDE each test body, the
same convention ``tests/test_us005b1_import_run_record.py`` already uses,
so a missing module fails only the tests that need it, not the whole
file's collection). Every schema test below is expected to fail on
``psycopg.errors.UndefinedTable``.

-------------------------------------------------------------------------
Scope fences (restated from the brief so this file can be checked against
them directly):

* Buying cost ONLY. This file does NOT test the other five `profit-v1`
  cost components, the profit formula, ROI, estimate versions, or the
  per-field edit allowance (`cost-edit-v1`, US-011) -- the one exception
  is the narrow "completeness" state AC-02 itself names, scoped to
  exactly one component code (see the G-1-style invention below).
* No closed-month / `has_ever_been_closed` test. `accounting_month_id` is
  nullable and nothing posts to periods yet (US-005c deferred). This file
  never provisions an `accounting_months` row and never asserts BR-08.
* No multi-SKU branch. `DECISIONS.md` 2026-10-28 ("US-008 proceeds on
  BR-02...") -- B-18 already rejects a multi-SKU order WHOLE at import,
  so no such order can ever reach a cost profile; a test demanding a
  multi-SKU branch here would be testing a case this module can provably
  never see.
* No UI/template test. `spec-oracle` confirmed (per the brief) that none
  of the four ACs reference a rendered screen and `Dependencies: US-007`
  is delivery ordering only. Every test below calls a service/repository
  function directly, or the ALREADY-BUILT `POST /imports/orders` JSON API
  to create realistic order fixtures -- never a template, never HTML.

-------------------------------------------------------------------------
Two decisions already recorded, applied here rather than re-decided
(`DECISIONS.md`, both dated 2026-10-28):

1. **Currency provenance.** A cost profile inherits the parent order's
   own `currency_code`, always -- never the store's `reporting_currency_
   code`, never independently set, never defaulted to USD. Tested below
   by DELIBERATELY making the two differ (store reporting currency USD,
   one specific order's own currency EUR) so a coincidental match cannot
   hide a wrong implementation that reads the store instead of the order.
2. **BR-02/B-18.** No multi-SKU test, per the scope fence above.

-------------------------------------------------------------------------
This file's own disclosed inventions (G-1/B-12 precedent -- nothing below
is derived from a document; each is a judgement this file commits to and
states plainly):

1. **New package, three functions, exact signatures the implementer must
   match:**

   ``src.costing.service.save_buying_cost(conn, scope, *, actor_id,
   order_id, buying_cost_per_unit, operation_id) -> dict``
       ``buying_cost_per_unit`` is a decimal-exact ``str`` (e.g. ``"0.00"``,
       ``"1.005"``) or ``None`` (an explicit clear/blank). A ``float`` must
       be rejected the same way ``src.money.Money`` already rejects one
       (``InvalidMoney``) -- this module reuses ``Money``, it does not
       reimplement decimal handling a second time (AGENTS.md: "One
       implementation of profit-v1... never reimplemented a second time").
       Raises ``src.scoping.OutOfScope`` -- never a different exception,
       never 403-shaped -- for an order whose store is outside ``scope``
       AND for an ``order_id`` that does not exist at all. The two cases
       must be indistinguishable (US-002 AC-02's posture, reused here),
       the same discipline ``stores_repository.get_store`` already
       applies, generalised from "store not in scope" to "order's store
       not in scope".
       Returns a dict with EXACTLY these six keys (structural pin, same
       discipline ``test_us005b1_import_run_record.py`` already applies to
       ``get_import_run_state`` -- so a fabricated field cannot slip in
       unnoticed):
       ``order_id``, ``currency_code``, ``quantity_snapshot``,
       ``buying_cost_per_unit`` (``Decimal | None``),
       ``buying_cost_per_unit_is_known_zero`` (``bool``),
       ``buying_cost_total`` (``Decimal | None``).

   ``src.costing.repository.get_cost_profile(conn, scope, order_id) ->
   dict`` -- same six-key return shape as above, read-only. **Must return
   a fully-populated dict even when no ``order_cost_profiles`` row has
   ever been written for this order** (a "virtual blank" profile:
   ``buying_cost_per_unit=None``, ``is_known_zero=False``,
   ``buying_cost_total=None``, ``currency_code``/``quantity_snapshot``
   read straight from the order) -- it must NOT raise merely because
   nothing has been saved yet; it raises ``OutOfScope`` ONLY for the
   same two cases ``save_buying_cost`` does (out-of-scope order,
   nonexistent order).

   ``src.costing.service.get_cost_completeness(conn, scope, order_id) ->
   dict`` -- the ONE thing in scope beyond the four ACs (AC-02 says "when
   calculating completeness"). Returns AT LEAST ``complete: bool`` and
   ``missing_component_codes: list[str]``. This file pins membership only
   (never a full-list/full-dict equality) and only for ONE component
   code, ``"buying_cost_total"`` (chosen to match the schema's own name
   for this component, `database-schema.md:251` -- not invented from
   nothing). The other five component codes are legitimately always
   missing at this story's stage (nothing populates them yet) and this
   file never asserts anything about them, so later US-009/US-010 work
   cannot break this file by adding them.

2. No HTTP endpoint is invented for saving a buying cost -- UI-05/UI-06
   do not exist and no AC requires one. If the implementer builds one
   anyway for consistency with the rest of the app, these tests (calling
   the service function directly) still exercise the real business logic
   underneath it.

-------------------------------------------------------------------------
Conventions reused, not reinvented, from ``tests/test_us005b1_import_run_
record.py`` (copied locally, per that file's own stated discipline that
each test file keeps its own local helpers): ``_create_user``,
``_create_store_db``, ``_assign``, ``_committed``, the ``client`` fixture,
``_sign_in``, ``_order_payload``, ``_import``, ``_idempotency_key``. Real
orders are created through the ALREADY-BUILT, ALREADY-GREEN
``POST /imports/orders`` endpoint rather than hand-rolled with raw SQL, so
every fixture order carries a realistic, correctly-derived shape (BR-14
``scope_status_code``, a real ``order_date`` conversion, etc.) instead of
a test-only approximation that could silently diverge from what the
importer actually produces.
"""

from __future__ import annotations

import uuid
from contextlib import contextmanager
from decimal import Decimal

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row

from src import main

# ---------------------------------------------------------------------------
# helpers -- copied locally from test_us005b1_import_run_record.py, trimmed
# to what this file needs (manager-only; no MFA path is exercised here).
# ---------------------------------------------------------------------------


def _new_order_id(label: str) -> str:
    return f"US008-{label}-{uuid.uuid4().hex[:12]}"


def _create_user(c, *, role="manager", password="us008-pw", mfa_enabled=False, mfa_secret=None):
    from src.access import passwords

    email = f"us008-{role}-{uuid.uuid4().hex}@example.test"
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
    timezone_name="America/New_York",
    reporting_currency_code="USD",
    managed_from_year=2026,
    managed_from_month=1,
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
            f"US008-{uuid.uuid4().hex[:12]}",
            timezone_name,
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


def _sign_in(client, user) -> str:
    r = client.post(
        "/auth/session", json={"email": user["email"], "password": user["password"]}
    )
    assert r.status_code == 201, r.text
    token = r.json()["data"]["token"]
    assert token
    return token


def _idempotency_key() -> str:
    return str(uuid.uuid4())


def _order_payload(**overrides) -> dict:
    payload = {
        "walmart_order_id": _new_order_id("default"),
        "walmart_purchase_order_id": None,
        "sku": "SKU-US008-DEFAULT",
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


def _import_single_order(client, token, store_id, **overrides) -> uuid.UUID:
    """Create one real order via the already-built import endpoint and
    return its internal ``orders.id`` -- never a hand-rolled SQL insert,
    per this file's own module docstring.
    """
    order_id = _new_order_id("fixture")
    payload = _order_payload(walmart_order_id=order_id, **overrides)
    r = _import(client, token, store_id, [payload])
    assert r.status_code in (200, 201), (
        f"fixture setup failed: order import must succeed: {r.status_code} {r.text}"
    )
    orders = r.json()["data"]["orders"]
    assert len(orders) == 1, f"fixture setup failed: expected exactly one order: {orders!r}"
    return uuid.UUID(orders[0]["order_id"])


def _manager_scope(manager_id, *store_ids):
    from src.scoping import StoreScope

    return StoreScope.assigned(actor_id=manager_id, store_ids=list(store_ids))


def _insert_order_direct_bypassing_scope(conn, store_id, **overrides) -> dict:
    """Create a real ``orders`` row by direct SQL, deliberately bypassing
    the scope-checked ``POST /imports/orders`` endpoint.

    Same pattern as ``TestOrderCostProfilesSchemaInvariants.
    _insert_order_direct`` in this same file, and as
    ``test_us005b1_import_run_record.py``'s unassigned-store tests
    (``test_an_out_of_scope_store_raises_out_of_scope_not_a_quiet_empty_
    state``): a precondition of "a row exists in a store the actor is NOT
    assigned to" cannot be built through the real import endpoint, because
    that endpoint is itself correctly scope-checked and will 404 first --
    testing the endpoint's own scoping there, not the costing module's
    scoping here. The actor's lack of assignment is asserted by the caller
    (never assigning ``manager`` to ``store_id`` here, never calling the
    HTTP API for this one row), not by this helper.
    """
    fields = {
        "walmart_order_id": _new_order_id("unassigned"),
        "walmart_purchase_order_id": None,
        "sku": "SKU-US008-UNASSIGNED",
        "item_description": "Test item (unassigned-store fixture)",
        "order_placed_at_utc": "2026-07-15 14:30:00+00",
        "order_date": "2026-07-15",
        "accounting_month_id": None,
        "scope_status_code": "managed",
        "posting_status_code": "unpostable_no_period",
        "quantity": 1,
        "selling_amount": "9.99",
        "currency_code": "USD",
        "fulfillment_status_code": "delivered",
        "last_imported_at": "2026-07-15 14:30:05+00",
    }
    fields.update(overrides)
    columns = ["store_id", *fields.keys()]
    values = [store_id, *fields.values()]
    placeholders = ", ".join(["%s"] * len(columns))
    return conn.execute(
        f"insert into orders ({', '.join(columns)}) values ({placeholders}) returning *",
        values,
    ).fetchone()


# ---------------------------------------------------------------------------
# Schema-level invariants -- direct SQL against order_cost_profiles,
# independent of any service function. Expected failure right now:
# psycopg.errors.UndefinedTable: relation "order_cost_profiles" does not
# exist (or a readable AssertionError from the information_schema-based
# assertions reporting the table/column missing).
# ---------------------------------------------------------------------------


class TestOrderCostProfilesSchemaInvariants:
    def _insert_order_direct(self, conn, store_id, **overrides):
        fields = {
            "walmart_order_id": _new_order_id("schema"),
            "walmart_purchase_order_id": None,
            "sku": "SKU-SCHEMA-TEST",
            "item_description": None,
            "order_placed_at_utc": "2026-07-15 14:30:00+00",
            "order_date": "2026-07-15",
            "accounting_month_id": None,
            "scope_status_code": "managed",
            "posting_status_code": "unpostable_no_period",
            "quantity": 1,
            "selling_amount": "9.99",
            "currency_code": "USD",
            "fulfillment_status_code": "delivered",
            "last_imported_at": "2026-07-15 14:30:05+00",
        }
        fields.update(overrides)
        columns = ["store_id", *fields.keys()]
        values = [store_id, *fields.values()]
        placeholders = ", ".join(["%s"] * len(columns))
        return conn.execute(
            f"insert into orders ({', '.join(columns)}) values ({placeholders}) returning *",
            values,
        ).fetchone()

    def _insert_cost_profile_direct(self, conn, order_id, **overrides):
        fields = {
            "currency_code": "USD",
            "quantity_snapshot": 1,
        }
        fields.update(overrides)
        columns = ["order_id", *fields.keys()]
        values = [order_id, *fields.values()]
        placeholders = ", ".join(["%s"] * len(columns))
        return conn.execute(
            f"insert into order_cost_profiles ({', '.join(columns)}) values "
            f"({placeholders}) returning *",
            values,
        ).fetchone()

    def test_order_cost_profiles_has_the_documented_columns_and_not_null_shape(
        self, conn, store_id
    ):
        """database-schema.md:548-569. Mutation this catches: any NOT NULL
        column declared nullable, or a documented nullable column
        (``buying_cost_per_unit``, ``buying_cost_total``) tightened to
        NOT NULL -- the latter would make "unknown" unrepresentable,
        AGENTS.md non-negotiable #4.
        """
        rows = conn.execute(
            "select column_name, is_nullable from information_schema.columns "
            "where table_name = 'order_cost_profiles'"
        ).fetchall()
        assert rows, "order_cost_profiles table does not exist yet"
        by_name = {r["column_name"]: r["is_nullable"] for r in rows}

        # Full documented shape must exist (the retrofit-avoidance
        # discipline already applied to orders/accounting_months in
        # 003_orders.sql), even though this story only populates the
        # buying-cost-related columns.
        for col in [
            "id", "order_id", "currency_code", "buying_cost_per_unit",
            "buying_cost_per_unit_is_known_zero", "quantity_snapshot",
            "buying_cost_total", "store_settings_version_applied",
            "warehouse_cost_default_snapshot", "warehouse_cost_override",
            "warehouse_cost_effective", "third_party_label_cost", "other_cost",
            "cost_completeness_code", "record_revision", "notes",
            "created_by", "updated_by", "created_at", "updated_at",
        ]:
            assert col in by_name, f"missing column: {col}"

        # Only the columns THIS story owns get a pinned nullability.
        for col in ["id", "order_id", "currency_code", "quantity_snapshot",
                    "created_at", "updated_at"]:
            assert by_name[col] == "NO", f"{col} must be NOT NULL"

        for col in ["buying_cost_per_unit", "buying_cost_total"]:
            assert by_name[col] == "YES", (
                f"{col} must be NULLABLE -- null means unknown, never zero "
                "(US-008 AC-02/AC-03, database-schema.md:575)"
            )

        assert by_name["buying_cost_per_unit_is_known_zero"] == "NO", (
            "buying_cost_per_unit_is_known_zero must be NOT NULL "
            "(defaults false) -- it is a flag, not itself an unknown value"
        )

    def test_order_cost_profiles_buying_cost_columns_are_numeric_12_2(self, conn):
        """database-schema.md:553,556. Mutation this catches: a `double
        precision`/`float` column (AGENTS.md #5, database-schema.md:1101),
        or a precision/scale other than (12, 2) -- which would silently
        change the manual-cost rounding rule from 2dp to something else.
        """
        rows = conn.execute(
            "select column_name, data_type, numeric_precision, numeric_scale "
            "from information_schema.columns "
            "where table_name = 'order_cost_profiles' "
            "and column_name in ('buying_cost_per_unit', 'buying_cost_total')"
        ).fetchall()
        assert rows, "order_cost_profiles table does not exist yet"
        by_name = {r["column_name"]: r for r in rows}
        for col in ["buying_cost_per_unit", "buying_cost_total"]:
            assert by_name[col]["data_type"] == "numeric", f"{col} must be numeric, not float"
            assert by_name[col]["numeric_precision"] == 12, f"{col} must be numeric(12,2)"
            assert by_name[col]["numeric_scale"] == 2, f"{col} must be numeric(12,2)"

    def test_order_cost_profiles_has_no_zero_or_other_default_on_monetary_columns(self, conn):
        """database-schema.md:1098: 'No default of 0 ... on any monetary
        ... column.' Mutation this catches: `buying_cost_per_unit numeric
        (12,2) default 0` -- which would make a never-touched row read as
        a known zero instead of unknown, violating AC-02/AC-03 at the
        schema level before any application code even runs.
        """
        rows = conn.execute(
            "select column_name, column_default from information_schema.columns "
            "where table_name = 'order_cost_profiles' "
            "and column_name in ('buying_cost_per_unit', 'buying_cost_total', 'currency_code')"
        ).fetchall()
        assert rows, "order_cost_profiles table does not exist yet"
        for row in rows:
            assert row["column_default"] is None, (
                f"{row['column_name']} must have NO column default "
                f"(got {row['column_default']!r}) -- BR-12/AC-02's unknown-is-"
                "not-zero rule must hold even for a row inserted with no "
                "explicit value"
            )

    def test_order_cost_profiles_order_id_is_unique_and_a_required_foreign_key(
        self, conn, store_id
    ):
        """database-schema.md:551 ('One active cost profile per order').
        Mutation this catches: no unique constraint (two profile rows per
        order, an ambiguous "current" value) or no foreign key (a profile
        pointing at an order that does not exist).
        """
        order = self._insert_order_direct(conn, store_id)

        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            with conn.transaction():
                self._insert_cost_profile_direct(conn, str(uuid.uuid4()))

        self._insert_cost_profile_direct(conn, order["id"])
        with pytest.raises(psycopg.errors.UniqueViolation):
            with conn.transaction():
                self._insert_cost_profile_direct(conn, order["id"])

    def test_order_cost_profiles_created_at_and_updated_at_are_timestamptz(self, conn):
        """UTC storage discipline (AGENTS.md). Mutation this catches: a
        bare `timestamp` (no time zone) column silently accepting and
        storing a naive, ambiguous instant.
        """
        rows = conn.execute(
            "select column_name, data_type from information_schema.columns "
            "where table_name = 'order_cost_profiles' "
            "and column_name in ('created_at', 'updated_at')"
        ).fetchall()
        assert rows, "order_cost_profiles table does not exist yet"
        by_name = {r["column_name"]: r["data_type"] for r in rows}
        assert by_name.get("created_at") == "timestamp with time zone"
        assert by_name.get("updated_at") == "timestamp with time zone"

    def test_order_cost_profiles_a_never_touched_row_defaults_to_unknown_not_zero(
        self, conn, store_id
    ):
        """A row inserted with no value at all for the buying-cost columns
        must read back as UNKNOWN (NULL, flag false), never a known zero
        -- the database-level half of AC-02/AC-03, independent of
        whatever the service layer later adds on top.
        """
        order = self._insert_order_direct(conn, store_id)
        row = self._insert_cost_profile_direct(conn, order["id"])
        assert row["buying_cost_per_unit"] is None
        assert row["buying_cost_per_unit_is_known_zero"] is False
        assert row["buying_cost_total"] is None


# ---------------------------------------------------------------------------
# AC-01 -- total buying cost is quantity x per-unit, exact decimal, one
# half-up rounding at the end. The sharp case: an entered per-unit value
# whose product lands exactly on a half-cent boundary, where a naive
# float implementation AND a naive double-rounding implementation both
# give a DIFFERENT, specific wrong answer from the correct one -- verified
# arithmetically before writing this test:
#
#   per_unit = Decimal("1.005"), quantity = 3
#   correct (round the TOTAL once):       1.005 * 3 = 3.015 -> 3.02
#   double-rounded (round per-unit FIRST): round(1.005) = 1.01 -> 1.01*3 = 3.03
#   naive float (binary fp arithmetic):    round(1.005*3, 2) in float = 3.01
#
# Three different wrong answers from three different naive approaches,
# all different from each other and from the correct 3.02 -- a test
# asserting only "the total is roughly right" could not tell these apart;
# this one pins the exact value.
# ---------------------------------------------------------------------------


class TestUS008AC01TotalBuyingCostFromExactDecimalArithmetic:
    def test_us008_ac01_total_buying_cost_is_quantity_times_per_unit_computed_in_exact_decimal_not_double_rounded(
        self, client, migrated_database
    ):
        """AC-01: 'the input is explicitly per unit and total buying cost
        is calculated from imported quantity' (BR-02).

        Mutation this catches: rounding `buying_cost_per_unit` to 2dp
        BEFORE multiplying by quantity (double rounding -- would produce
        3.03, not 3.02); using Python `float` arithmetic anywhere in the
        chain (would produce 3.01, not 3.02); deriving `quantity_snapshot`
        from anything other than the order's own imported `quantity`.

        Expected failure right now: `ModuleNotFoundError: No module named
        'src.costing'` -- the package does not exist.
        """
        from src.costing import service as costing_service

        with _committed(migrated_database) as c:
            store_id = str(_create_store_db(c, "AC01 Store"))
            manager = _create_user(c, role="manager")
            _assign(c, manager["id"], store_id)
        token = _sign_in(client, manager)

        order_id = _import_single_order(
            client, token, store_id, quantity=3, currency_code="USD"
        )

        with _committed(migrated_database) as conn:
            scope = _manager_scope(manager["id"], uuid.UUID(store_id))
            result = costing_service.save_buying_cost(
                conn,
                scope,
                actor_id=manager["id"],
                order_id=order_id,
                buying_cost_per_unit="1.005",
                operation_id=_idempotency_key(),
            )

        assert set(result.keys()) == {
            "order_id", "currency_code", "quantity_snapshot",
            "buying_cost_per_unit", "buying_cost_per_unit_is_known_zero",
            "buying_cost_total",
        }, f"unexpected or missing keys in save_buying_cost's result: {result!r}"

        assert result["order_id"] == order_id
        assert result["currency_code"] == "USD"
        assert result["quantity_snapshot"] == 3, (
            "quantity_snapshot must come from the order's own imported "
            f"quantity (3), got {result['quantity_snapshot']!r}"
        )

        assert isinstance(result["buying_cost_per_unit"], Decimal), (
            f"buying_cost_per_unit must be a Decimal, got "
            f"{type(result['buying_cost_per_unit']).__name__}"
        )
        assert result["buying_cost_per_unit"] == Decimal("1.01"), (
            "the manually-entered per-unit value is itself stored/"
            "returned at 2dp, half-up rounded once (profit-v1): 1.005 -> "
            f"1.01, got {result['buying_cost_per_unit']!r}"
        )

        assert isinstance(result["buying_cost_total"], Decimal), (
            f"buying_cost_total must be a Decimal, got "
            f"{type(result['buying_cost_total']).__name__}"
        )
        assert result["buying_cost_total"] == Decimal("3.02"), (
            "buying_cost_total must be computed from the EXACT entered "
            "value (1.005 * 3 = 3.015 exactly) with a SINGLE half-up "
            f"rounding applied to the total, giving 3.02 -- got "
            f"{result['buying_cost_total']!r}"
        )
        assert result["buying_cost_total"] != Decimal("3.03"), (
            "3.03 is what rounding buying_cost_per_unit to 2dp FIRST and "
            "then multiplying by quantity (double rounding) would wrongly "
            "produce -- profit-v1 forbids intermediate rounding"
        )
        assert result["buying_cost_total"] != Decimal("3.01"), (
            "3.01 is what naive binary-float arithmetic would wrongly "
            "produce (round(1.005 * 3, 2) in Python float) -- profit-v1 "
            "prohibits float for monetary arithmetic anywhere"
        )

        # Durability: read back over a SEPARATE connection, confirming
        # this is a real persisted row, not just an in-memory return value.
        with psycopg.connect(migrated_database, row_factory=dict_row) as verify_conn:
            persisted = verify_conn.execute(
                "select buying_cost_per_unit, buying_cost_total, "
                "quantity_snapshot, currency_code from order_cost_profiles "
                "where order_id = %s",
                (order_id,),
            ).fetchone()
        assert persisted is not None, "no order_cost_profiles row was persisted"
        assert persisted["buying_cost_per_unit"] == Decimal("1.01")
        assert persisted["buying_cost_total"] == Decimal("3.02")
        assert persisted["quantity_snapshot"] == 3


# ---------------------------------------------------------------------------
# AC-02 -- no entered cost leaves the amount unknown; no SKU/store default
# substituted. Proven here by giving a SECOND order THE SAME SKU as a
# FIRST order that already has a known buying cost -- if the
# implementation ever looked up "the last known cost for this SKU" as a
# convenience default, this is the test that would catch it.
# ---------------------------------------------------------------------------


class TestUS008AC02NoEnteredCostStaysUnknown:
    def test_us008_ac02_an_order_with_no_entered_cost_reports_the_amount_unknown_with_no_sku_or_store_default_substituted(
        self, client, migrated_database
    ):
        """AC-02: 'the amount remains unknown; no SKU/store buying-cost
        default is substituted.'

        Mutation this catches: a read that falls back to a previous order
        sharing the same SKU (or any store-level default) instead of
        reporting None/missing for an order that itself has never had a
        cost entered -- exactly the shape BR-02's own text rules out
        ("no store/SKU buying-cost default").

        Expected failure right now: `ModuleNotFoundError: No module named
        'src.costing'`.
        """
        from src.costing import repository as costing_repository
        from src.costing import service as costing_service

        with _committed(migrated_database) as c:
            store_id = str(_create_store_db(c, "AC02 Store"))
            manager = _create_user(c, role="manager")
            _assign(c, manager["id"], store_id)
        token = _sign_in(client, manager)

        shared_sku = "SKU-US008-AC02-SHARED"
        order_known = _import_single_order(
            client, token, store_id, sku=shared_sku, quantity=2
        )
        order_blank = _import_single_order(
            client, token, store_id, sku=shared_sku, quantity=5
        )

        scope = _manager_scope(manager["id"], uuid.UUID(store_id))
        with _committed(migrated_database) as conn:
            costing_service.save_buying_cost(
                conn,
                scope,
                actor_id=manager["id"],
                order_id=order_known,
                buying_cost_per_unit="4.00",
                operation_id=_idempotency_key(),
            )

        with psycopg.connect(migrated_database, row_factory=dict_row) as conn:
            blank_profile = costing_repository.get_cost_profile(conn, scope, order_blank)
            completeness = costing_service.get_cost_completeness(conn, scope, order_blank)

        # The amount itself stays unknown -- never coerced to zero, never
        # borrowed from the same SKU's other order.
        assert blank_profile["buying_cost_per_unit"] is None, (
            "an order with no entered cost must report buying_cost_per_"
            f"unit as None, not a value borrowed from the same SKU's "
            f"other order (4.00): got {blank_profile['buying_cost_per_unit']!r}"
        )
        assert blank_profile["buying_cost_total"] is None, (
            "buying_cost_total must also stay None -- it must not be "
            "computed from a substituted default per-unit value times "
            f"this order's own quantity: got {blank_profile['buying_cost_total']!r}"
        )
        assert blank_profile["buying_cost_per_unit_is_known_zero"] is False

        # The completeness check names buying cost as missing.
        assert "buying_cost_total" in completeness["missing_component_codes"], (
            "an order with no entered buying cost must list "
            "'buying_cost_total' among its missing components: "
            f"{completeness['missing_component_codes']!r}"
        )
        assert completeness["complete"] is False

        # Positive control: the OTHER order (same SKU, cost genuinely
        # entered) does NOT list buying cost as missing -- so this test
        # cannot pass merely because the completeness function always
        # reports everything as missing regardless of input.
        with psycopg.connect(migrated_database, row_factory=dict_row) as conn:
            known_completeness = costing_service.get_cost_completeness(
                conn, scope, order_known
            )
        assert "buying_cost_total" not in known_completeness["missing_component_codes"], (
            "setup/regression check: the order that DID have a cost "
            "entered must not list buying_cost_total as missing, or this "
            f"test proves nothing: {known_completeness['missing_component_codes']!r}"
        )


# ---------------------------------------------------------------------------
# AC-03 -- an explicitly saved zero is distinguishable from a blank input.
# Both the stored representation AND the completeness/behavioural read
# must differ, not merely an internal flag.
# ---------------------------------------------------------------------------


class TestUS008AC03KnownZeroDistinguishableFromBlank:
    def test_us008_ac03_an_explicitly_saved_zero_is_distinguishable_from_a_blank_input(
        self, client, migrated_database
    ):
        """AC-03: 'Given a known zero cost, when explicitly saved as
        zero, then it is distinguishable from a blank input.'

        Mutation this catches: representing an explicit zero the same
        way as "never entered" (both NULL, or both reporting
        `is_known_zero=False`) -- which would make a manager's genuine
        "I paid nothing for this unit" indistinguishable from "I have not
        looked at this order yet", and would make `buying_cost_total`
        silently become `None` for a cost that is actually known to be
        zero (the unknown-is-not-zero rule, applied in reverse: a KNOWN
        zero must not be coerced to unknown either).

        Expected failure right now: `ModuleNotFoundError: No module named
        'src.costing'`.
        """
        from src.costing import repository as costing_repository
        from src.costing import service as costing_service

        with _committed(migrated_database) as c:
            store_id = str(_create_store_db(c, "AC03 Store"))
            manager = _create_user(c, role="manager")
            _assign(c, manager["id"], store_id)
        token = _sign_in(client, manager)

        order_zero = _import_single_order(client, token, store_id, quantity=4)
        order_blank = _import_single_order(client, token, store_id, quantity=4)

        scope = _manager_scope(manager["id"], uuid.UUID(store_id))
        with _committed(migrated_database) as conn:
            zero_result = costing_service.save_buying_cost(
                conn,
                scope,
                actor_id=manager["id"],
                order_id=order_zero,
                buying_cost_per_unit="0.00",
                operation_id=_idempotency_key(),
            )

        with psycopg.connect(migrated_database, row_factory=dict_row) as conn:
            blank_profile = costing_repository.get_cost_profile(conn, scope, order_blank)
            zero_completeness = costing_service.get_cost_completeness(conn, scope, order_zero)
            blank_completeness = costing_service.get_cost_completeness(conn, scope, order_blank)

        # The known-zero order: a REAL, known value -- not absent.
        assert zero_result["buying_cost_per_unit"] is not None
        assert isinstance(zero_result["buying_cost_per_unit"], Decimal)
        assert zero_result["buying_cost_per_unit"] == Decimal("0.00")
        assert zero_result["buying_cost_per_unit_is_known_zero"] is True
        assert zero_result["buying_cost_total"] == Decimal("0.00"), (
            "a known zero per-unit cost must produce a KNOWN total of "
            f"0.00, not None: got {zero_result['buying_cost_total']!r}"
        )

        # The blank order: genuinely absent.
        assert blank_profile["buying_cost_per_unit"] is None
        assert blank_profile["buying_cost_per_unit_is_known_zero"] is False
        assert blank_profile["buying_cost_total"] is None

        # Behavioural distinction, not just the flag: completeness treats
        # them oppositely.
        assert "buying_cost_total" not in zero_completeness["missing_component_codes"], (
            "a KNOWN zero must count as supplied, not missing: "
            f"{zero_completeness['missing_component_codes']!r}"
        )
        assert "buying_cost_total" in blank_completeness["missing_component_codes"], (
            "a BLANK input must count as missing: "
            f"{blank_completeness['missing_component_codes']!r}"
        )

        # Durable storage distinction, read back over a separate
        # connection: the known-zero row genuinely stores 0.00 (not
        # NULL), the blank order has no row (or a NULL row) at all.
        with psycopg.connect(migrated_database, row_factory=dict_row) as verify_conn:
            zero_row = verify_conn.execute(
                "select buying_cost_per_unit, buying_cost_per_unit_is_known_zero, "
                "buying_cost_total from order_cost_profiles where order_id = %s",
                (order_zero,),
            ).fetchone()
            blank_row = verify_conn.execute(
                "select buying_cost_per_unit, buying_cost_per_unit_is_known_zero, "
                "buying_cost_total from order_cost_profiles where order_id = %s",
                (order_blank,),
            ).fetchone()

        assert zero_row is not None
        assert zero_row["buying_cost_per_unit"] == Decimal("0.00")
        assert zero_row["buying_cost_per_unit_is_known_zero"] is True
        assert zero_row["buying_cost_total"] == Decimal("0.00")

        if blank_row is not None:
            # A row may legitimately not exist at all for a never-touched
            # order (the "virtual blank profile" convention this file's
            # own module docstring states) -- but IF one exists, it must
            # still read as unknown, never as a zero.
            assert blank_row["buying_cost_per_unit"] is None
            assert blank_row["buying_cost_per_unit_is_known_zero"] is False
            assert blank_row["buying_cost_total"] is None


# ---------------------------------------------------------------------------
# AC-04 -- a successful change to one order's cost does not change other
# orders, including other orders carrying the SAME SKU. This is the test
# that catches an implementation keyed by SKU instead of by order_id --
# tempting given BR-02's "one SKU per order" framing, and exactly wrong.
# ---------------------------------------------------------------------------


class TestUS008AC04OtherOrdersCostsDoNotChange:
    def test_us008_ac04_a_successful_change_to_one_orders_cost_does_not_change_other_orders_including_same_sku(
        self, client, migrated_database
    ):
        """AC-04: 'other orders' costs do not change, including other
        orders carrying the same SKU.'

        Mutation this catches: a `buying_cost_per_unit` column keyed (or
        cached) by `(store_id, sku)` instead of `order_id` -- which would
        make saving order B's cost silently overwrite order A's, since
        BR-02 already guarantees at most one SKU per order and a careless
        reading of that rule could tempt exactly this shortcut.

        Expected failure right now: `ModuleNotFoundError: No module named
        'src.costing'`.
        """
        from src.costing import service as costing_service

        with _committed(migrated_database) as c:
            store_id = str(_create_store_db(c, "AC04 Store"))
            manager = _create_user(c, role="manager")
            _assign(c, manager["id"], store_id)
        token = _sign_in(client, manager)

        shared_sku = "SKU-US008-AC04-SHARED"
        order_a = _import_single_order(client, token, store_id, sku=shared_sku, quantity=2)
        order_b = _import_single_order(client, token, store_id, sku=shared_sku, quantity=7)

        scope = _manager_scope(manager["id"], uuid.UUID(store_id))

        with _committed(migrated_database) as conn:
            result_a = costing_service.save_buying_cost(
                conn, scope, actor_id=manager["id"], order_id=order_a,
                buying_cost_per_unit="10.00", operation_id=_idempotency_key(),
            )
        assert result_a["buying_cost_total"] == Decimal("20.00")

        # Order B, same SKU, never touched yet -- must still read blank.
        with psycopg.connect(migrated_database, row_factory=dict_row) as conn:
            from src.costing import repository as costing_repository
            profile_b_before = costing_repository.get_cost_profile(conn, scope, order_b)
        assert profile_b_before["buying_cost_per_unit"] is None, (
            "order B (same SKU) must stay unaffected by order A's save: "
            f"got {profile_b_before['buying_cost_per_unit']!r}"
        )

        with _committed(migrated_database) as conn:
            result_b = costing_service.save_buying_cost(
                conn, scope, actor_id=manager["id"], order_id=order_b,
                buying_cost_per_unit="3.50", operation_id=_idempotency_key(),
            )
        assert result_b["quantity_snapshot"] == 7, (
            "order B's own quantity_snapshot must be its OWN imported "
            f"quantity (7), not order A's (2): got {result_b['quantity_snapshot']!r}"
        )
        assert result_b["buying_cost_total"] == Decimal("24.50")

        # The decisive assertion: order A's own saved cost must be EXACTLY
        # what it was before order B was ever touched.
        with psycopg.connect(migrated_database, row_factory=dict_row) as conn:
            from src.costing import repository as costing_repository
            profile_a_after = costing_repository.get_cost_profile(conn, scope, order_a)
        assert profile_a_after["buying_cost_per_unit"] == Decimal("10.00"), (
            "order A's buying_cost_per_unit must be unchanged by order "
            f"B's later save: got {profile_a_after['buying_cost_per_unit']!r}"
        )
        assert profile_a_after["buying_cost_total"] == Decimal("20.00"), (
            "order A's buying_cost_total must be unchanged by order B's "
            f"later save: got {profile_a_after['buying_cost_total']!r}"
        )
        assert profile_a_after["quantity_snapshot"] == 2


# ---------------------------------------------------------------------------
# Slice-0 invariants and recorded decisions -- required whenever this area
# is touched (AGENTS.md, the brief's own table). Not mapped to a numbered
# AC; each test states which invariant/decision it pins.
# ---------------------------------------------------------------------------


class TestUS008CurrencyProvenance:
    def test_us008_invariant_cost_profile_currency_is_inherited_from_the_order_never_the_store(
        self, client, migrated_database
    ):
        """DECISIONS.md 2026-10-28, 'US-008 currency provenance decided by
        default': the cost profile copies the parent ORDER's own
        `currency_code`, never the store's `reporting_currency_code`, and
        is never set independently.

        Deliberately makes the two differ (store reporting currency USD,
        this one order's own currency EUR) so a coincidental match cannot
        hide an implementation that reads the wrong source.

        Expected failure right now: `ModuleNotFoundError: No module named
        'src.costing'`.
        """
        from src.costing import service as costing_service

        with _committed(migrated_database) as c:
            store_id = str(_create_store_db(c, "Currency Store", reporting_currency_code="USD"))
            manager = _create_user(c, role="manager")
            _assign(c, manager["id"], store_id)
        token = _sign_in(client, manager)

        order_id = _import_single_order(
            client, token, store_id, currency_code="EUR", selling_amount="20.00"
        )

        scope = _manager_scope(manager["id"], uuid.UUID(store_id))
        with _committed(migrated_database) as conn:
            result = costing_service.save_buying_cost(
                conn, scope, actor_id=manager["id"], order_id=order_id,
                buying_cost_per_unit="2.50", operation_id=_idempotency_key(),
            )

        assert result["currency_code"] == "EUR", (
            "the cost profile's currency must be the ORDER's own "
            "currency_code (EUR), never the store's reporting_currency_"
            f"code (USD): got {result['currency_code']!r}"
        )


class TestUS008ExactDecimalNeverFloat:
    def test_us008_invariant_a_float_buying_cost_per_unit_is_rejected_not_silently_coerced(
        self, client, migrated_database
    ):
        """AGENTS.md non-negotiable #5: exact decimal money, never float.
        `src.money.Money` already refuses to be constructed from a
        `float` -- this module must reuse that, not reimplement decimal
        handling a second time (AGENTS.md: "no approved policy is
        reimplemented a second time").

        Expected failure right now: `ModuleNotFoundError: No module named
        'src.costing'`.
        """
        from src.costing import service as costing_service
        from src.money import InvalidMoney

        with _committed(migrated_database) as c:
            store_id = str(_create_store_db(c, "Float Rejection Store"))
            manager = _create_user(c, role="manager")
            _assign(c, manager["id"], store_id)
        token = _sign_in(client, manager)

        order_id = _import_single_order(client, token, store_id)
        scope = _manager_scope(manager["id"], uuid.UUID(store_id))

        with pytest.raises(InvalidMoney):
            with _committed(migrated_database) as conn:
                costing_service.save_buying_cost(
                    conn, scope, actor_id=manager["id"], order_id=order_id,
                    buying_cost_per_unit=1.5,  # a real float, not a string
                    operation_id=_idempotency_key(),
                )


class TestUS008UnassignedStoreIndistinguishableFromNonexistent:
    def test_us008_invariant_an_order_in_an_unassigned_store_is_indistinguishable_from_a_nonexistent_order(
        self, client, migrated_database
    ):
        """B-20-style posture, generalised from store to order: a Manager
        assigned to store A only must get the SAME exception for "save a
        cost against a real order in store B" (not assigned) as for "save
        a cost against an order id that names no row at all" -- a
        different outcome for the two would leak which order ids are
        real to a caller who should not even know that much.

        ``order_in_b_unassigned`` is deliberately NOT created through
        ``POST /imports/orders``: that endpoint is itself correctly
        scope-checked against the Manager's own assignments and would
        404 before this test's own assertions ever ran -- exercising the
        import endpoint's scoping, not ``src.costing``'s. The precondition
        ("a real order exists, in a store this actor is never assigned
        to") is instead built out of band with a direct SQL insert, the
        same pattern ``test_us005b1_import_run_record.py``'s
        ``test_an_out_of_scope_store_raises_out_of_scope_not_a_quiet_
        empty_state`` already uses for the identical need one layer up
        (an unassigned STORE rather than an unassigned order). This is
        the only way to make the unassigned-store branch below actually
        execute instead of failing in fixture setup.

        Expected failure right now: `ModuleNotFoundError: No module named
        'src.costing'`.
        """
        from src.costing import service as costing_service
        from src.scoping import OutOfScope

        with _committed(migrated_database) as c:
            store_a = str(_create_store_db(c, "Scope Store A"))
            store_b = str(_create_store_db(c, "Scope Store B (not assigned)"))
            manager = _create_user(c, role="manager")
            _assign(c, manager["id"], store_a)
            # Deliberately NOT _assign(c, manager["id"], store_b) -- the
            # whole point of this fixture is that the actor never has
            # this store in scope.
            order_in_b_row = _insert_order_direct_bypassing_scope(c, store_b)
        token = _sign_in(client, manager)

        order_in_a = _import_single_order(client, token, store_a)
        order_in_b_unassigned = order_in_b_row["id"]
        nonexistent_order_id = uuid.uuid4()

        scope = _manager_scope(manager["id"], uuid.UUID(store_a))

        with pytest.raises(OutOfScope) as exc_unassigned:
            with _committed(migrated_database) as conn:
                costing_service.save_buying_cost(
                    conn, scope, actor_id=manager["id"],
                    order_id=order_in_b_unassigned,
                    buying_cost_per_unit="1.00", operation_id=_idempotency_key(),
                )

        with pytest.raises(OutOfScope) as exc_nonexistent:
            with _committed(migrated_database) as conn:
                costing_service.save_buying_cost(
                    conn, scope, actor_id=manager["id"],
                    order_id=nonexistent_order_id,
                    buying_cost_per_unit="1.00", operation_id=_idempotency_key(),
                )

        assert type(exc_unassigned.value) is OutOfScope
        assert type(exc_nonexistent.value) is OutOfScope

        # Matching exception TYPE is necessary but not sufficient -- an
        # implementation could still leak which case occurred by attaching
        # a different message/shape for "store not in scope" vs "order
        # does not exist" (e.g. "order exists in another store" only on
        # the unassigned branch). The actual no-disclosure property is
        # about the OBSERVABLE SURFACE of the exception, so pin that
        # directly: in both branches the exception carries EXACTLY the
        # ``order_id`` that was passed in and nothing else -- no extra
        # arg, no distinguishing text appended to one branch but not the
        # other.
        assert exc_unassigned.value.args == (str(order_in_b_unassigned),), (
            "unassigned-store OutOfScope must carry only the order_id, "
            f"got args={exc_unassigned.value.args!r}"
        )
        assert exc_nonexistent.value.args == (str(nonexistent_order_id),), (
            "nonexistent-order OutOfScope must carry only the order_id, "
            f"got args={exc_nonexistent.value.args!r}"
        )
        assert str(exc_unassigned.value) == str(order_in_b_unassigned)
        assert str(exc_nonexistent.value) == str(nonexistent_order_id)

        # Same shape check, phrased structurally rather than by literal
        # value (which necessarily differs -- the two order ids are not
        # equal to each other): both exceptions are built the same way,
        # from a single positional arg, with no additional context object,
        # cause chain, or extra attribute that differs between branches.
        assert len(exc_unassigned.value.args) == len(exc_nonexistent.value.args) == 1
        assert exc_unassigned.value.__cause__ is None
        assert exc_nonexistent.value.__cause__ is None

        # Positive control: the manager's OWN assigned order succeeds, so
        # this test cannot pass merely because every call raises.
        with _committed(migrated_database) as conn:
            own_result = costing_service.save_buying_cost(
                conn, scope, actor_id=manager["id"], order_id=order_in_a,
                buying_cost_per_unit="1.00", operation_id=_idempotency_key(),
            )
        assert own_result["buying_cost_per_unit"] == Decimal("1.00")


class TestUS008OperationIdReplay:
    def test_us008_invariant_a_repeated_operation_id_returns_the_original_result_and_consumes_nothing(
        self, client, migrated_database
    ):
        """`cost-edit-v1` / US-014 AC-03: a write whose outcome is
        unknown (timeout, dropped connection, impatient retry) can be
        replayed with the same `operation_id` and takes effect ONCE. The
        second call below deliberately submits a DIFFERENT value under
        the SAME operation_id -- if the replay were not honoured, this
        would overwrite the first save, which is exactly the double-
        effect this guarantee exists to prevent.

        Expected failure right now: `ModuleNotFoundError: No module named
        'src.costing'`.
        """
        from src.costing import service as costing_service

        with _committed(migrated_database) as c:
            store_id = str(_create_store_db(c, "Replay Store"))
            manager = _create_user(c, role="manager")
            _assign(c, manager["id"], store_id)
        token = _sign_in(client, manager)

        order_id = _import_single_order(client, token, store_id)
        scope = _manager_scope(manager["id"], uuid.UUID(store_id))
        op_id = _idempotency_key()

        with _committed(migrated_database) as conn:
            first = costing_service.save_buying_cost(
                conn, scope, actor_id=manager["id"], order_id=order_id,
                buying_cost_per_unit="5.00", operation_id=op_id,
            )
        assert first["buying_cost_per_unit"] == Decimal("5.00")

        with _committed(migrated_database) as conn:
            second = costing_service.save_buying_cost(
                conn, scope, actor_id=manager["id"], order_id=order_id,
                buying_cost_per_unit="999.99", operation_id=op_id,
            )

        assert second["buying_cost_per_unit"] == Decimal("5.00"), (
            "a replayed operation_id must return the ORIGINAL result "
            "(5.00), never apply the new value (999.99) a second time: "
            f"got {second['buying_cost_per_unit']!r}"
        )

        with psycopg.connect(migrated_database, row_factory=dict_row) as conn:
            persisted = conn.execute(
                "select buying_cost_per_unit from order_cost_profiles "
                "where order_id = %s",
                (order_id,),
            ).fetchone()
        assert persisted["buying_cost_per_unit"] == Decimal("5.00"), (
            "the durably persisted value must still be 5.00 after the "
            f"replay, never 999.99: got {persisted['buying_cost_per_unit']!r}"
        )


class TestUS008AuditEventWritten:
    def test_us008_invariant_a_successful_save_writes_an_audit_event(
        self, client, migrated_database
    ):
        """AGENTS.md: every write is audited. Mutation this catches: a
        save that mutates `order_cost_profiles` with no corresponding
        `audit_events` row at all -- a financial change with no trail.

        Expected failure right now: `ModuleNotFoundError: No module named
        'src.costing'`.
        """
        from src.costing import service as costing_service

        with _committed(migrated_database) as c:
            store_id = str(_create_store_db(c, "Audit Store"))
            manager = _create_user(c, role="manager")
            _assign(c, manager["id"], store_id)
        token = _sign_in(client, manager)

        order_id = _import_single_order(client, token, store_id)
        scope = _manager_scope(manager["id"], uuid.UUID(store_id))
        op_id = _idempotency_key()

        with _committed(migrated_database) as conn:
            costing_service.save_buying_cost(
                conn, scope, actor_id=manager["id"], order_id=order_id,
                buying_cost_per_unit="7.25", operation_id=op_id,
            )

        with psycopg.connect(migrated_database, row_factory=dict_row) as conn:
            events = conn.execute(
                "select * from audit_events where operation_id = %s", (op_id,)
            ).fetchall()

        assert events, (
            "a successful buying-cost save must write an audit_events row "
            f"under its own operation_id ({op_id}); none was found"
        )
        assert len(events) == 1, f"expected exactly one audit event, found {len(events)}"
        event = events[0]
        assert event["actor_id"] == manager["id"]
        assert event["store_id"] == uuid.UUID(store_id)


class TestUS008ManagerCapability:
    def test_us008_invariant_a_manager_may_save_buying_cost_in_an_open_period(
        self, client, migrated_database
    ):
        """walmart-user-stories.md:677, capability matrix: 'Enter and
        correct manual costs in an open period | Owner Yes | Admin Yes |
        Manager Yes.' This story's own actor is a Manager -- confirming
        here that a Manager, scoped only to their own assigned store (not
        Owner), can successfully save a buying cost at all.

        Expected failure right now: `ModuleNotFoundError: No module named
        'src.costing'`.
        """
        from src.costing import service as costing_service

        with _committed(migrated_database) as c:
            store_id = str(_create_store_db(c, "Manager Capability Store"))
            manager = _create_user(c, role="manager")
            _assign(c, manager["id"], store_id)
        token = _sign_in(client, manager)

        order_id = _import_single_order(client, token, store_id, quantity=1)
        scope = _manager_scope(manager["id"], uuid.UUID(store_id))

        with _committed(migrated_database) as conn:
            result = costing_service.save_buying_cost(
                conn, scope, actor_id=manager["id"], order_id=order_id,
                buying_cost_per_unit="6.00", operation_id=_idempotency_key(),
            )

        assert result["buying_cost_per_unit"] == Decimal("6.00")
        assert result["buying_cost_total"] == Decimal("6.00")
