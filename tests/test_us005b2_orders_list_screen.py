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
B-22 -- deliberately NOT resolved here, and the tests are written so they
do not need to be. ``database-schema.md:454`` defines ``orders.
last_imported_at`` as "Freshness (US-005 AC-03)" -- a per-ORDER stamp. The
already-audited backend answers AC-03 per-RUN, from ``import_batches``
(``database-schema.md:967-984``, ``get_import_run_state``). No document
says the screen must read one, the other, or both. Every timestamp
assertion below is therefore a WINDOW check (captured ``before``/``after``
wall-clock bounds around the fixture's own import calls), never an exact
equality against one specific column's stored value -- both candidate
source columns are written inside the same window, so either one (or a
computed combination) satisfies these tests without this file taking a
side. If a future change makes that window check inexpressible for some
behaviour, the rule is to skip citing B-22, not to silently pick a source
-- not needed for any test actually written below.

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
