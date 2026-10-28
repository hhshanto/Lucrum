"""G-8 -- deterministic selection of "the most recent import attempt".

Defect fix against already-merged code (US-005 AC-03, delivered via
US-005b-1 and US-005b-2). `src/orders/repository.py`'s
`get_import_run_state` selects the most recent `import_batches` row with
plain `order by started_at desc` -- no tiebreaker. `started_at` is
supplied by the application, so two rows for the same
`(store_id, import_type)` can share a byte-identical instant; Postgres's
row order among ties is UNDEFINED. Observed failing ~1 run in 4-5 under
full-suite load (PROGRESS.md, G-8).

Governing decision -- `DECISIONS.md`, entry dated 2026-10-28 beginning
"G-8 decided under the standing escalation rule" (read in full before
touching this file; not re-derived here):

* The specification is uniformly silent on tiebreaking (`period-v1`
  covers UTC storage/DST, not row ordering; US-005 AC-03 does not define
  "last" under a tie; `database-schema.md` defines no secondary sort
  key).
* `id desc` is explicitly REJECTED as the fix. `import_batches.id` is a
  random `uuid`, so ordering by it is deterministic but ARBITRARY --
  it would report the OLDER attempt as "most recent" about half the
  time, silently.
* The decided fix is a new monotonic `batch_seq bigint generated always
  as identity` column (migration 005), with
  `order by started_at desc, batch_seq desc`. `started_at` remains the
  PRIMARY sort key.

Per this story's brief: do not assert on `batch_seq` by name anywhere --
only the OBSERVABLE behaviour of `get_import_run_state` and the route are
tested here. No test in this file selects or references a `batch_seq`
column; doing so would error on a missing column before the fix lands,
which proves nothing about the actual defect.

-------------------------------------------------------------------------
How a genuine tie is constructed: `import_batches` rows are inserted
DIRECTLY (`_insert_batch_row` below), each via its own connection and its
own `commit()`, with an explicit, hand-picked `started_at` literal shared
byte-for-byte between both rows of a pair. This is the only reliable way
to produce a tie on demand -- the real import path always calls
`datetime.now(timezone.utc)`, which cannot be guaranteed to collide to
the microsecond, and a test that depended on a REAL clock collision would
itself be flaky and would defeat the purpose (this file's own brief,
verbatim). Each pair's two rows are inserted SEQUENTIALLY, each
`commit()`-ed before the next `INSERT` begins, so "inserted first" /
"inserted second" below is a real, observed fact about commit order, not
an assumption.

-------------------------------------------------------------------------
Conventions reused, not imported (the established discipline in this
codebase -- `test_us005b1_import_run_record.py`'s own module docstring:
"each test file in this codebase keeps its own local helpers"):
`client` fixture, `_committed`, `_create_user`, `_create_store_db`,
`_assign`, `_sign_in_json`/`_post_sign_in` (cookie session for the route,
bearer token only where a JSON import call is needed), `_orders_list_
path`, the regex-based HTML helpers (`_find_paired_tags`,
`_generic_elements_by_data_field`), and `_order_payload`/`_import`/
`_new_order_id` -- all copied in reduced form from
`tests/test_us005b1_import_run_record.py` and
`tests/test_us005b2_orders_list_screen.py`.

-------------------------------------------------------------------------
Honesty note on G8-AC-01, recorded here rather than glossed over (see
that test class's own docstring for the full reasoning): within a single,
otherwise-quiet hermetic test process, Postgres's tie-break for exactly
two rows is, empirically (verified against this repository's own running
Postgres before writing this file), governed by physical heap position,
which stays STABLE across repeated reads when nothing else is writing to
the table concurrently. The real flake PROGRESS.md records requires
full-suite concurrent write load to manifest -- a condition this file
cannot reproduce without itself depending on real timing, which the
brief explicitly forbids relying on. G8-AC-01 is written as specified and
kept as a regression guard the eventual fix must also satisfy, but it is
NOT expected to fail against current `main` in this environment; G8-AC-02
is the test in this file that reliably fails for the right reason today.
"""

from __future__ import annotations

import re
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row

from src import main

SIGN_IN_PATH = "/sign-in"


# ---------------------------------------------------------------------------
# helpers, copied locally per this codebase's own convention -- see module
# docstring above.
# ---------------------------------------------------------------------------


def _orders_list_path(store_id) -> str:
    return f"/app/stores/{store_id}/orders"


def _new_order_id(label: str) -> str:
    return f"G8-{label}-{uuid.uuid4().hex[:12]}"


def _create_user(c, *, role="manager", password, mfa_enabled=False, mfa_secret=None):
    from src.access import passwords

    email = f"g8-{role}-{uuid.uuid4().hex}@example.test"
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
            f"G8-{uuid.uuid4().hex[:12]}",
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


@pytest.fixture
def client(migrated_database):
    with TestClient(main.app) as c:
        yield c


def _sign_in_json(client, user) -> str:
    r = client.post(
        "/auth/session", json={"email": user["email"], "password": user["password"]}
    )
    assert r.status_code == 201, r.text
    return r.json()["data"]["token"]


def _post_sign_in(client, email: str, password: str, *, follow_redirects: bool = True):
    return client.post(
        SIGN_IN_PATH,
        data={"email": email, "password": password},
        follow_redirects=follow_redirects,
    )


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


def _import(client, token, store_id, orders: list[dict], *, operation_id=None):
    headers = {
        "Authorization": f"Bearer {token}",
        "Idempotency-Key": operation_id or _idempotency_key(),
    }
    return client.post(
        "/imports/orders",
        json={"store_id": str(store_id), "orders": orders},
        headers=headers,
    )


# ---------------------------------------------------------------------------
# tiny HTML helpers -- copied in reduced form from
# test_us005b2_orders_list_screen.py (no HTML parser is a declared
# dependency in this project).
# ---------------------------------------------------------------------------

_ATTR_RE = re.compile(
    r"""([a-zA-Z_:][-a-zA-Z0-9_:.]*)\s*=\s*"([^"]*)"""
    r"""|([a-zA-Z_:][-a-zA-Z0-9_:.]*)\s*=\s*'([^']*)'"""
)


def _parse_attrs(tag_text: str) -> dict[str, str]:
    attrs: dict[str, str] = {}
    for m in _ATTR_RE.finditer(tag_text):
        if m.group(1) is not None:
            attrs[m.group(1).lower()] = m.group(2)
        else:
            attrs[m.group(3).lower()] = m.group(4)
    return attrs


def _find_paired_tags(html: str, tag: str) -> list[tuple[dict[str, str], str]]:
    pattern = re.compile(rf"<{tag}\b([^>]*)>(.*?)</{tag}>", re.IGNORECASE | re.DOTALL)
    return [(_parse_attrs(m.group(1)), m.group(2)) for m in pattern.finditer(html)]


def _strip_tags(fragment: str) -> str:
    text = re.sub(r"<[^>]+>", " ", fragment)
    return re.sub(r"\s+", " ", text).strip()


def _generic_elements_by_data_field(html: str, field: str) -> list[tuple[dict[str, str], str]]:
    results: list[tuple[dict[str, str], str]] = []
    for tag in ("time", "span", "div", "p", "strong", "b", "dd", "td", "li"):
        for attrs, inner in _find_paired_tags(html, tag):
            if attrs.get("data-field") == field:
                results.append((attrs, _strip_tags(inner)))
    return results


# ---------------------------------------------------------------------------
# the one new helper this file itself needs: a DIRECT, raw insert into
# import_batches with a caller-controlled started_at -- bypassing
# create_import_batch/finish_import_batch entirely so a byte-identical
# started_at can be shared between two rows on demand. Each call is its
# own connection + its own commit, so two sequential calls establish a
# real, observable commit order.
# ---------------------------------------------------------------------------


def _insert_batch_row(
    database_url: str,
    *,
    store_id,
    status_code: str,
    started_at: datetime,
    finished_at: datetime | None,
    import_type: str = "orders",
) -> dict:
    with psycopg.connect(database_url, row_factory=dict_row) as conn:
        row = conn.execute(
            """
            insert into import_batches (
                import_type, store_id, status_code, started_at, finished_at
            )
            values (%(import_type)s, %(store_id)s, %(status_code)s,
                    %(started_at)s, %(finished_at)s)
            returning id, status_code, started_at, finished_at
            """,
            {
                "import_type": import_type,
                "store_id": str(store_id),
                "status_code": status_code,
                "started_at": started_at,
                "finished_at": finished_at,
            },
        ).fetchone()
        conn.commit()
    return row


# ---------------------------------------------------------------------------
# G8-AC-01 -- determinism under a tie
# ---------------------------------------------------------------------------


class TestG8AC01DeterminismUnderATie:
    """G8-AC-01. Two `import_batches` rows for the same
    `(store_id, import_type)` with byte-identical `started_at`. Calling
    `get_import_run_state` repeatedly must return the SAME batch every
    single time -- not usually, not "in practice", every time.

    See this file's own module docstring, "Honesty note", for why this
    specific test is not expected to fail against current `main` in this
    environment: a hermetic, single-process test cannot reproduce the
    full-suite concurrent-write conditions the real flake depends on
    without itself relying on genuine timing, which this story's own
    brief forbids. This test is retained as the literal statement of the
    determinism property and as a regression guard the eventual
    `batch_seq`-based fix must also satisfy -- not as this file's primary
    evidence of the defect (that is G8-AC-02, immediately below).
    """

    def test_g8_ac01_repeated_reads_of_a_tied_pair_return_the_same_batch_every_time(
        self, migrated_database
    ):
        from src.orders import repository
        from src.scoping import StoreScope

        with _committed(migrated_database) as c:
            store_id = _create_store_db(c, "G8 AC01 Tie Store")

        tied_at = datetime(2026, 7, 15, 10, 0, 0, tzinfo=timezone.utc)
        _insert_batch_row(
            migrated_database,
            store_id=store_id,
            status_code="completed",
            started_at=tied_at,
            finished_at=tied_at + timedelta(seconds=1),
        )
        _insert_batch_row(
            migrated_database,
            store_id=store_id,
            status_code="failed",
            started_at=tied_at,
            finished_at=tied_at + timedelta(seconds=2),
        )

        seen_statuses: set[str | None] = set()
        seen_finished_ats: set = set()
        for _ in range(40):
            with psycopg.connect(migrated_database, row_factory=dict_row) as conn:
                state = repository.get_import_run_state(
                    conn, StoreScope.owner(), store_id
                )
            seen_statuses.add(state["last_attempt_status"])
            seen_finished_ats.add(state["last_attempt_finished_at"])

        assert len(seen_statuses) == 1, (
            "get_import_run_state returned DIFFERENT answers for the "
            f"identical tied pair across 40 repeated calls: "
            f"{seen_statuses!r} -- the most recent import attempt must "
            "be a stable fact, not one that changes depending on which "
            "call happens to ask"
        )
        assert len(seen_finished_ats) == 1, (
            f"last_attempt_finished_at was similarly unstable: {seen_finished_ats!r}"
        )


# ---------------------------------------------------------------------------
# G8-AC-02 -- correctness under a tie, not merely stability
# ---------------------------------------------------------------------------


class TestG8AC02CorrectnessUnderATieNotMerelyStability:
    """G8-AC-02. For a tied-`started_at` pair inserted in a KNOWN order --
    the FIRST inserted carrying `completed`, the SECOND inserted carrying
    `failed` -- the LATER-INSERTED row (`failed`) is the genuinely most
    recent attempt and must be what `get_import_run_state` reports.

    Repeated across 10 independent, freshly-created stores: a tiebreak
    that resolves ties by a RANDOM value (the rejected `id desc` fix --
    `import_batches.id` is `uuid primary key default gen_random_uuid()`)
    would get roughly HALF of these right by chance alone, so requiring
    ALL 10 to agree makes a 50%-correct implementation overwhelmingly
    unlikely to pass (p <= 2^-10 if genuinely coin-flip random). The
    CURRENT, undecided-tiebreak implementation is not random at all --
    empirically it consistently prefers the FIRST-inserted (i.e. WRONG,
    stale) row for a fresh tie, which is exactly what this test catches,
    every time, deterministically.
    """

    def test_g8_ac02_the_later_inserted_row_of_a_tie_is_reported_as_the_last_attempt(
        self, migrated_database
    ):
        from src.orders import repository
        from src.scoping import StoreScope

        tied_at = datetime(2026, 7, 15, 11, 0, 0, tzinfo=timezone.utc)
        wrong_answers = []

        for i in range(10):
            with _committed(migrated_database) as c:
                store_id = _create_store_db(c, f"G8 AC02 Tie Store {i}")

            # Inserted FIRST: a genuine success, now stale.
            _insert_batch_row(
                migrated_database,
                store_id=store_id,
                status_code="completed",
                started_at=tied_at,
                finished_at=tied_at + timedelta(seconds=1),
            )
            # Inserted SECOND, the SAME started_at: a genuinely LATER
            # failure -- this is the row "last" must mean.
            _insert_batch_row(
                migrated_database,
                store_id=store_id,
                status_code="failed",
                started_at=tied_at,
                finished_at=tied_at + timedelta(seconds=2),
            )

            with psycopg.connect(migrated_database, row_factory=dict_row) as conn:
                state = repository.get_import_run_state(
                    conn, StoreScope.owner(), store_id
                )

            if state["last_attempt_status"] != "failed":
                wrong_answers.append((i, state["last_attempt_status"]))

        assert not wrong_answers, (
            "the LATER-inserted row of a tied pair ('failed') must be "
            "reported as the last attempt in every one of 10 independent "
            f"stores; got the WRONG (earlier-inserted, stale) answer in "
            f"{len(wrong_answers)} of 10: {wrong_answers!r} -- this is "
            "exactly the shape a random-UUID tiebreak would get right "
            "only about half the time, and the current no-tiebreak "
            "implementation gets wrong consistently"
        )


# ---------------------------------------------------------------------------
# G8-AC-03 -- `started_at` still dominates the sequence
# ---------------------------------------------------------------------------


class TestG8AC03StartedAtStillDominatesTheSequence:
    """G8-AC-03. A row inserted SECOND but carrying an EARLIER
    `started_at` than the first must NOT win merely because it was
    inserted later (the guard against "just order by insertion sequence
    alone", and against a future `batch_seq`-only ordering that drops
    `started_at`). The row with the LATER `started_at` -- the one
    inserted FIRST here -- must still be reported.

    This already holds today: plain `order by started_at desc` has no
    tie here to get wrong (the two `started_at` values are genuinely
    different), so this test is a PINNING/regression guard, not evidence
    of the G-8 defect itself -- recorded honestly, per this file's own
    module docstring convention, rather than presented as something it
    is not. It must keep passing once `batch_seq` is introduced
    (`order by started_at desc, batch_seq desc` keeps `started_at`
    primary); it would fail immediately if a future "simplification"
    dropped `started_at` from the ordering, which is exactly the
    regression this criterion exists to catch.
    """

    def test_g8_ac03_a_later_started_at_wins_even_though_inserted_first(
        self, migrated_database
    ):
        from src.orders import repository
        from src.scoping import StoreScope

        with _committed(migrated_database) as c:
            store_id = _create_store_db(c, "G8 AC03 Started At Dominates Store")

        later_started_at = datetime(2026, 7, 15, 12, 0, 0, tzinfo=timezone.utc)
        earlier_started_at = datetime(2026, 7, 15, 9, 0, 0, tzinfo=timezone.utc)

        # Inserted FIRST, but its OWN started_at is LATER.
        first_row = _insert_batch_row(
            migrated_database,
            store_id=store_id,
            status_code="completed",
            started_at=later_started_at,
            finished_at=later_started_at + timedelta(seconds=5),
        )
        # Inserted SECOND, but its OWN started_at is EARLIER.
        _insert_batch_row(
            migrated_database,
            store_id=store_id,
            status_code="failed",
            started_at=earlier_started_at,
            finished_at=earlier_started_at + timedelta(seconds=5),
        )

        with psycopg.connect(migrated_database, row_factory=dict_row) as conn:
            state = repository.get_import_run_state(
                conn, StoreScope.owner(), store_id
            )

        assert state["last_attempt_status"] == "completed", (
            "the row with the LATER started_at (inserted FIRST here) "
            f"must still be reported as the most recent attempt: got "
            f"{state['last_attempt_status']!r}, expected 'completed' -- "
            "insertion/sequence order must never override started_at"
        )
        assert state["last_attempt_finished_at"] == first_row["finished_at"], (
            "the reported finished_at must belong to the LATER-started_at "
            "batch, not the later-INSERTED one"
        )


# ---------------------------------------------------------------------------
# G8-AC-04 -- the end-to-end surface the flake actually appeared on
# ---------------------------------------------------------------------------


class TestG8AC04EndToEndRouteDeterminism:
    """G8-AC-04. Through `GET /app/stores/{store_id}/orders`
    (`tests/test_us005b2_orders_list_screen.py`'s own route), with two
    tied-`started_at` batches for the store, the rendered
    `last-attempt-status` (`data-status-code`) must match what
    `get_import_run_state` selects directly, and must be the same value
    across repeated requests -- the actual surface the flake was observed
    on (PROGRESS.md: this file and `test_us005b2_orders_list_screen.py`
    both flipped under full-suite load).
    """

    def test_g8_ac04_the_rendered_last_attempt_status_is_correct_and_stable_across_repeated_requests(
        self, client, migrated_database
    ):
        from src.orders import repository
        from src.scoping import StoreScope

        with _committed(migrated_database) as c:
            store_id = str(_create_store_db(c, "G8 AC04 Route Tie Store"))
            manager = _create_user(c, role="manager", password="g8-ac04-manager-pw")
            _assign(c, manager["id"], store_id)

        tied_at = datetime(2026, 7, 15, 13, 0, 0, tzinfo=timezone.utc)
        # Inserted FIRST: a genuine success, now stale.
        _insert_batch_row(
            migrated_database,
            store_id=store_id,
            status_code="completed",
            started_at=tied_at,
            finished_at=tied_at + timedelta(seconds=1),
        )
        # Inserted SECOND, same started_at: a genuinely LATER failure.
        _insert_batch_row(
            migrated_database,
            store_id=store_id,
            status_code="failed",
            started_at=tied_at,
            finished_at=tied_at + timedelta(seconds=2),
        )

        _post_sign_in(client, manager["email"], manager["password"])

        rendered_codes: set[str | None] = set()
        for _ in range(10):
            page = client.get(_orders_list_path(store_id), follow_redirects=True)
            assert page.status_code == 200, page.text[:500]
            status_elements = _generic_elements_by_data_field(
                page.text, "last-attempt-status"
            )
            assert status_elements, (
                "no element carrying data-field=\"last-attempt-status\" "
                f"was rendered: {page.text[:2000]!r}"
            )
            attrs, _text = status_elements[0]
            rendered_codes.add(attrs.get("data-status-code"))

        assert len(rendered_codes) == 1, (
            "the rendered last-attempt-status flickered across repeated "
            f"requests for the SAME tied data: {rendered_codes!r}"
        )
        rendered_code = next(iter(rendered_codes))

        with psycopg.connect(migrated_database, row_factory=dict_row) as conn:
            direct_state = repository.get_import_run_state(
                conn, StoreScope.owner(), uuid.UUID(store_id)
            )

        assert rendered_code == direct_state["last_attempt_status"], (
            "the rendered page and get_import_run_state must agree on "
            f"which batch is the most recent attempt: rendered="
            f"{rendered_code!r} repository="
            f"{direct_state['last_attempt_status']!r}"
        )
        assert rendered_code == "failed", (
            "the LATER-inserted row ('failed') is the genuinely most "
            f"recent attempt and must be what the screen renders: got "
            f"{rendered_code!r}"
        )


# ---------------------------------------------------------------------------
# N-02 -- pin that `last-attempt-status` is present for a `completed`
# batch, not only for `failed`/`partial`. Test-only; requires ZERO
# production change. If this fails against current `main`, that is a NEW
# defect to report, not something to adjust here (this story's own
# constraint).
# ---------------------------------------------------------------------------


class TestN02LastAttemptStatusPresentForCompletedBatch:
    def test_n02_last_attempt_status_element_is_present_for_a_completed_batch(
        self, client, migrated_database
    ):
        with _committed(migrated_database) as c:
            store_id = str(_create_store_db(c, "N02 Completed Status Store"))
            manager = _create_user(c, role="manager", password="n02-manager-pw")
            _assign(c, manager["id"], store_id)
        token = _sign_in_json(client, manager)

        order_id = _new_order_id("n02-success")
        r = _import(client, token, store_id, [_order_payload(walmart_order_id=order_id)])
        assert r.status_code == 201, r.text

        _post_sign_in(client, manager["email"], manager["password"])
        page = client.get(_orders_list_path(store_id), follow_redirects=True)
        assert page.status_code == 200, page.text[:500]

        status_elements = _generic_elements_by_data_field(
            page.text, "last-attempt-status"
        )
        assert status_elements, (
            "no element carrying data-field=\"last-attempt-status\" was "
            "found for a store whose most recent (and only) attempt "
            f"succeeded: {page.text[:2000]!r}"
        )
        attrs, _text = status_elements[0]
        assert attrs.get("data-status-code") == "completed", (
            f"expected the raw status 'completed' to be rendered: {attrs!r}"
        )


# ---------------------------------------------------------------------------
# N-03 -- pin that `order-last-imported-at` is ABSENT for a store with
# zero orders. Test-only; requires ZERO production change.
# ---------------------------------------------------------------------------


class TestN03OrderLastImportedAtAbsentForZeroOrders:
    def test_n03_order_last_imported_at_element_is_absent_for_a_store_with_zero_orders(
        self, client, migrated_database
    ):
        with _committed(migrated_database) as c:
            store_id = str(_create_store_db(c, "N03 Zero Orders Store"))
            manager = _create_user(c, role="manager", password="n03-manager-pw")
            _assign(c, manager["id"], store_id)

        _post_sign_in(client, manager["email"], manager["password"])
        page = client.get(_orders_list_path(store_id), follow_redirects=True)
        assert page.status_code == 200, page.text[:500]

        order_level_elements = _generic_elements_by_data_field(
            page.text, "order-last-imported-at"
        )
        assert not order_level_elements, (
            "a store with ZERO orders must render NO "
            "order-last-imported-at element -- unknown is not zero "
            "(AGENTS.md #4): there is no instant to report, so nothing "
            f"should be fabricated: {order_level_elements!r}"
        )
