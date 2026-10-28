"""US-005b-2 -- Orders list screen, the SCREEN HALF of US-005 AC-03.

docs/walmart-user-stories.md:791, US-005 AC-03 (verbatim):

    AC-03: Given a failed refresh, when viewing the list, then the last
    successful update and failure are visible; stale data is not labeled
    current.

US-005b-1 (``tests/test_us005b1_import_run_record.py``, already green)
proved the BACKEND half: ``import_batches``/``import_errors`` durably
record every run, and ``src.orders.repository.get_import_run_state``
answers, per store, whether the store was never imported, when it last
SUCCEEDED, and what the MOST RECENT attempt's status was -- never
collapsing a failure into the stale "completed" status of an earlier
success.

This file is the other half: nothing here re-tests that backend logic.
Every test below renders an actual HTML page and reads it, the same way
``tests/test_ui001_sign_in_screen.py`` reads ``sign_in.html``/``shell.html``.
Nothing here exists yet -- no route, no template -- so every test is
expected to fail on first run; see the session report for which failure
each one produces.

-------------------------------------------------------------------------
Scope fences (spec-oracle's findings, reproduced here so this file can be
checked against them directly, not re-derived):

* **UI-04 has no normative content specification.** The only binding
  references are the traceability row (``walmart-user-stories.md:714``,
  "Orders list") and the two story headers citing it (``:786``, ``:802``).
  The column-group description in ``walmart-store-manager-design.md:
  107-120`` is non-normative vision/UX (rank 5 of 5) and does **not** bind
  this slice -- nothing below asserts a column, a filter, a sort order, or
  any other content from that document.
* **The AC-03 / US-006 boundary is by criterion id, not prose.** US-006
  has not started. Nothing below asserts: AC-01 filters, AC-02 date-basis
  label/reporting timezone, AC-03 empty-store-vs-filter-mismatch or a
  "Clear filters" control, AC-04 filter/sort/pagination/list-position
  restoration. This file tests ONLY the three AC-03 facts named above.

-------------------------------------------------------------------------
B-22 -- CLOSED by the Owner, 2026-10-28, as **"both"** (``DECISIONS.md``):
``database-schema.md:454`` defines ``orders.last_imported_at`` as
"Freshness (US-005 AC-03)" -- a per-ORDER stamp -- and it must be rendered
ALONGSIDE, not instead of, the already-audited per-RUN answer from
``import_batches`` (``database-schema.md:967-984``,
``get_import_run_state``). The original five tests in this file were
written source-agnostic BEFORE this ruling (every timestamp assertion is a
WINDOW check, never an exact equality against one specific column), which
is exactly why they could not catch F-100 (audit finding, same session):
``orders.last_imported_at`` is written in three places
(``src/orders/service.py:530``, ``src/orders/repository.py:97,118``,
migration ``003_orders.sql:160``) and READ nowhere -- only the run-level
fact is rendered. The ``TestF100...`` class below closes that hole with a
test that is source-SPECIFIC by construction: it builds a state where the
order-level and run-level answers genuinely DIFFER (a `partial` batch
updates an order row's own ``last_imported_at`` without advancing
``import_batches``' `last_success_at`, since only a `completed` batch
advances that), so a window check against the WRONG source fails on its
own window bound, not merely on a missing element.

-------------------------------------------------------------------------
G-1 -- an invented, minimal, DISCLOSED route+template+markup shape, since
no document names any of the three for this screen. The implementer must
match this shape; it is an engineering gap being filled, not an Owner
decision (B-12 precedent, PROGRESS.md).

1. **Route:** ``GET /app/stores/{store_id}/orders`` -- an HTML page,
   cookie-session authenticated exactly like the existing ``GET /app``
   shell (``src/web/routes.py``), under the SAME ``/app`` prefix those
   routes already establish. Picked because UI-04 ("Orders list") is
   store-scoped and this project's one existing authenticated page is
   already at ``/app``.
2. **Template:** ``src/templates/orders_list.html``.
3. **Authorization: store-scope-only (B-20, interim, unconfirmed).** No
   capability-matrix row distinguishes "view import history" from
   "trigger an import" (``walmart-user-stories.md:667`` answers *which
   stores*; ``:674`` grants Owner/Admin/Manager the *triggering*, not
   reading). The already-audited ``get_import_run_state`` applies
   store-scope-only and is labelled interim there; this file's one
   authorization test applies the SAME interim rule and cites B-20
   explicitly, rather than looking settled.
4. **Freshness markup contract** -- the minimal, flat (non-nesting) shape
   needed to test AC-03's three clauses without a real HTML parser in this
   suite (none is installed; see ``test_ui001_sign_in_screen.py``'s own
   note on this), and without inventing any UI-04/US-006 content beyond
   what AC-03 itself requires:

   * When the store has at least one successful import:
     ``<time data-field="last-success-at" datetime="...">...</time>``
     -- the ``datetime`` attribute carries an ISO-8601 UTC instant for
     "the last successful update".
   * When the MOST RECENT attempt for the store failed:
     ``<time data-field="last-import-failure" datetime="..." role="alert">
     ...fail...</time>`` -- ``datetime`` is the failed attempt's own
     instant; the element's own text names the failure (contains "fail",
     case-insensitively) so a user, not just a machine, can see it.
   * Both elements, when both are warranted, are present in the SAME
     response to the SAME request -- never only one of the two, and
     never behind a second page/request.
   * Structural+behavioural prohibition ("stale data is not labeled
     current"): the ``last-success-at`` element's own text never claims
     currency (no "current"/"fresh"/"up to date", case-insensitively)
     when the most recent attempt did not succeed; and the page as a
     whole never states an unambiguous currency claim ("up to date",
     "data is current", "fully current") anywhere, under the same
     condition.
   * **F-100 (B-22 closed "both", ``DECISIONS.md`` 2026-10-28)** --
     order-level freshness, read from ``orders.last_imported_at`` and
     NEVER from ``import_batches``:
     ``<time data-field="order-last-imported-at" datetime="...">...
     </time>`` (or any other paired tag -- this contract names the
     ``data-field`` attribute, not a tag). Present whenever the store has
     at least one ``orders`` row, as its OWN element, genuinely distinct
     from ``last-success-at`` -- the two are never the same value
     re-rendered under a second name, and neither is derived from the
     other. The new read-only function this requires in
     ``src/orders/repository.py`` is an explicit, Owner-ruled widening of
     this story's boundary (``DECISIONS.md`` 2026-10-28, "the boundary
     widens, the ruling does not move") -- not a scope violation.
   * **F-101 (G-7 partially closed, ``DECISIONS.md`` 2026-10-28)** -- the
     most recent attempt's raw ``import_batches.status_code`` is
     rendered VERBATIM, never flattened to an invented word:
     ``<... data-field="last-attempt-status" data-status-code="...">
     ...</...>`` carries the exact stored value (one of ``pending`` /
     ``processing`` / ``completed`` / ``failed`` / ``partial``) in
     ``data-status-code``. ``last-import-failure`` (with its "fail"
     text contract above) is reserved for a status that is literally
     ``failed``; a ``partial`` outcome (most rows succeeded) must NOT be
     rendered through that element, and the word "fail"/"failed" must
     not appear anywhere on the page when the most recent attempt was
     ``partial``. The undocumented ``failed``-vs-``partial`` TRANSITION
     rule (which batches become which) stays open (G-7) and is not
     decided by this file -- only that whichever raw value a batch
     actually has is the value rendered.

   Nothing else about the page's markup is pinned -- no column, no table
   structure, no row content for individual orders. That is deliberately
   out of scope (see the fences above).

-------------------------------------------------------------------------
Conventions reused, not reinvented:

* ``client`` fixture, ``_committed``, store/user creation, ``_import``,
  ``_conflicting_pair``, ``_new_order_id``, ``_order_payload`` -- copied
  locally from ``tests/test_us005b1_import_run_record.py`` (that file's own
  stated discipline: each test file keeps its own local helpers rather
  than importing them, the same way ``test_us002_select_store.py`` does
  not import from ``test_us001_sign_in.py``).
* Cookie-based sign-in -- posting ``email``/``password`` to ``POST
  /sign-in`` and letting ``TestClient``'s own cookie jar carry the session,
  exactly as ``test_ui001_sign_in_screen.py``'s ``_post_sign_in`` does.
  Manager accounts are used throughout (not Owner/Admin) specifically so
  this form post never needs an MFA code -- ``src/web/routes.py``'s own
  docstring discloses that the HTML sign-in form does not collect one yet;
  a Manager's ``mfa_enabled`` is optional/off by default, so this gap never
  blocks this file. Manager is also already a role the capability matrix
  grants "Import orders" to (``walmart-user-stories.md:674``), so the same
  account can both create the import fixtures (via the JSON API, bearer
  token from ``POST /auth/session``) and browse the new screen (via the
  cookie).
* Regex-based HTML helpers -- ``_find_paired_tags``, ``_strip_tags`` --
  copied locally in the same reduced form ``test_ui001_sign_in_screen.py``
  already uses, not imported (that file's own module docstring, point 5:
  no HTML parser is a declared dependency).
"""

from __future__ import annotations

import re
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row

from src import main

# ---------------------------------------------------------------------------
# Invented route (G-1, point 1 above). Change this constant, not the
# assertions, if the Owner or the implementer names a different path.
# ---------------------------------------------------------------------------


def _orders_list_path(store_id) -> str:
    return f"/app/stores/{store_id}/orders"


SIGN_IN_PATH = "/sign-in"


# ---------------------------------------------------------------------------
# local helpers -- copied, not imported, per this file's own module
# docstring above
# ---------------------------------------------------------------------------


def _new_order_id(label: str) -> str:
    return f"US005B2-{label}-{uuid.uuid4().hex[:12]}"


def _create_user(c, *, role="manager", password, mfa_enabled=False, mfa_secret=None):
    from src.access import passwords

    email = f"us005b2-{role}-{uuid.uuid4().hex}@example.test"
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
            f"US005B2-{uuid.uuid4().hex[:12]}",
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
    """A within-batch multi-SKU conflict (B-18 shape) -- the same
    'raises before any row is written' failure fixture
    ``test_us005b1_import_run_record.py`` uses for every AC-03 failure
    case, reused here as the simplest reliable way to produce a genuinely
    FAILED ``import_batches`` row for this screen to render.
    """
    return [
        _order_payload(walmart_order_id=order_id, sku="SKU-CONFLICT-A"),
        _order_payload(walmart_order_id=order_id, sku="SKU-CONFLICT-B"),
    ]


def _idempotency_key() -> str:
    return str(uuid.uuid4())


@pytest.fixture
def client(migrated_database):
    with TestClient(main.app) as c:
        yield c


def _sign_in_json(client, user) -> str:
    """Bearer token via the JSON API -- used only to drive the import
    fixtures (``POST /imports/orders``), never to browse the new screen.
    """
    r = client.post(
        "/auth/session", json={"email": user["email"], "password": user["password"]}
    )
    assert r.status_code == 201, r.text
    return r.json()["data"]["token"]


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


def _post_sign_in(client, email: str, password: str, *, follow_redirects: bool = True):
    """Cookie-session sign-in through the actual HTML form, exactly as
    ``test_ui001_sign_in_screen.py``'s own helper of the same name does --
    the client's own cookie jar then carries the session for every
    subsequent request on this same ``client`` instance.
    """
    return client.post(
        SIGN_IN_PATH,
        data={"email": email, "password": password},
        follow_redirects=follow_redirects,
    )


# ---------------------------------------------------------------------------
# tiny HTML helpers -- copied in reduced form from
# test_ui001_sign_in_screen.py (no HTML parser is a declared dependency)
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


def _time_elements_by_field(html: str, field: str) -> list[tuple[dict[str, str], str]]:
    """Every ``<time data-field="{field}" ...>...</time>`` -- attrs plus
    stripped inner text -- per this file's own invented markup contract
    (G-1, point 4).
    """
    results = []
    for attrs, inner in _find_paired_tags(html, "time"):
        if attrs.get("data-field") == field:
            results.append((attrs, _strip_tags(inner)))
    return results


def _generic_elements_by_data_field(html: str, field: str) -> list[tuple[dict[str, str], str]]:
    """Like ``_time_elements_by_field`` above but not restricted to a
    ``<time>`` tag -- F-100/F-101 (G-1 point 4's own extension) name a
    ``data-field`` attribute, never a specific tag, so the implementer is
    free to choose ``<time>``, ``<span>`` or any other paired tag; this
    helper checks every plausible one rather than silently assuming one.
    """
    results: list[tuple[dict[str, str], str]] = []
    for tag in ("time", "span", "div", "p", "strong", "b", "dd", "td", "li"):
        for attrs, inner in _find_paired_tags(html, tag):
            if attrs.get("data-field") == field:
                results.append((attrs, _strip_tags(inner)))
    return results


def _latest_batch_status(database_url: str, store_id) -> str:
    """Direct DB read of the most recent ``import_batches.status_code``
    for a store -- used only as a setup/regression check, so each new
    test below can state plainly that its own fixture produced the exact
    outcome (``partial``) its assertions depend on, rather than assuming
    it.

    N-05 (``tests/test_g8_import_attempt_ordering.py``, G-8,
    ``DECISIONS.md`` 2026-10-28): this helper previously broke ties with
    ``, id desc`` -- a DIFFERENT tiebreak from
    ``repository.get_import_run_state``'s own ``order by started_at
    desc`` (no secondary key at all). Under a genuine tie the fixture
    could assert one batch while production reads another, making a
    production bug surface here as a wrong-looking fixture assertion
    instead of the real cause. The rule, stated in that story's brief:
    **the helper moves to match production, never the reverse.** This
    query is now byte-for-byte the same ordering
    ``get_import_run_state`` uses today. It inherits the SAME known
    non-determinism under a real tie (G-8, open until migration 005 adds
    ``batch_seq``) -- this file's own fixtures do not construct a tie on
    purpose, so that gap does not affect the tests that call this
    helper, only keeps this helper honest about what production actually
    does.
    """
    with psycopg.connect(database_url, row_factory=dict_row) as conn:
        row = conn.execute(
            """
            select status_code
            from import_batches
            where store_id = %s
            order by started_at desc
            limit 1
            """,
            (store_id,),
        ).fetchone()
    assert row is not None, (
        f"fixture setup failed: no import_batches row exists for store "
        f"{store_id}"
    )
    return row["status_code"]


def _parse_iso_utc(value: str) -> datetime:
    """Parse an ISO-8601 instant, normalising a trailing 'Z' the way
    ``datetime.fromisoformat`` (pre-3.11 semantics still relied on
    elsewhere in this codebase) requires, and attaching UTC if the
    rendered value was naive -- storage is always UTC (AGENTS.md), so a
    naive rendered value is assumed to already BE a UTC instant, not
    re-interpreted in any other zone.
    """
    normalised = value.strip()
    if normalised.endswith("Z"):
        normalised = normalised[:-1] + "+00:00"
    dt = datetime.fromisoformat(normalised)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


_UNAMBIGUOUS_CURRENCY_CLAIM_RE = re.compile(
    r"\bup[\s-]?to[\s-]?date\b|\bdata is current\b|\bfully current\b|"
    r"\bcurrently up to date\b",
    re.IGNORECASE,
)
_FACT_LEVEL_CURRENCY_WORD_RE = re.compile(
    r"\bcurrent\b|\bfresh\b|\bup[\s-]?to[\s-]?date\b", re.IGNORECASE
)


# ---------------------------------------------------------------------------
# Shared fixture-building step: one genuine success, then one genuine
# whole-batch failure (B-18 shape), for one store, reused by every test
# below so each test's OWN docstring can state only what it checks, not
# how the data was produced.
# ---------------------------------------------------------------------------


def _build_success_then_failure(client, migrated_database, *, store_name: str):
    with _committed(migrated_database) as c:
        store_id = str(_create_store_db(c, store_name))
        manager = _create_user(c, role="manager", password=f"{store_name}-pw")
        _assign(c, manager["id"], store_id)

    token = _sign_in_json(client, manager)

    before = datetime.now(timezone.utc)

    success_order_id = _new_order_id("success")
    r1 = _import(client, token, store_id, [_order_payload(walmart_order_id=success_order_id)])
    assert r1.status_code == 201, r1.text

    conflict_order_id = _new_order_id("conflict")
    r2 = _import(client, token, store_id, _conflicting_pair(conflict_order_id))
    assert r2.status_code not in (200, 201), (
        "fixture setup failed: the whole-batch conflict must actually "
        f"fail, or this file's AC-03 fixture proves nothing: {r2.text}"
    )

    after = datetime.now(timezone.utc)

    _post_sign_in(client, manager["email"], manager["password"])

    return store_id, manager, before, after


# ---------------------------------------------------------------------------
# AC-03, clause 1 -- "the last successful update ... [is] visible"
# ---------------------------------------------------------------------------


class TestAC03LastSuccessfulUpdateIsVisible:
    def test_ac03_last_successful_update_is_visible_when_viewing_the_list(
        self, client, migrated_database
    ):
        """After a successful import and a LATER failed one, viewing the
        orders list must show the LAST SUCCESS's own instant -- not
        nothing, and not the failure's instant mistaken for it. The
        expected value is checked as a WINDOW (``before``/``after``
        wall-clock bounds spanning both import calls), not pinned to one
        exact source column (B-22: either ``orders.last_imported_at`` or
        ``import_batches.finished_at`` -- or a computed value derived
        from either -- satisfies this, as long as it falls inside the
        window both columns were genuinely written within).

        Mutation this catches: the screen never rendering a last-success
        fact at all once a later failure exists (collapsing "last
        success" into "most recent attempt", which is exactly the AC-03
        failure mode the audited backend's own structural guard already
        forbids at the data layer -- this test proves the SCREEN does not
        reintroduce it one layer up).

        Expected failure right now: a 404 (no route exists for
        ``GET /app/stores/{store_id}/orders`` yet), not a missing-element
        assertion -- there is no template or handler to miss an element
        in.
        """
        store_id, _manager, before, after = _build_success_then_failure(
            client, migrated_database, store_name="AC03 Last Success Store"
        )

        page = client.get(_orders_list_path(store_id), follow_redirects=True)
        assert page.status_code == 200, (
            f"GET {_orders_list_path(store_id)} must succeed for a store "
            f"in the signed-in manager's own scope: {page.status_code} "
            f"{page.text[:500]}"
        )

        success_elements = _time_elements_by_field(page.text, "last-success-at")
        assert success_elements, (
            "no element carrying data-field=\"last-success-at\" was found -- "
            "'the last successful update' must be visible when viewing the "
            f"list, even though a later import has since failed: {page.text[:2000]!r}"
        )
        attrs, _text = success_elements[0]
        assert attrs.get("datetime"), (
            f"the last-success-at element has no datetime attribute: {attrs!r}"
        )
        rendered_at = _parse_iso_utc(attrs["datetime"])
        assert before <= rendered_at <= after, (
            f"the rendered last-success instant {rendered_at} falls outside "
            f"the window [{before}, {after}] the fixture's own successful "
            "import call was made within"
        )


# ---------------------------------------------------------------------------
# AC-03, clause 2 -- "... and failure are visible"
# ---------------------------------------------------------------------------


class TestAC03FailureIsVisible:
    def test_ac03_the_failure_itself_is_visible_when_viewing_the_list(
        self, client, migrated_database
    ):
        """The failed refresh itself must be visible, not just silently
        absorbed -- a human-readable signal (the element's own text names
        it as a failure) AND a machine-readable instant (the ``datetime``
        attribute, window-checked against B-22 the same way as the
        success fact above).

        Mutation this catches: a screen that shows ONLY the stale last-
        success fact and never surfaces that the most recent attempt
        failed -- which from a user's perspective is indistinguishable
        from a healthy store, the exact harm AC-03 exists to prevent.

        Expected failure right now: a 404 for the same reason as the
        test above -- the route does not exist yet.
        """
        store_id, _manager, before, after = _build_success_then_failure(
            client, migrated_database, store_name="AC03 Failure Visible Store"
        )

        page = client.get(_orders_list_path(store_id), follow_redirects=True)
        assert page.status_code == 200, page.text[:500]

        failure_elements = _time_elements_by_field(page.text, "last-import-failure")
        assert failure_elements, (
            "no element carrying data-field=\"last-import-failure\" was "
            "found -- the failure itself must be visible when viewing the "
            f"list: {page.text[:2000]!r}"
        )
        attrs, text = failure_elements[0]
        assert "fail" in text.lower(), (
            "the failure element's own text must say, in human-readable "
            f"terms, that the import failed: {text!r}"
        )
        assert attrs.get("role") == "alert", (
            "the failure element must be marked role=\"alert\" so assistive "
            f"technology surfaces it, not just visual styling: {attrs!r}"
        )
        assert attrs.get("datetime"), (
            f"the failure element has no datetime attribute: {attrs!r}"
        )
        rendered_at = _parse_iso_utc(attrs["datetime"])
        assert before <= rendered_at <= after, (
            f"the rendered failure instant {rendered_at} falls outside the "
            f"window [{before}, {after}] the fixture's own failing import "
            "call was made within"
        )


# ---------------------------------------------------------------------------
# AC-03, clause 2 (continued) -- "both ... visible", i.e. together, in the
# SAME view, not two separate screens or a second request.
# ---------------------------------------------------------------------------


class TestAC03BothFactsAreVisibleTogether:
    def test_ac03_last_success_and_failure_are_visible_together_in_the_same_view(
        self, client, migrated_database
    ):
        """AC-03's own sentence names both facts in one breath: "the last
        successful update and failure are visible". This test is
        deliberately distinct from the two above -- it is not enough for
        each fact to be independently reachable somehow; a single ``GET``
        of the list must carry BOTH in its one response body at once.

        Mutation this catches: an implementation that renders the success
        fact on the list page but moves the failure fact behind a SECOND
        request (e.g. only visible after following a link to the batch
        detail) -- each of the two tests above would still pass in
        isolation (each independently re-fetches the page), but this test
        fails because it demands both be present in exactly ONE captured
        response.
        """
        store_id, _manager, before, after = _build_success_then_failure(
            client, migrated_database, store_name="AC03 Both Together Store"
        )

        page = client.get(_orders_list_path(store_id), follow_redirects=True)
        assert page.status_code == 200, page.text[:500]
        body = page.text

        success_elements = _time_elements_by_field(body, "last-success-at")
        failure_elements = _time_elements_by_field(body, "last-import-failure")

        assert success_elements and failure_elements, (
            "both facts must be present in the SAME response: "
            f"last-success-at found={bool(success_elements)}, "
            f"last-import-failure found={bool(failure_elements)}"
        )

        success_at = _parse_iso_utc(success_elements[0][0]["datetime"])
        failure_at = _parse_iso_utc(failure_elements[0][0]["datetime"])
        assert before <= success_at <= after
        assert before <= failure_at <= after
        # The two instants are genuinely distinct facts, not the same
        # import echoed under two field names -- the success call and the
        # failing call are two different HTTP requests in this fixture.
        assert success_at != failure_at, (
            "the last-success and last-failure instants must not be "
            f"identical: both read {success_at}"
        )


# ---------------------------------------------------------------------------
# AC-03, clause 3 -- "stale data is not labeled current"
# ---------------------------------------------------------------------------


class TestAC03StaleDataIsNotLabeledCurrent:
    def test_ac03_stale_data_is_not_labeled_current_after_a_failed_refresh(
        self, client, migrated_database
    ):
        """The negative constraint AC-03 exists to enforce. After the
        most recent attempt has failed, the LAST-SUCCESS fact (now stale)
        must never be presented as current/fresh/up to date -- neither in
        its own element's text, nor anywhere else on the page via an
        unambiguous currency claim.

        This is the sharpest test in this file: it is trivially satisfied
        by an implementation that renders NOTHING (no success fact, no
        failure fact) -- which is exactly why it is paired with the
        presence assertions above in the SAME test, so a blank page cannot
        pass this one by accident.

        Mutation this catches: a screen that renders the stale
        last-success timestamp next to a static "Data is up to date" (or
        equivalent) label that does not actually read the most recent
        attempt's own status -- a literal, real UI anti-pattern this
        story exists to forbid, not a hypothetical one.
        """
        store_id, _manager, before, after = _build_success_then_failure(
            client, migrated_database, store_name="AC03 Not Labeled Current Store"
        )

        page = client.get(_orders_list_path(store_id), follow_redirects=True)
        assert page.status_code == 200, page.text[:500]
        body = page.text

        # Presence, so a blank page cannot pass this test by default.
        success_elements = _time_elements_by_field(body, "last-success-at")
        failure_elements = _time_elements_by_field(body, "last-import-failure")
        assert success_elements and failure_elements, (
            "setup/regression check: both facts must be rendered for the "
            "negative assertions below to mean anything -- "
            f"last-success-at found={bool(success_elements)}, "
            f"last-import-failure found={bool(failure_elements)}"
        )

        # Fact-level: the stale success element's OWN text never claims
        # currency.
        _attrs, success_text = success_elements[0]
        assert not _FACT_LEVEL_CURRENCY_WORD_RE.search(success_text), (
            "the last-success-at element's own text claims currency "
            f"('current'/'fresh'/'up to date') after a later failure, "
            f"which is exactly the forbidden labeling: {success_text!r}"
        )

        # Page-wide: no unambiguous currency claim appears anywhere, under
        # the same condition (the most recent attempt did not succeed).
        assert not _UNAMBIGUOUS_CURRENCY_CLAIM_RE.search(body), (
            "the page contains an unambiguous currency claim ('up to "
            "date' / 'data is current' / 'fully current') even though the "
            "most recent import attempt for this store failed"
        )


# ---------------------------------------------------------------------------
# Access -- "An unassigned store is indistinguishable from a non-existent
# one" (the general Access-area case this table requires whenever the area
# is touched). B-20 is OPEN and UNCONFIRMED (PROGRESS.md): no capability
# row distinguishes "view import history" from "trigger an import". This
# test asserts the project's one INTERIM answer -- store-scope-only,
# already applied in ``get_import_run_state`` -- and cites B-20 explicitly
# so it reads as provisional, not settled.
# ---------------------------------------------------------------------------


class TestAccessUnassignedStoreIsIndistinguishableFromNonExistent:
    def test_b20_an_unassigned_store_is_indistinguishable_from_a_nonexistent_one(
        self, client, migrated_database
    ):
        """B-20 (interim, unconfirmed): a Manager assigned to store A, but
        NOT to store B, must get the SAME response shape for "view B's
        import list" as for a store id that names no row at all -- a
        different status/body for "exists but not yours" vs "does not
        exist" would leak which store ids are real to someone who should
        not even know that much (the same discipline ``stores_repository.
        get_store``'s own docstring already states for every other
        store-scoped read in this codebase).

        If the Owner later rules a DIFFERENT authorization shape for this
        screen (e.g. a role gate narrower than "any assigned user"), THIS
        test is the one to revisit -- not the three AC-03 tests above,
        which do not depend on which authorization rule wins.
        """
        with _committed(migrated_database) as c:
            store_a = str(_create_store_db(c, "B20 Assigned Store A"))
            store_b_unassigned = str(_create_store_db(c, "B20 Unassigned Store B"))
            manager = _create_user(c, role="manager", password="b20-manager-pw")
            _assign(c, manager["id"], store_a)

        _post_sign_in(client, manager["email"], manager["password"])

        nonexistent_store_id = str(uuid.uuid4())

        resp_unassigned = client.get(
            _orders_list_path(store_b_unassigned), follow_redirects=True
        )
        resp_nonexistent = client.get(
            _orders_list_path(nonexistent_store_id), follow_redirects=True
        )

        assert resp_unassigned.status_code == resp_nonexistent.status_code, (
            "an unassigned-but-real store and a nonexistent store id must "
            f"produce the SAME status code: unassigned="
            f"{resp_unassigned.status_code} nonexistent="
            f"{resp_nonexistent.status_code}"
        )
        assert resp_unassigned.status_code == 404, (
            "both an unassigned store and a nonexistent store id must read "
            f"as 404 (never 403, which would confirm the store id is real): "
            f"got {resp_unassigned.status_code}"
        )

        # Positive control: the SAME manager reaching their OWN store's
        # list must actually succeed, so this test cannot pass merely
        # because the whole route 404s for everyone unconditionally.
        resp_own = client.get(_orders_list_path(store_a), follow_redirects=True)
        assert resp_own.status_code == 200, (
            "setup/regression check: the manager's own assigned store must "
            f"be reachable, or this test proves nothing: {resp_own.status_code} "
            f"{resp_own.text[:500]}"
        )


# ---------------------------------------------------------------------------
# F-100 -- B-22 closed "both" (DECISIONS.md, 2026-10-28): order-level
# freshness (``orders.last_imported_at``) must be read and rendered as its
# OWN element, genuinely distinct from the run-level facts above. Audited
# finding: no reader of ``orders.last_imported_at`` exists anywhere in the
# codebase (only writers -- ``src/orders/service.py:530``,
# ``src/orders/repository.py:97,118``, ``003_orders.sql:160``) and the
# route renders only the run-level fact.
#
# These original 5 tests above were deliberately written source-agnostic
# BEFORE B-22 was ruled, so they structurally cannot detect this gap (any
# window check against either source passes when both sources are written
# in the same request). The test below is source-SPECIFIC by
# construction instead: it builds a state where the order-level and
# run-level answers GENUINELY DIFFER, so a window check against the wrong
# source fails on its own window bound, not merely on a missing element.
# ---------------------------------------------------------------------------


class TestF100OrderLevelFreshnessIsItsOwnDistinctElement:
    def test_f100_order_level_freshness_is_read_from_orders_last_imported_at_not_the_run(
        self, client, migrated_database
    ):
        """B-22 closed "both" (DECISIONS.md, 2026-10-28). The decisive
        property this test depends on: a state where the order-level
        answer and the run-level answer GENUINELY DIFFER, so that
        rendering only the run-level value (today's actual behaviour)
        cannot pass by coincidence.

        Construction: batch 1 imports order A and COMPLETES -- the
        run-level ``last_success_at`` becomes batch 1's own instant,
        window ``[before1, after1]``. Batch 2 re-imports the SAME order A
        (which updates ITS OWN ``orders.last_imported_at`` row to a NEWER
        instant) alongside an unrelated whole-order rejection (B-18
        shape, ``_conflicting_pair``) in the SAME request -- this makes
        batch 2 classify as ``partial``, never ``completed``
        (``service.py``'s own three-way outcome, already proven by F-95
        in ``test_us005b1_import_run_record.py``). Because
        ``get_import_run_state`` only advances ``last_success_at`` on a
        ``completed`` batch, the RUN-level fact stays pinned to batch 1's
        window, while the ORDER-level fact (``orders.last_imported_at``
        for the one order row this store ever has) genuinely moves to
        batch 2's window -- the rejected order in batch 2 never gets an
        ``orders`` row at all (B-18: reject whole, zero rows), so there
        is exactly ONE candidate value for any correct reading of
        ``orders.last_imported_at`` in this store: no aggregation
        ambiguity to hide behind.

        Mutation this catches: rendering ``order-last-imported-at`` by
        copying/aliasing the run-level ``last_success_at`` value (reads
        batch 1's window, not batch 2's) -- indistinguishable from NOT
        reading ``orders.last_imported_at`` at all, which is today's
        actual, audited state.

        Expected failure right now: no element carries
        ``data-field="order-last-imported-at"`` at all -- the route does
        not render it (F-100's own finding: no reader of the column
        exists anywhere), not a wrong-window value.
        """
        with _committed(migrated_database) as c:
            store_id = str(_create_store_db(c, "F100 Order Level Freshness Store"))
            manager = _create_user(c, role="manager", password="f100-manager-pw")
            _assign(c, manager["id"], store_id)

        token = _sign_in_json(client, manager)

        order_a_id = _new_order_id("f100-order-a")

        before1 = datetime.now(timezone.utc)
        r1 = _import(
            client,
            token,
            store_id,
            [_order_payload(walmart_order_id=order_a_id, sku="SKU-F100-A")],
        )
        assert r1.status_code == 201, r1.text
        after1 = datetime.now(timezone.utc)

        rejected_order_id = _new_order_id("f100-rejected")
        before2 = datetime.now(timezone.utc)
        r2 = _import(
            client,
            token,
            store_id,
            [
                _order_payload(walmart_order_id=order_a_id, sku="SKU-F100-A"),
                *_conflicting_pair(rejected_order_id),
            ],
        )
        assert r2.status_code in (200, 201), (
            "fixture setup failed: a mixed batch (one valid re-import, "
            "one rejected pair elsewhere in the same request) must still "
            "report success for the valid order, or this file's own "
            "F-95 precedent does not hold here -- 200 is the correct "
            "code here (main.py: 'nothing CREATED on this call', order A "
            f"already existed from batch 1): {r2.status_code} {r2.text}"
        )
        after2 = datetime.now(timezone.utc)

        # Regression/setup check: batch 2 must genuinely be `partial`, not
        # `completed` -- otherwise `last_success_at` would legitimately
        # advance to batch 2 too, and this fixture would prove nothing
        # about run-level vs order-level divergence.
        assert _latest_batch_status(migrated_database, store_id) == "partial", (
            "fixture setup failed: batch 2 must classify as 'partial' "
            "(one success, one whole-order rejection) for the run-level/"
            "order-level divergence this test depends on to exist"
        )

        _post_sign_in(client, manager["email"], manager["password"])
        page = client.get(_orders_list_path(store_id), follow_redirects=True)
        assert page.status_code == 200, page.text[:500]
        body = page.text

        # Run-level: unchanged from batch 1's own window (already proven
        # by F-95; re-asserted here so the divergence below is relative
        # to a value THIS test itself has pinned, not merely assumed).
        success_elements = _time_elements_by_field(body, "last-success-at")
        assert success_elements, "no last-success-at element found"
        run_level_at = _parse_iso_utc(success_elements[0][0]["datetime"])
        assert before1 <= run_level_at <= after1, (
            f"run-level last-success-at {run_level_at} must stay pinned "
            f"to batch 1's window [{before1}, {after1}] -- batch 2 was "
            "'partial', not 'completed', and must not advance it"
        )

        # Order-level: the new, F-100 element.
        order_level_elements = _generic_elements_by_data_field(
            body, "order-last-imported-at"
        )
        assert order_level_elements, (
            "no element carrying data-field=\"order-last-imported-at\" "
            "was found -- B-22 ('both', DECISIONS.md 2026-10-28) "
            "requires order-level freshness (orders.last_imported_at) "
            f"to be rendered as its own distinct element: {body[:2000]!r}"
        )
        order_attrs, _order_text = order_level_elements[0]
        assert order_attrs.get("datetime"), (
            "the order-last-imported-at element has no datetime "
            f"attribute: {order_attrs!r}"
        )
        order_level_at = _parse_iso_utc(order_attrs["datetime"])
        assert before2 <= order_level_at <= after2, (
            f"the rendered order-level instant {order_level_at} falls "
            f"outside batch 2's own window [{before2}, {after2}] -- "
            "order A's own orders.last_imported_at row was updated by "
            "batch 2, so a correct reading of orders.last_imported_at "
            "must land here, not in batch 1's window"
        )

        # The decisive property itself: the two facts are NOT the same
        # value -- an implementation that merely aliases/copies the
        # run-level fact under a second field name cannot satisfy both
        # window assertions above AND this one.
        assert order_level_at != run_level_at, (
            "order-level and run-level freshness must be genuinely "
            f"distinct facts in this fixture: both read {run_level_at}"
        )
        assert order_level_at > run_level_at, (
            "order-level freshness (updated by batch 2, a partial run) "
            "must be NEWER than run-level last-success-at (still pinned "
            f"to batch 1, the last COMPLETED run): order={order_level_at} "
            f"run={run_level_at}"
        )


# ---------------------------------------------------------------------------
# F-101 -- a `partial` batch (most rows succeeded) must not be rendered as
# the word "failed". `src/web/routes.py:286-295` currently flattens every
# non-`completed` status (including `partial`) into that hardcoded word,
# even though `partial` is the DESIGNED outcome for B-19/B-21's flagged-
# aside rows (DECISIONS.md, 2026-10-28) and will become the common case,
# not an edge case, once those land.
#
# G-7's narrow, still-OPEN question (which batches become `failed` versus
# `partial`) is deliberately NOT pinned here -- only that whichever raw
# value a batch actually has is the value rendered, verbatim.
# ---------------------------------------------------------------------------


class TestF101PartialIsRenderedAsItsOwnValueNotTheWordFailed:
    def test_f101_a_partial_batch_is_rendered_as_partial_not_as_the_word_failed(
        self, client, migrated_database
    ):
        """A `partial` batch (one order succeeds, one is rejected whole,
        B-18 shape -- the same mixed-outcome fixture F-95 in
        ``test_us005b1_import_run_record.py`` already proves classifies
        as `partial`, never `completed` or `failed`) must be rendered as
        its OWN raw status, not mapped to the invented word "failed".

        G-7 (the undecided failed-vs-partial TRANSITION rule) is NOT
        pinned here -- nothing below asserts which batches become
        `partial` versus `failed`; only that whichever raw value THIS
        batch actually has is the value rendered, never flattened to a
        different word (DECISIONS.md 2026-10-28, "G-7 partially closed:
        import run status is rendered as its own value, never mapped to
        an invented word").

        Mutation this catches: ``routes.py``'s current
        ``last_attempt_status != "completed"`` branch, which renders the
        literal text "The most recent import attempt failed ..." for ANY
        non-completed status, including `partial` -- a false statement
        about a run that mostly succeeded, and one that simultaneously
        leaves the (older, batch-1) `last-success-at` looking like the
        only success marker even though batch 2 ALSO succeeded for most
        of its rows.

        Expected failure right now: the word "fail" IS found on the page
        (the current hardcoded sentence), where this test requires it to
        be ABSENT for a `partial` outcome.
        """
        with _committed(migrated_database) as c:
            store_id = str(_create_store_db(c, "F101 Partial Rendering Store"))
            manager = _create_user(c, role="manager", password="f101-manager-pw")
            _assign(c, manager["id"], store_id)

        token = _sign_in_json(client, manager)

        valid_order_id = _new_order_id("f101-valid")
        rejected_order_id = _new_order_id("f101-rejected")

        r = _import(
            client,
            token,
            store_id,
            [
                _order_payload(walmart_order_id=valid_order_id, sku="SKU-F101-VALID"),
                *_conflicting_pair(rejected_order_id),
            ],
        )
        assert r.status_code == 201, (
            "fixture setup failed: a mixed batch (one valid order, one "
            "whole-order rejection elsewhere in the same request) must "
            f"still report success for the valid order: {r.status_code} "
            f"{r.text}"
        )

        # Regression/setup check: this batch must genuinely be `partial`,
        # or this test proves nothing about the `partial`-vs-`failed`
        # distinction.
        assert _latest_batch_status(migrated_database, store_id) == "partial", (
            "fixture setup failed: the mixed batch must classify as "
            "'partial', not some other status, for this test to mean "
            "anything"
        )

        _post_sign_in(client, manager["email"], manager["password"])
        page = client.get(_orders_list_path(store_id), follow_redirects=True)
        assert page.status_code == 200, page.text[:500]
        body = page.text

        assert "fail" not in body.lower(), (
            "the word 'fail'/'failed' must not appear anywhere on the "
            "page when the most recent import attempt was 'partial' "
            f"(most rows succeeded), not 'failed': {body[:2000]!r}"
        )

        # Positive requirement, paired in the SAME test so a blank page
        # cannot pass the negative assertion above by accident: the raw
        # status is still visibly represented as its OWN value.
        status_elements = _generic_elements_by_data_field(
            body, "last-attempt-status"
        )
        assert status_elements, (
            "no element carrying data-field=\"last-attempt-status\" was "
            "found -- a 'partial' outcome must still be visibly "
            f"represented as its OWN raw value, not silently absorbed: "
            f"{body[:2000]!r}"
        )
        status_attrs, status_text = status_elements[0]
        assert status_attrs.get("data-status-code") == "partial", (
            "the last-attempt-status element must carry the RAW stored "
            "status_code ('partial') in data-status-code, not an "
            f"invented word: {status_attrs!r}"
        )
        assert "partial" in status_text.lower() or "partial" in str(status_attrs).lower(), (
            "the 'partial' outcome must be legible as its own value "
            f"somewhere in the element (text or attrs): "
            f"text={status_text!r} attrs={status_attrs!r}"
        )

        # The existing failure-naming contract ("last-import-failure",
        # text containing "fail") is reserved for a genuinely `failed`
        # status -- a `partial` outcome must NOT be rendered through it.
        failure_elements = _time_elements_by_field(body, "last-import-failure")
        for attrs, text in failure_elements:
            assert "fail" not in text.lower(), (
                "a 'partial' outcome must not be rendered through the "
                "'last-import-failure' element, which is reserved for a "
                f"genuinely 'failed' status: {text!r}"
            )
