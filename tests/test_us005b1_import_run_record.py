"""US-005b-1 -- Import run visibility and freshness, BACKEND HALF ONLY.

docs/walmart-user-stories.md:791, US-005 AC-03 (verbatim):

    AC-03: Given a failed refresh, when viewing the list, then the last
    successful update and failure are visible; stale data is not labeled
    current.

AC-03 was split by the Owner into a backend half (US-005b-1, this file) and
a screen half (US-005b-2, not started). This file tests ONLY the durable
recording of import runs and the scope-filtered, queryable state a future
screen will read. **No test here renders a template, hits
``src/web/routes.py``, or asserts on HTML** -- that is explicitly someone
else's story (PROGRESS.md, 2026-10-28, "G-6 closed by the Owner": a real
rendered screen is coming, but it is US-005b-2's job to build it).

-------------------------------------------------------------------------
Established facts, verified against the running code, not re-derived here
(reproduced so this file can be checked against them directly):

* ``import_batches`` and ``import_errors`` are fully specified at
  ``database-schema.md:967-998`` but NO migration has ever created them --
  confirmed by search, zero hits in ``src/``. Every test touching either
  table is expected to fail right now with
  ``psycopg.errors.UndefinedTable: relation "import_batches" does not
  exist`` (or the equivalent for ``import_errors``), not a collection-time
  error -- the test FILE imports cleanly; only the test BODIES reach
  missing database objects or missing Python attributes.
* ``raw_import_records`` is documented as optional (``database-schema.md:
  1000``) and is deliberately OUT OF SCOPE for this file -- no test
  requires it to exist.
* ``orders.last_imported_at`` already exists and is already written on
  every import (``src/orders/service.py:264``) -- this file does not
  retest that; it is AC-01/02 ground already covered by
  ``test_us005a_import_orders.py``.
* The import entry point is unchanged: ``POST /imports/orders`` in
  ``src/main.py``, calling ``src.orders.service.import_orders``. This
  file instruments the SAME endpoint and reads the new tables/functions
  it must populate as a side effect -- no new HTTP endpoint is invented
  for writing.
* ``BR-02`` (``walmart-user-stories.md:41``): "An order carries exactly
  one SKU... There are no order line items." ``order_items``/
  ``order_lines`` must never exist (``database-schema.md:80``,
  ``INVARIANTS.md:44`` DB-07, ``SC-09``). This is why the multi-SKU
  conflict below is modelled as TWO ``OrderImportItem``s sharing one
  ``walmart_order_id`` in a single request body, not as a richer line-item
  structure -- there is no documented container for a "multi-line order"
  at all, which is exactly the shape of the real contradiction
  ``docs/walmart/orders-v3.md:208-229`` surfaced.

-------------------------------------------------------------------------
This file's own reasoned, disclosed inventions (nothing here is derived
from a document; each is a judgement this file commits to and states
plainly, the same discipline ``OrderImportRequest``'s own docstring
already applies to its unspecified body shape):

1. **The read surface is a query-layer function, not a new HTTP route.**
   ``src.orders.repository.get_import_run_state(conn, scope, store_id, *,
   import_type="orders")`` -> a dict with exactly these four keys:
   ``never_imported`` (bool), ``last_success_at`` (datetime | None),
   ``last_attempt_status`` (str | None), ``last_attempt_finished_at``
   (datetime | None). Scope-checked the same way ``stores_repository.
   get_store`` is -- ``OutOfScope`` for a store outside the caller's
   scope, raised from SQL composed with ``scope.sql_filter``, never from
   a row fetched then discarded (the F-22 discipline). The exact key set
   is pinned (tested with set-equality, not just presence) specifically
   so a later implementation cannot slip in a fabricated "is_current" /
   "is_fresh" field alongside the four honest ones -- AC-03's own "stale
   data is not labeled current" clause is enforced structurally here, not
   just behaviourally.
2. **"A failed run" (AC-03.2, for this story) is the within-batch
   multi-SKU conflict (B-18), not a vehicle invented separately.**
   PROGRESS.md's own 2026-10-28 session note draws this connection
   explicitly: "'Flag it somewhere' and AC-03's 'failure... visible' turn
   out to need the same infrastructure." A batch whose ENTIRE content is
   a conflicting pair (no item that could independently succeed) raises
   before any row is written -- G-7's own narrow rule from the prior
   spec-oracle brief: "a batch that raises before any row is written is
   failed; anything else is out of scope for AC-03 specifically." That is
   exactly the shape used for every AC-03.2/AC-03.3/AC-03.4 fixture in
   this file. The MIXED-batch case (a valid order alongside a conflicting
   pair) is deliberately kept to the B-18.1 tests only, and its overall
   batch status_code is NOT pinned to one value (neither "failed" nor
   "partial") for exactly the reason G-7 names: that labeling question is
   out of scope here.
3. **B-18's "reject whole" is pinned at the row level: zero rows for the
   REJECTED order's ``walmart_order_id``.** The Owner's ruling named the
   *order*, not the batch (PROGRESS.md, 2026-10-28): a valid, unrelated
   order elsewhere in the same batch must still commit. This file states
   that decision in a test, not just a comment, per the project's own
   rule that "a comment explaining why a test avoids an input is a defect
   report" (US-003 lesson) -- the decision is tested, not merely narrated.
4. **HTTP status code for the whole-batch-failure case is NOT pinned to
   one exact value** -- same discipline ``test_ac01_a_non_fulfillment_
   value_is_rejected_not_silently_stored`` already uses in
   ``test_us005a_import_orders.py``: what is pinned is that it is never
   2xx, and that the response body visibly carries something (an
   ``errors`` list or a ``detail`` key) -- never a bare empty
   success-shaped envelope.
5. **No staleness window, threshold, or "stale after N hours" policy
   value is tested or assumed anywhere in this file.** Re-verified before
   writing this file (PROGRESS.md's own session note is explicit: no
   ``period-v1`` clause, no policy table, no config row exists for this).
   AC-03's text requires only that data is "not labeled current" after a
   failure -- a negative constraint plus the presence of both facts
   (last success, latest failure), never a computed time-based boolean.
   Inventing a threshold here would force a policy value into existence
   that violates AGENTS.md non-negotiable #3 ("policy values come from
   tables, never constants"). If this project later adds a real
   staleness policy, it is a DIFFERENT, additive story -- not a
   retroactive edit to these tests.

-------------------------------------------------------------------------
B-18.2 (the CROSS-batch SKU disagreement -- a later import whose ``sku``
disagrees with an already-stored row for the same ``(store_id,
walmart_order_id)``) is a genuinely undecided business question, per the
Owner's own B-18 ruling being phrased around the WITHIN-batch case only.
One test below is written in full and then marked ``@pytest.mark.skip``,
citing a new blocker, **B-19** -- not un-skipped, not resolved here. See
that test's own docstring for the live defect it would otherwise expose
(``src/orders/repository.py:91``, ``sku = excluded.sku``).

-------------------------------------------------------------------------
Conventions carried over from ``test_us005a_import_orders.py`` (not
imported from it -- each test file in this codebase keeps its own local
helpers, the same way ``test_us002_select_store.py`` does not import from
``test_us001_sign_in.py``): a decoy store/user wherever scoping is
asserted (F-23 hermeticity discipline); 404 never confused with 403;
money/identifiers read back from a second, committed connection; the
SQL-spy pattern for proving a scope predicate is composed into SQL rather
than checked after the fetch (F-22, copied from ``test_us002_select_
store.py:552-654``, adapted to the new read function).
"""

from __future__ import annotations

import uuid
from contextlib import contextmanager
from datetime import datetime, timezone

import psycopg
import pytest
import pyotp
from fastapi.testclient import TestClient
from psycopg.rows import dict_row

from src import main

TOTP_TEST_SECRET = "JBSWY3DPEHPK3PXP"


# ---------------------------------------------------------------------------
# helpers (local to this file, deliberately not shared with
# test_us005a_import_orders.py)
# ---------------------------------------------------------------------------


def _new_order_id(label: str) -> str:
    return f"US005B1-{label}-{uuid.uuid4().hex[:12]}"


def _totp_code(secret: str = TOTP_TEST_SECRET) -> str:
    return pyotp.TOTP(secret).now()


def _create_user(c, *, role, password, mfa_enabled=None, mfa_secret=None):
    from src.access import passwords

    if mfa_enabled is None:
        mfa_enabled = role in ("owner", "admin")
    if mfa_enabled and mfa_secret is None:
        mfa_secret = TOTP_TEST_SECRET

    email = f"us005b1-{role}-{uuid.uuid4().hex}@example.test"
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
            f"US005B1-{uuid.uuid4().hex[:12]}",
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


def _conflicting_pair(order_id: str) -> list[dict]:
    """Two order lines sharing one ``walmart_order_id`` with different
    ``sku`` values -- the within-batch shape of the real contradiction
    ``docs/walmart/orders-v3.md:208-229`` found (two line items, two SKUs,
    one ``purchaseOrderId``), modelled the only way the documented request
    shape allows: two items in one ``orders`` list.
    """
    return [
        _order_payload(walmart_order_id=order_id, sku="SKU-CONFLICT-A"),
        _order_payload(walmart_order_id=order_id, sku="SKU-CONFLICT-B"),
    ]


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


def _count_orders(url: str, store_id, walmart_order_id: str) -> int:
    with psycopg.connect(url) as c:
        return c.execute(
            "select count(*) from orders where store_id = %s and walmart_order_id = %s",
            (str(store_id), walmart_order_id),
        ).fetchone()[0]


def _fetch_order_row(url: str, store_id, walmart_order_id: str) -> dict | None:
    with psycopg.connect(url, row_factory=dict_row) as c:
        return c.execute(
            "select * from orders where store_id = %s and walmart_order_id = %s",
            (str(store_id), walmart_order_id),
        ).fetchone()


def _fetch_audit_rows(url: str, operation_id: str) -> list[dict]:
    with psycopg.connect(url, row_factory=dict_row) as c:
        return c.execute(
            "select * from audit_events where operation_id = %s", (operation_id,)
        ).fetchall()


def _fetch_batches_for_store(url: str, store_id, import_type: str = "orders") -> list[dict]:
    """Every ``import_batches`` row for a store, oldest first -- read
    directly over a second, committed connection, the same convention
    ``test_us005a_import_orders.py``'s ``_fetch_order_row`` already uses.
    Deliberately bypasses scope (this is a test-verification helper, not
    production code reachable by a request) -- the scope-filtering
    requirement is pinned separately, against ``repository.
    get_import_run_state``, not against this raw-SQL helper.
    """
    with psycopg.connect(url, row_factory=dict_row) as c:
        return c.execute(
            """
            select * from import_batches
            where store_id = %s and import_type = %s
            order by started_at asc
            """,
            (str(store_id), import_type),
        ).fetchall()


def _fetch_errors_for_batch(url: str, import_batch_id) -> list[dict]:
    with psycopg.connect(url, row_factory=dict_row) as c:
        return c.execute(
            "select * from import_errors where import_batch_id = %s",
            (str(import_batch_id),),
        ).fetchall()


def _count_batches_for_store(url: str, store_id, import_type: str = "orders") -> int:
    with psycopg.connect(url) as c:
        return c.execute(
            "select count(*) from import_batches where store_id = %s and import_type = %s",
            (str(store_id), import_type),
        ).fetchone()[0]


# ---------------------------------------------------------------------------
# Schema-level invariants -- direct SQL against import_batches/import_errors,
# independent of any import endpoint or service. Expected failure right now:
# `psycopg.errors.UndefinedTable: relation "import_batches" does not exist`
# (or the information_schema-based assertions report the table/column is
# missing via a plain, readable AssertionError), because no migration
# creates either table yet.
# ---------------------------------------------------------------------------


class TestImportBatchesAndErrorsSchemaInvariants:
    """DDL/CATALOG-shaped tests, using the rolled-back ``conn`` fixture
    directly -- the same pattern ``test_us005a_import_orders.py``'s
    ``TestOrdersSchemaInvariants`` already uses.
    """

    def _insert_batch(self, conn, *, store_id=None, **overrides):
        fields = {
            "import_type": "orders",
            "store_id": store_id,
            "source_name": None,
            "status_code": "completed",
            "imported_by": None,
            "started_at": "2026-07-15 10:00:00+00",
            "finished_at": "2026-07-15 10:00:05+00",
        }
        fields.update(overrides)
        columns = list(fields.keys())
        values = list(fields.values())
        placeholders = ", ".join(["%s"] * len(columns))
        return conn.execute(
            f"insert into import_batches ({', '.join(columns)}) values ({placeholders}) "
            f"returning *",
            values,
        ).fetchone()

    def _insert_error(self, conn, *, import_batch_id, **overrides):
        fields = {
            "row_number": None,
            "external_reference": "ERR-REF",
            "error_code": "TEST_ERROR",
            "error_message": "a schema-level test error",
            "raw_row": None,
        }
        fields.update(overrides)
        columns = ["import_batch_id", *fields.keys()]
        values = [import_batch_id, *fields.values()]
        placeholders = ", ".join(["%s"] * len(columns))
        return conn.execute(
            f"insert into import_errors ({', '.join(columns)}) values ({placeholders}) "
            f"returning *",
            values,
        ).fetchone()

    def test_import_batches_has_the_documented_columns_and_not_null_shape(self, conn):
        """database-schema.md:967-983. Mutation this catches: any NOT NULL
        column declared nullable (silently satisfied by a NULL instead of
        a real value), or a documented nullable column tightened to NOT
        NULL (which would make a store-less import_type like 'stores'
        impossible to record at all).
        """
        rows = conn.execute(
            "select column_name, is_nullable from information_schema.columns "
            "where table_name = 'import_batches'"
        ).fetchall()
        assert rows, "import_batches table does not exist yet"
        by_name = {r["column_name"]: r["is_nullable"] for r in rows}

        for col in ["id", "import_type", "status_code", "started_at"]:
            assert col in by_name, f"missing column: {col}"
            assert by_name[col] == "NO", f"{col} must be NOT NULL"

        for col in ["store_id", "source_name", "imported_by", "finished_at"]:
            assert col in by_name, f"missing column: {col}"
            assert by_name[col] == "YES", f"{col} must be nullable"

        for col in ["row_count", "success_count", "error_count"]:
            assert col in by_name, f"missing column: {col}"
            assert by_name[col] == "NO", f"{col} must be NOT NULL (defaults to 0)"

    def test_import_batches_counts_default_to_zero(self, conn, store_id):
        """Mutation this catches: counts declared with no default at all,
        which would force every caller to specify them explicitly or fail
        -- the documented default is 0, not NULL (these are bookkeeping
        counters, not the financial 'unknown is not zero' fields; a
        default of 0 here is correct, unlike a cost or a recovered
        amount).
        """
        row = self._insert_batch(conn, store_id=store_id)
        assert row["row_count"] == 0
        assert row["success_count"] == 0
        assert row["error_count"] == 0

    def test_import_batches_started_at_and_finished_at_are_timestamptz(self, conn):
        """UTC storage discipline (AGENTS.md: conversion happens at the
        edges, storage is always UTC). Mutation this catches: either
        column declared as a bare ``timestamp`` (no time zone), which
        would silently accept and store a naive, ambiguous instant.
        """
        rows = conn.execute(
            "select column_name, data_type from information_schema.columns "
            "where table_name = 'import_batches' "
            "and column_name in ('started_at', 'finished_at')"
        ).fetchall()
        assert rows, "import_batches table does not exist yet"
        by_name = {r["column_name"]: r["data_type"] for r in rows}
        assert by_name.get("started_at") == "timestamp with time zone"
        assert by_name.get("finished_at") == "timestamp with time zone"

    def test_import_batches_status_code_accepts_each_documented_value(
        self, conn, store_id
    ):
        """database-schema.md:977. Mutation this catches: a CHECK
        constraint too narrow (rejects a value it must accept).
        """
        for code in ["pending", "processing", "completed", "failed", "partial"]:
            row = self._insert_batch(conn, store_id=store_id, status_code=code)
            assert row is not None

    def test_import_batches_status_code_rejects_an_undocumented_value(
        self, conn, store_id
    ):
        """Complement of the above. Mutation this catches: no CHECK
        constraint at all (a bare ``text`` column accepting any string).
        """
        with pytest.raises(psycopg.errors.CheckViolation):
            with conn.transaction():
                self._insert_batch(conn, store_id=store_id, status_code="bogus")

    def test_import_batches_import_type_accepts_each_documented_value(self, conn):
        """database-schema.md:974. ``store_id`` is left NULL deliberately
        for 'settlements'/'stores' here -- a store-scoped column must not
        be required for an import type that is not store-scoped.
        """
        for import_type in ["orders", "settlements", "stores"]:
            row = self._insert_batch(conn, store_id=None, import_type=import_type)
            assert row is not None

    def test_import_batches_import_type_rejects_an_undocumented_value(self, conn):
        with pytest.raises(psycopg.errors.CheckViolation):
            with conn.transaction():
                self._insert_batch(conn, store_id=None, import_type="nonsense")

    def test_import_batches_store_id_is_a_nullable_foreign_key(self, conn):
        """Mutation this catches: ``store_id`` declared as a bare
        ``uuid`` with no ``references stores(id)`` -- an unconstrained
        column would silently accept a value naming no real store.
        """
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            with conn.transaction():
                self._insert_batch(conn, store_id=str(uuid.uuid4()))

    def test_import_errors_has_the_documented_columns_and_not_null_shape(
        self, conn, store_id
    ):
        """database-schema.md:985-998."""
        batch = self._insert_batch(conn, store_id=store_id)
        rows = conn.execute(
            "select column_name, is_nullable from information_schema.columns "
            "where table_name = 'import_errors'"
        ).fetchall()
        assert rows, "import_errors table does not exist yet"
        by_name = {r["column_name"]: r["is_nullable"] for r in rows}

        for col in ["id", "import_batch_id", "error_code", "error_message", "created_at"]:
            assert col in by_name, f"missing column: {col}"
            assert by_name[col] == "NO", f"{col} must be NOT NULL"

        for col in ["row_number", "external_reference", "raw_row"]:
            assert col in by_name, f"missing column: {col}"
            assert by_name[col] == "YES", f"{col} must be nullable"

        # A row with every nullable field actually NULL must still insert,
        # proving nullability is real, not just the catalog's opinion.
        err = self._insert_error(
            conn,
            import_batch_id=batch["id"],
            row_number=None,
            external_reference=None,
            raw_row=None,
        )
        assert err is not None

    def test_import_errors_import_batch_id_is_a_required_foreign_key(self, conn):
        """Mutation this catches: ``import_batch_id`` nullable, or with no
        ``references import_batches(id)`` at all -- either would let an
        error row exist with no traceable parent batch, defeating the
        whole point of this table (database-schema.md:992: "Batch").
        """
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            with conn.transaction():
                self._insert_error(conn, import_batch_id=str(uuid.uuid4()))

        with pytest.raises(psycopg.errors.NotNullViolation):
            with conn.transaction():
                self._insert_error(conn, import_batch_id=None)


# ---------------------------------------------------------------------------
# AC-03.1 -- a successful run is durably recorded
# ---------------------------------------------------------------------------


class TestAC031SuccessfulRunIsDurablyRecorded:
    def test_ac03_1_a_successful_import_creates_a_completed_batch_with_counts_and_finished_at(
        self, client, migrated_database
    ):
        """AC-03's first clause: 'the last successful update... [is]
        visible'. A successful `POST /imports/orders` for one order must
        leave exactly one `import_batches` row for that store whose
        `status_code` is the terminal success value ('completed'),
        `finished_at` is set (not still mid-flight / NULL), and whose
        counts describe what actually happened -- one row submitted, one
        succeeded, zero errors.

        Mutation this catches: no batch row created at all (the whole
        point of this story); `finished_at` left NULL on success (makes
        'last successful update' unanswerable); `success_count` not
        incremented (makes the counts lie about what happened).

        Expected failure right now: `psycopg.errors.UndefinedTable:
        relation "import_batches" does not exist` -- the table does not
        exist, so there is nothing for the import to write into yet.
        """
        with _committed(migrated_database) as c:
            store_id = str(_create_store_db(c, "AC03.1 Success Store"))
            owner = _create_user(c, role="owner", password="ac031-owner-pw")
        token = _sign_in(client, owner, with_mfa=True)

        order_id = _new_order_id("ac031")
        before = datetime.now(timezone.utc)
        r = _import(client, token, store_id, [_order_payload(walmart_order_id=order_id)])
        assert r.status_code == 201, r.text
        after = datetime.now(timezone.utc)

        batches = _fetch_batches_for_store(migrated_database, store_id)
        assert len(batches) == 1, (
            f"expected exactly one import_batches row, found {len(batches)}: {batches!r}"
        )
        batch = batches[0]
        assert batch["import_type"] == "orders"
        assert batch["status_code"] == "completed"
        assert batch["finished_at"] is not None
        assert before <= batch["started_at"] <= batch["finished_at"] <= after
        assert batch["row_count"] == 1
        assert batch["success_count"] == 1
        assert batch["error_count"] == 0
        # Traceability (database-schema.md:978, "Actor") -- cheap to pin
        # alongside the rest of this row while it is already being read.
        # Mutation this catches: `imported_by` never set, leaving a batch
        # row with no record of who ran the import.
        assert batch["imported_by"] is not None


# ---------------------------------------------------------------------------
# AC-03.2 -- a failed run is durably recorded and does not advance the
# last-successful marker. The heart of AC-03; tested hard, per the brief.
# ---------------------------------------------------------------------------


class TestAC032FailedRunDoesNotAdvanceLastSuccess:
    def test_ac03_2_a_failed_import_is_recorded_without_moving_the_last_success_marker(
        self, client, migrated_database
    ):
        """Given a store with one earlier SUCCESSFUL import, when a LATER
        import fails entirely (the B-18 whole-batch-conflict shape: two
        items sharing one `walmart_order_id`, different `sku`, nothing
        else in the batch -- G-7's 'raises before any row is written'
        case), then:

        * a NEW `import_batches` row records the failure (status_code
          'failed', finished_at set -- the failure itself completed, it
          did not hang);
        * the EARLIER successful batch is still queryable as the most
          recent success -- its own row is untouched, same id, same
          finished_at;
        * the failure detail is durable in `import_errors`, identifying
          the rejected order;
        * the HTTP response for the failing call is visibly not a
          success (never 2xx; body carries `errors` or `detail`).

        Mutation this catches: the single sharpest one this story exists
        to prevent -- an implementation that, on failure, either silently
        updates the EXISTING success batch's own timestamp/counts instead
        of writing a new row (which would make 'last successful update'
        lie about when the data was last genuinely verified good), or
        that returns 2xx for the whole-batch-conflict case (today's ACTUAL
        behaviour: `repository.upsert_order`'s `ON CONFLICT ... DO UPDATE`
        silently applies both conflicting lines in sequence, the second
        overwriting the first's `sku`, and the endpoint reports 201 --
        this is the live B-18 defect, not a hypothetical one).
        """
        with _committed(migrated_database) as c:
            store_id = str(_create_store_db(c, "AC03.2 Failed Run Store"))
            owner = _create_user(c, role="owner", password="ac032-owner-pw")
        token = _sign_in(client, owner, with_mfa=True)

        # First, a genuine success.
        success_order_id = _new_order_id("ac032-success")
        r1 = _import(
            client, token, store_id, [_order_payload(walmart_order_id=success_order_id)]
        )
        assert r1.status_code == 201, r1.text

        batches_after_success = _fetch_batches_for_store(migrated_database, store_id)
        assert len(batches_after_success) == 1
        success_batch = batches_after_success[0]
        assert success_batch["status_code"] == "completed"

        # Then, a whole-batch failure: nothing in this batch could ever
        # succeed, by construction.
        conflict_order_id = _new_order_id("ac032-conflict")
        r2 = _import(client, token, store_id, _conflicting_pair(conflict_order_id))

        assert r2.status_code not in (200, 201), (
            "a whole-batch conflict must not be reported as success -- "
            f"got {r2.status_code}: {r2.text}"
        )
        body = r2.json()
        assert body.get("errors") or body.get("detail"), (
            f"the failure must be visible in the response body: {body!r}"
        )

        # Neither conflicting line was persisted (B-18: reject whole).
        assert _count_orders(migrated_database, store_id, conflict_order_id) == 0

        batches_after_failure = _fetch_batches_for_store(migrated_database, store_id)
        assert len(batches_after_failure) == 2, (
            "the failed run must create ITS OWN batch row, not reuse or "
            f"mutate the success batch: {batches_after_failure!r}"
        )
        # The earlier success batch is untouched -- same id, same finished_at.
        still_success = next(
            b for b in batches_after_failure if b["id"] == success_batch["id"]
        )
        assert still_success["status_code"] == "completed"
        assert still_success["finished_at"] == success_batch["finished_at"]

        failed_batch = next(
            b for b in batches_after_failure if b["id"] != success_batch["id"]
        )
        assert failed_batch["status_code"] == "failed"
        assert failed_batch["finished_at"] is not None
        assert failed_batch["success_count"] == 0
        assert failed_batch["error_count"] >= 1

        errors = _fetch_errors_for_batch(migrated_database, failed_batch["id"])
        assert errors, "a failed batch must leave a durable import_errors record"
        assert any(conflict_order_id in (e["external_reference"] or "") for e in errors), (
            "the error record must identify WHICH order was rejected, not "
            f"just that something failed: {errors!r}"
        )
        assert all(e["error_message"] for e in errors), "every error needs a human message"


# ---------------------------------------------------------------------------
# AC-03.3 -- stale data is not labeled current
# ---------------------------------------------------------------------------


class TestAC033StaleDataIsNotLabeledCurrent:
    def test_ac03_3_queryable_state_reports_last_success_and_latest_failure_never_as_current(
        self, client, migrated_database
    ):
        """After the AC-03.2 sequence (one success, then one whole-batch
        failure), the queryable import state for the store must report
        BOTH facts together: the last success did NOT move, and the most
        recent attempt failed. It must never claim the store's data is
        current -- enforced here structurally (the returned dict's key
        set is pinned by equality, so a fabricated 'is_current'/'is_
        fresh' field cannot slip in unnoticed) AND behaviourally
        (`last_attempt_status` must read 'failed', never silently fall
        back to the stale 'completed' value from the earlier success).

        No staleness THRESHOLD is asserted anywhere in this test -- see
        this file's own module docstring, point 5. Only the negative
        constraint (never reports as current) and the presence of both
        facts (last success, latest-attempt status) are pinned.

        Mutation this catches: a naive implementation that only ever
        tracks "the last successful batch" and reports ITS status as the
        overall state -- which would make `last_attempt_status` read
        'completed' even though the most recent attempt genuinely failed,
        exactly the "stale data... labeled current" failure AC-03 exists
        to forbid.

        Expected failure right now: `AttributeError: module
        'src.orders.repository' has no attribute 'get_import_run_state'`
        -- this function does not exist yet.
        """
        from src.orders import repository
        from src.scoping import StoreScope

        with _committed(migrated_database) as c:
            store_id = str(_create_store_db(c, "AC03.3 Stale Store"))
            owner = _create_user(c, role="owner", password="ac033-owner-pw")
        token = _sign_in(client, owner, with_mfa=True)

        success_order_id = _new_order_id("ac033-success")
        r1 = _import(
            client, token, store_id, [_order_payload(walmart_order_id=success_order_id)]
        )
        assert r1.status_code == 201, r1.text

        conflict_order_id = _new_order_id("ac033-conflict")
        r2 = _import(client, token, store_id, _conflicting_pair(conflict_order_id))
        assert r2.status_code not in (200, 201), r2.text

        batches = _fetch_batches_for_store(migrated_database, store_id)
        success_batch = next(b for b in batches if b["status_code"] == "completed")
        failed_batch = next(b for b in batches if b["status_code"] == "failed")

        with psycopg.connect(migrated_database, row_factory=dict_row) as conn:
            state = repository.get_import_run_state(
                conn, StoreScope.owner(), uuid.UUID(store_id)
            )

        # Structural guard: exactly these four keys, nothing more. A
        # fabricated currency-claiming field would fail this line, not a
        # behavioural assertion further down -- catching it even if that
        # field happened to be computed "correctly" by some other measure.
        assert set(state.keys()) == {
            "never_imported",
            "last_success_at",
            "last_attempt_status",
            "last_attempt_finished_at",
        }, f"unexpected or missing keys in import run state: {state!r}"

        assert state["never_imported"] is False
        # Last success did NOT advance because of the failed run.
        assert state["last_success_at"] == success_batch["finished_at"]
        # The latest ATTEMPT is reported as failed -- never silently
        # reported as the stale 'completed' success.
        assert state["last_attempt_status"] == "failed"
        assert state["last_attempt_status"] != "completed"
        assert state["last_attempt_finished_at"] == failed_batch["finished_at"]
        # The two timestamps are genuinely different -- proof the state
        # is not simply echoing the success batch twice under two keys.
        assert state["last_attempt_finished_at"] != state["last_success_at"]


# ---------------------------------------------------------------------------
# AC-03.4 -- unknown is not zero
# ---------------------------------------------------------------------------


class TestAC034UnknownIsNotZero:
    def test_ac03_4_never_imported_is_distinct_from_a_successful_and_a_failed_store(
        self, client, migrated_database
    ):
        """Three stores, three distinct observable states:

        * S1 has NEVER been imported at all.
        * S2 has exactly one SUCCESSFUL import, nothing else.
        * S3 has exactly one FAILED import (the whole-batch-conflict
          shape), and NO import has ever succeeded for it.

        AGENTS.md non-negotiable #4: unknown is not zero. S1's
        'never_imported' state must not collapse into S3's 'last attempt
        failed, zero successes' state just because both report
        `last_success_at is None` -- they are different facts ("nothing
        has ever been attempted" vs "something was attempted and did not
        work") and must be told apart by `never_imported` specifically,
        not inferred from `last_success_at` alone.

        Mutation this catches: an implementation that represents "never
        imported" as the SAME dict shape as "imported and failed" (e.g.
        both report `never_imported=False, last_attempt_status=None`), or
        one that uses `COALESCE(last_success_at, <failed_batch>.finished_
        at)`-style logic that fabricates a fake 'last success' out of a
        failure.
        """
        from src.orders import repository
        from src.scoping import StoreScope

        with _committed(migrated_database) as c:
            s1_never_imported = str(_create_store_db(c, "AC03.4 Never Imported Store"))
            s2_success_only = str(_create_store_db(c, "AC03.4 Success Only Store"))
            s3_failed_only = str(_create_store_db(c, "AC03.4 Failed Only Store"))
            owner = _create_user(c, role="owner", password="ac034-owner-pw")
        token = _sign_in(client, owner, with_mfa=True)

        r2 = _import(
            client,
            token,
            s2_success_only,
            [_order_payload(walmart_order_id=_new_order_id("ac034-s2"))],
        )
        assert r2.status_code == 201, r2.text

        r3 = _import(
            client, token, s3_failed_only, _conflicting_pair(_new_order_id("ac034-s3"))
        )
        assert r3.status_code not in (200, 201), r3.text

        with psycopg.connect(migrated_database, row_factory=dict_row) as conn:
            state1 = repository.get_import_run_state(
                conn, StoreScope.owner(), uuid.UUID(s1_never_imported)
            )
            state2 = repository.get_import_run_state(
                conn, StoreScope.owner(), uuid.UUID(s2_success_only)
            )
            state3 = repository.get_import_run_state(
                conn, StoreScope.owner(), uuid.UUID(s3_failed_only)
            )

        # S1 -- never imported. The explicit flag is the ONLY thing that
        # may distinguish this from S3 below; both have last_success_at
        # is None, so that field alone is not enough.
        assert state1["never_imported"] is True
        assert state1["last_success_at"] is None
        assert state1["last_attempt_status"] is None
        assert state1["last_attempt_finished_at"] is None

        # S2 -- a real, known success.
        assert state2["never_imported"] is False
        assert state2["last_success_at"] is not None
        assert state2["last_attempt_status"] == "completed"

        # S3 -- attempted, and failed. Zero successes, but NOT "never
        # imported" -- an attempt genuinely happened.
        assert state3["never_imported"] is False
        assert state3["last_success_at"] is None
        assert state3["last_attempt_status"] == "failed"
        assert state3["last_attempt_finished_at"] is not None

        # The three states must be pairwise distinct as whole dicts --
        # not just on the one field each assertion above happened to
        # check.
        assert state1 != state2
        assert state1 != state3
        assert state2 != state3


# ---------------------------------------------------------------------------
# B-18.1 -- a multi-SKU order is rejected whole, visibly
# ---------------------------------------------------------------------------


class TestB181MultiSkuOrderRejectedWhole:
    def test_b18_1_neither_conflicting_line_is_persisted_and_the_rejection_is_visible(
        self, client, migrated_database
    ):
        """'Reject whole' means ZERO rows for the conflicting
        `walmart_order_id` -- not one row (whichever line happened to be
        applied last). This is the clause most likely to be got wrong,
        because the naive implementation (today's actual behaviour)
        writes the first line via `ON CONFLICT ... DO UPDATE`, then the
        second line overwrites it -- ending with exactly ONE row (the
        LAST sku silently wins), which a weaker test asserting only
        'count != 2' would miss entirely.

        Mutation this catches: an implementation that successfully
        inserts/updates one of the two lines before detecting the
        conflict (today's literal behaviour) -- `_count_orders(...) == 0`
        fails against a count of 1, not 2, which is exactly the trap a
        'no duplicate rows' style assertion would NOT catch.
        """
        with _committed(migrated_database) as c:
            store_id = str(_create_store_db(c, "B18.1 Reject Whole Store"))
            owner = _create_user(c, role="owner", password="b181-owner-pw")
        token = _sign_in(client, owner, with_mfa=True)

        order_id = _new_order_id("b181-wholebatch")
        r = _import(client, token, store_id, _conflicting_pair(order_id))

        assert r.status_code not in (200, 201), (
            f"a multi-SKU conflict must not report success: {r.status_code}: {r.text}"
        )
        assert _count_orders(migrated_database, store_id, order_id) == 0, (
            "neither conflicting line may be persisted -- a count of 1 means "
            "one line silently overwrote the other instead of the whole "
            "order being rejected"
        )

        batches = _fetch_batches_for_store(migrated_database, store_id)
        assert batches, "the rejection must be durably recorded, not a silent skip"
        batch = batches[-1]
        errors = _fetch_errors_for_batch(migrated_database, batch["id"])
        assert errors, "the rejection must be visible in import_errors, not just an HTTP body"
        assert any(order_id in (e["external_reference"] or "") for e in errors)

    def test_b18_1_a_valid_order_elsewhere_in_the_same_batch_still_commits(
        self, client, migrated_database
    ):
        """The Owner's ruling (PROGRESS.md, 2026-10-28) named the ORDER,
        not the batch: 'reject the whole order... never split, never
        partially imported'. It says nothing about every OTHER order in
        the same import batch. This test states and pins the narrowest
        defensible reading explicitly, rather than assuming all-or-
        nothing for the whole batch: a batch containing one genuinely
        valid order alongside one conflicting pair must still commit the
        valid order. Reasoning: an importer processes one Walmart
        catalogue page of (typically hundreds of) orders at a time: a data
        problem on ONE order's PO must not make every other, unrelated
        order in the same page invisible until the batch is manually
        resubmitted -- that would turn one rare data defect (0.17% of
        orders, per docs/walmart/orders-v3.md:222) into an outage for
        every store's entire import run. If the Owner later rules the
        stricter all-or-nothing-per-batch reading instead, THIS test is
        the one to revisit, not AC-01/AC-02/AC-03's own content.

        The overall HTTP status code and the batch's own `status_code`
        for this MIXED case are deliberately NOT pinned to one value
        (neither 'failed' nor 'partial') -- this file's own module
        docstring, point 2, explains why: that labeling question is
        explicitly out of scope for AC-03 here (G-7). What IS pinned: the
        valid order is persisted, the conflicting order is not, and the
        response visibly names the problem.

        Mutation this catches: an implementation that takes the
        all-or-nothing shortcut (one failing SKU conflict rolls back the
        ENTIRE transaction, including unrelated valid orders) -- the
        valid order's row would not exist, failing this test outright.
        """
        with _committed(migrated_database) as c:
            store_id = str(_create_store_db(c, "B18.1 Mixed Batch Store"))
            owner = _create_user(c, role="owner", password="b181-mixed-owner-pw")
        token = _sign_in(client, owner, with_mfa=True)

        valid_order_id = _new_order_id("b181-mixed-valid")
        conflict_order_id = _new_order_id("b181-mixed-conflict")
        batch = [
            _order_payload(walmart_order_id=valid_order_id, sku="SKU-VALID"),
            *_conflicting_pair(conflict_order_id),
        ]

        r = _import(client, token, store_id, batch)

        # The valid order commits regardless of the conflicting pair
        # elsewhere in the same request.
        assert _count_orders(migrated_database, store_id, valid_order_id) == 1, (
            "a valid, unrelated order in the same batch must still commit"
        )
        valid_row = _fetch_order_row(migrated_database, store_id, valid_order_id)
        assert valid_row["sku"] == "SKU-VALID"

        # The conflicting order is still rejected whole.
        assert _count_orders(migrated_database, store_id, conflict_order_id) == 0

        # The problem is visible somewhere in the response, regardless of
        # which exact status code accompanies a mixed outcome.
        body = r.json()
        assert body.get("errors"), (
            f"a batch with ANY rejected order must carry a visible error, "
            f"even when other orders in it succeeded: {body!r}"
        )


# ---------------------------------------------------------------------------
# B-18.2 -- SKIPPED. Cross-batch SKU disagreement is an undecided business
# question (B-19). Do not un-skip; do not implement a rule for it.
# ---------------------------------------------------------------------------


class TestB182CrossBatchSkuDisagreement:
    @pytest.mark.skip(
        reason=(
            "B-19 (new blocker, undecided): a LATER import whose `sku` "
            "disagrees with the already-stored row for the same "
            "(store_id, walmart_order_id) could be a genuine SKU "
            "correction, or it could be the same BR-02 contradiction "
            "arriving one call later instead of within one batch. The "
            "Owner's B-18 ruling is phrased around the WITHIN-batch case "
            "only (PROGRESS.md, 2026-10-28) and does not cover this. "
            "Today's live, unguarded behaviour -- "
            "src/orders/repository.py:91, `sku = excluded.sku` -- "
            "silently overwrites the stored sku with no error, no "
            "import_errors row, and no visible signal of any kind. This "
            "test is written in full and skipped rather than asserting "
            "either resolution, so the gap stays visible in every test "
            "run instead of inventing a rule nobody made (B-11/B-13 "
            "precedent). Needs an Owner ruling before un-skipping."
        )
    )
    def test_b18_2_a_later_import_disagreeing_on_sku_is_an_undecided_question(
        self, client, migrated_database
    ):
        with _committed(migrated_database) as c:
            store_id = str(_create_store_db(c, "B18.2 Cross-Batch SKU Store"))
            owner = _create_user(c, role="owner", password="b182-owner-pw")
        token = _sign_in(client, owner, with_mfa=True)

        order_id = _new_order_id("b182")
        first = _import(
            client, token, store_id, [_order_payload(walmart_order_id=order_id, sku="SKU-ORIGINAL")]
        )
        assert first.status_code == 201, first.text

        second = _import(
            client, token, store_id, [_order_payload(walmart_order_id=order_id, sku="SKU-DIFFERENT")]
        )

        # Whatever the Owner eventually rules, it must be a DELIBERATE,
        # visible outcome -- not today's silent overwrite. Left
        # unasserted beyond this comment: this test is skipped precisely
        # because what SHOULD happen here is not yet decided.
        assert second.status_code in (200, 409, 422)


# ---------------------------------------------------------------------------
# Write discipline -- the new write paths this story creates (batch rows,
# error rows) are writes, and inherit the project-wide idempotency
# guarantee. F-88's lesson (US-005a audit): no test reusing an
# Idempotency-Key is a decorative test. Proven here by requiring the
# SECOND call under the SAME key to leave the SAME row counts as the
# first, for both the success path and the failure path.
# ---------------------------------------------------------------------------


class TestImportRunRecordingIsIdempotent:
    def test_replaying_a_successful_import_does_not_create_a_second_batch_row(
        self, client, migrated_database
    ):
        """Mutation this catches: a batch row inserted unconditionally on
        every call to the endpoint, bypassing the existing replay
        pre-check short-circuit (`audit.find_by_operation`) that
        `src/orders/service.py` already uses for the `orders` table
        itself -- this test fails if the NEW write path (the batch row)
        was not wired through that same short-circuit.
        """
        with _committed(migrated_database) as c:
            store_id = str(_create_store_db(c, "Idem Success Batch Store"))
            owner = _create_user(c, role="owner", password="idem-success-owner-pw")
        token = _sign_in(client, owner, with_mfa=True)

        op_id = _idempotency_key()
        order_id = _new_order_id("idem-success")
        payload = [_order_payload(walmart_order_id=order_id)]

        first = _import(client, token, store_id, payload, operation_id=op_id)
        assert first.status_code == 201, first.text
        assert _count_batches_for_store(migrated_database, store_id) == 1

        second = _import(client, token, store_id, payload, operation_id=op_id)
        assert second.status_code in (200, 201), second.text
        assert _count_batches_for_store(migrated_database, store_id) == 1, (
            "an honest retry under the same Idempotency-Key must not "
            "create a second import_batches row"
        )

    def test_replaying_a_failed_import_does_not_double_the_failure_record(
        self, client, migrated_database
    ):
        """The sharper half, for the NEW failure-recording path
        specifically (F-88 applied to this story's own write surface, not
        just inherited from US-005a's success path). An honest retry of
        the SAME whole-batch-conflict payload, under the SAME
        Idempotency-Key, must not double the recorded damage: still
        exactly one failed `import_batches` row, still the same
        `import_errors` row count after the second call as after the
        first.

        Mutation this catches: a failure path that is not wired through
        ANY replay recognition at all (plausible today, since the
        existing pre-check only ever looked at `audit.find_by_operation`
        results written by a SUCCESSFUL prior call) -- every retry of a
        failing import would durably record ANOTHER failed batch and
        ANOTHER set of errors, growing without bound on every client
        retry of what is, from the caller's perspective, one single
        attempt.
        """
        with _committed(migrated_database) as c:
            store_id = str(_create_store_db(c, "Idem Failure Batch Store"))
            owner = _create_user(c, role="owner", password="idem-failure-owner-pw")
        token = _sign_in(client, owner, with_mfa=True)

        op_id = _idempotency_key()
        order_id = _new_order_id("idem-failure")
        payload = _conflicting_pair(order_id)

        first = _import(client, token, store_id, payload, operation_id=op_id)
        assert first.status_code not in (200, 201), first.text
        batches_after_first = _fetch_batches_for_store(migrated_database, store_id)
        assert len(batches_after_first) == 1
        errors_after_first = _fetch_errors_for_batch(
            migrated_database, batches_after_first[0]["id"]
        )
        assert errors_after_first

        second = _import(client, token, store_id, payload, operation_id=op_id)
        assert second.status_code == first.status_code, (
            "a replayed failure must report the SAME outcome as the first "
            f"attempt, not a different one: {first.status_code} vs "
            f"{second.status_code}"
        )

        batches_after_second = _fetch_batches_for_store(migrated_database, store_id)
        assert len(batches_after_second) == 1, (
            "an honest retry of a failing import must not record a SECOND "
            f"failed batch: {batches_after_second!r}"
        )
        errors_after_second = _fetch_errors_for_batch(
            migrated_database, batches_after_second[0]["id"]
        )
        assert len(errors_after_second) == len(errors_after_first), (
            "an honest retry must not duplicate the recorded error rows"
        )


# ---------------------------------------------------------------------------
# Store scoping -- the read must be scope-filtered in SQL, never fetched
# then filtered in Python (F-22, US-002a audit). Reuses the SQL-spy pattern
# from test_us002_select_store.py:552-654, adapted to the new function.
# ---------------------------------------------------------------------------


class TestImportRunStateIsScopeFilteredInSql:
    def test_scope_predicate_is_composed_into_the_sql_not_checked_after_the_fetch(
        self, conn
    ):
        """F-22, applied to the NEW read function this story introduces.
        A rewrite of `get_import_run_state` that selects by `store_id`
        alone, fetches whatever batches exist, and only THEN checks
        `scope.permits(store_id)` in Python before raising `OutOfScope`
        would pass every purely behavioural test in this file (the store
        was still out of the caller's scope in the end) while having
        already read the out-of-scope store's import history into
        memory -- exactly the leak `src/stores/repository.py`'s own
        module docstring describes, applied to a second function that
        touches store-scoped data.

        The spy is local to this test; no production hook is added.
        """
        from src.orders import repository
        from src.scoping import OutOfScope, StoreScope

        row = conn.execute(
            """
            insert into stores (name, walmart_store_key, timezone,
                                reporting_currency_code, managed_from_year,
                                managed_from_month, is_active)
            values (%s, %s, 'America/New_York', 'USD', 2026, 1, true)
            returning id
            """,
            (
                f"US005B1 SQL-layer store {uuid.uuid4().hex[:8]}",
                f"US005B1-SQL-{uuid.uuid4().hex[:12]}",
            ),
        ).fetchone()
        unassigned_store_id = row["id"]

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
                repository.get_import_run_state(conn, scope, unassigned_store_id)
        finally:
            conn.execute = real_execute

        assert captured, "get_import_run_state must query the database, not reason in Python alone"

        where_clause, scope_params = scope.sql_filter("store_id")
        matching = [(sql, params) for sql, params in captured if where_clause in sql]
        assert matching, (
            "the scope predicate was never composed into any statement sent "
            f"to the database; statements sent were: {[s for s, _ in captured]!r}"
        )

        sql, params = matching[0]
        assert params is not None
        for key, value in scope_params.items():
            assert params.get(key) == value

        # F-22, tightened, exactly as test_us002_select_store.py:625-651
        # does: every statement touching `stores` (the table the scope
        # check must be composed against) must itself carry the scope
        # predicate -- an unscoped read followed by a second, decorative
        # scoped one still reads the out-of-scope row before discarding it.
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
            f"predicate composed into it: {unscoped_stores_statements!r}"
        )

    def test_an_out_of_scope_store_raises_out_of_scope_not_a_quiet_empty_state(
        self, conn
    ):
        """BT-08's posture, applied here: a store outside the caller's
        scope must not be answerable at all through this function --
        never a quiet 'never_imported: True'-shaped state that would be
        indistinguishable from a real, in-scope store that genuinely has
        no import history. A positive control (a store the SAME scope
        DOES permit) is included so a stub that always raises
        `OutOfScope` cannot pass this test by accident.
        """
        from src.orders import repository
        from src.scoping import OutOfScope, StoreScope

        permitted_row = conn.execute(
            """
            insert into stores (name, walmart_store_key, timezone,
                                reporting_currency_code, managed_from_year,
                                managed_from_month, is_active)
            values (%s, %s, 'America/New_York', 'USD', 2026, 1, true)
            returning id
            """,
            (
                f"US005B1 Permitted store {uuid.uuid4().hex[:8]}",
                f"US005B1-PERMITTED-{uuid.uuid4().hex[:12]}",
            ),
        ).fetchone()
        permitted_store_id = permitted_row["id"]

        unassigned_row = conn.execute(
            """
            insert into stores (name, walmart_store_key, timezone,
                                reporting_currency_code, managed_from_year,
                                managed_from_month, is_active)
            values (%s, %s, 'America/New_York', 'USD', 2026, 1, true)
            returning id
            """,
            (
                f"US005B1 Unassigned store {uuid.uuid4().hex[:8]}",
                f"US005B1-UNASSIGNED-{uuid.uuid4().hex[:12]}",
            ),
        ).fetchone()
        unassigned_store_id = unassigned_row["id"]

        scope = StoreScope.assigned(
            actor_id=uuid.uuid4(), store_ids=[permitted_store_id]
        )

        # Positive control: the permitted store must be answerable at all,
        # as a never-imported store (no prior writes in this rolled-back
        # transaction touch it).
        control_state = repository.get_import_run_state(conn, scope, permitted_store_id)
        assert control_state["never_imported"] is True

        with pytest.raises(OutOfScope):
            repository.get_import_run_state(conn, scope, unassigned_store_id)
