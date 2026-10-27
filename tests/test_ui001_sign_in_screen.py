"""UI-01 — Sign-in screen, app shell, and design system.

Covers US-001's screen (the acceptance criteria themselves are pinned
server-side in ``test_us001_sign_in.py``; this file pins the *screen* that
sits in front of them), the cross-cutting accessibility criteria of US-032
that apply to every screen, and a handful of theme-agnostic engineering-
hygiene requirements (Group 5) for however the eventual stylesheet is built.

``theme.txt`` at the repository root is **not** a design-system contract.
The Owner ruled on this explicitly (``PROGRESS.md``, Decisions log,
2026-10-27, "B-16 closed by the Owner"): ``theme.txt`` is non-normative
aesthetic inspiration only -- "the idea and inspiration", not exact hex
values, not the exact Google Fonts it names, not its SVG paper-grain
technique as a hard requirement. It does not override
``docs/walmart-store-manager-design.md``'s own UX baseline -- "a serious
internal finance-and-operations tool... data-dense... compact typography...
desktop-first density" -- it is a mood/palette steer layered on top of that
baseline, not a replacement for it. Nothing below pins a literal token from
``theme.txt``; where a Group-5 test used to assert an exact hex or font
name, it now asserts the underlying engineering property instead (a token
is declared once, not twice; heading and body typography are independently
settable; body text clears a real contrast floor against its background)
-- all true regardless of which palette or fonts eventually get chosen.

Nothing in ``src/templates/`` or ``src/static/`` exists yet. Every test below
is expected to fail; the session report accompanying this file says, per
test, whether the failure is because a **route or template is absent**
(infrastructure) or because a **specific behaviour is absent or wrong**
(the real constraint, once the infrastructure exists).

-------------------------------------------------------------------------
Decisions made to write these tests, recorded so they can be checked rather
than silently assumed -- the same discipline ``test_us003_create_store.py``
applies to its own request-body field names:

1. **Routes.** No document names a URL for the sign-in screen, the
   authenticated application shell, or a sign-out control.
   ``walmart-user-stories.md:711`` names the *screen* ``UI-01`` but not a
   path. This file assumes ``GET /`` (public entry), ``GET /sign-in``
   (the form), and ``POST /sign-in`` (form-encoded, fields ``email`` and
   ``password``) as the sign-in surface, and ``GET /app`` as the protected
   shell -- picked as the most conventional names, not confirmed by any
   document. If the Owner names different paths, change the constants
   below; do not change the behaviour they test.

2. **Sign-out is discovered from the rendered page, not assumed as a
   route.** Rather than guess a second path, every test that needs to sign
   out parses the authenticated shell's own markup for a ``<form>`` or
   ``<a>`` whose text says "sign out" / "log out", and issues whatever
   request that control actually describes. This also happens to be the
   literal check for "the shell shows ... a sign-out control" (Group 2).

3. **"Session established" and "lands in the shell" are checked
   behaviourally**, by probing whether ``GET /app`` now succeeds directly
   with this same client -- not by reading a cookie name, which nothing
   specifies and which would make the test about transport instead of
   about the rule.

4. **python-multipart is not installed and form posts will need it.**
   FastAPI's ``Form(...)`` parameters require Starlette to parse the
   request body, and Starlette requires ``python-multipart`` to do that --
   for *any* form content type, not only file uploads. Right now every
   ``POST /sign-in`` call below 404s because the route does not exist; the
   day it exists and uses ``Form(...)``, it will raise
   ``RuntimeError: Form data requires "python-multipart" to be installed``
   unless that dependency is added. Noted here rather than installed,
   per the brief; do not let a green run of this file be mistaken for that
   question being settled. (Adding the dependency is the implementer's
   job, not the test-author's -- left exactly as before.)

5. **CSS assertions are source-level, not rendered.** There is no browser
   here. "The stylesheet defines a visible focus style", "declared once as
   a CSS custom property", and "body text clears a contrast floor against
   its background" are all checked by parsing the stylesheet text with
   small, deliberately-documented regexes -- not by asking a browser what
   it computed. Where that distinction matters, it is called out at the
   assertion site and again in the accompanying session report.

6. **F-13 is not resolved here.** ``access-v1``/the US-001 audit left open
   whether ``ACCOUNT_LOCKED`` should be disclosed to the user at all
   (walmart-user-stories.md:90 wants a temporary lockout the user can act
   on; api-service-design.md:160 wants identical wording for every failure
   case, unknown account included). This file pins **no** answer either
   way. ``test_f13_account_locked_disclosure_is_an_open_question`` below is
   a skip, not a pass or a fail, so the open question stays visible in
   every run instead of being silently decided by whichever test happened
   to be written first.

Conventions carried over from ``test_us001_sign_in.py`` / ``test_us003_
create_store.py``, reused by import rather than copied, per the brief:
``_committed``, ``_create_user``, ``_create_store``, ``_assign``,
``_new_email``. Database tests that only need to seed rows use the module-
level ``migrated_database`` fixture from ``conftest.py``; every scoping-
shaped test seeds a decoy store/user that the assertion does not care
about, so the file stays meaningful when it runs alone against a fresh
database (the discipline F-23 exists to enforce).
"""

from __future__ import annotations

import re
import uuid

import pytest
from fastapi.testclient import TestClient

from src import main
from tests.test_us001_sign_in import (
    _assign,
    _committed,
    _create_store,
    _create_user,
    _new_email,
)

# ---------------------------------------------------------------------------
# Assumed routes (decision 1 above). Change these, not the assertions, if the
# Owner names different paths.
# ---------------------------------------------------------------------------

ROOT_PATH = "/"
SIGN_IN_PATH = "/sign-in"
SHELL_PATH = "/app"


# ---------------------------------------------------------------------------
# tiny, deliberately non-general HTML/CSS helpers
#
# No HTML/CSS parser is installed (no bs4, no lxml, no cssutils). These are
# regex-based and documented as approximations wherever that matters; they
# are not a substitute for a real parser once one is a declared dependency.
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


def _find_self_or_open_tags(html: str, tag: str) -> list[dict[str, str]]:
    """Attrs for every ``<tag ...>`` or ``<tag .../>`` occurrence. Good for
    void/self-closing-ish elements like ``input`` and ``link``."""
    pattern = re.compile(rf"<{tag}\b([^>]*)/?>", re.IGNORECASE)
    return [_parse_attrs(m.group(1)) for m in pattern.finditer(html)]


def _find_paired_tags(html: str, tag: str) -> list[tuple[dict[str, str], str]]:
    """(attrs, inner_html) for every ``<tag ...>...</tag>`` pair.

    Non-nesting-aware: matched via a non-greedy ``.*?`` up to the *next*
    same-named closing tag, so a ``<tag>`` containing a nested ``<tag>``
    will close early. Adequate for small, leaf elements (a label, a single
    alert div) which is all this file ever inspects; not a general parser.
    """
    pattern = re.compile(rf"<{tag}\b([^>]*)>(.*?)</{tag}>", re.IGNORECASE | re.DOTALL)
    return [(_parse_attrs(m.group(1)), m.group(2)) for m in pattern.finditer(html)]


def _strip_tags(fragment: str) -> str:
    text = re.sub(r"<[^>]+>", " ", fragment)
    return re.sub(r"\s+", " ", text).strip()


def _stylesheet_hrefs(html: str) -> list[str]:
    hrefs = []
    for attrs in _find_self_or_open_tags(html, "link"):
        if "stylesheet" in attrs.get("rel", "").lower():
            href = attrs.get("href")
            if href:
                hrefs.append(href)
    return hrefs


def _label_for_text(html: str) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for attrs, inner in _find_paired_tags(html, "label"):
        for_value = attrs.get("for")
        if for_value:
            mapping[for_value] = _strip_tags(inner)
    return mapping


def _inputs(html: str) -> list[dict[str, str]]:
    return _find_self_or_open_tags(html, "input")


def _elements_with_attr(
    html: str, attr: str, value: str | None = None
) -> list[tuple[str, dict[str, str], str]]:
    """(tag, attrs, visible_text) for every element carrying ``attr``
    (optionally equal to ``value``). Scans common container tags only --
    div/p/span/section -- which covers a status/alert region."""
    results: list[tuple[str, dict[str, str], str]] = []
    for tag in ("div", "p", "span", "section", "output"):
        for attrs, inner in _find_paired_tags(html, tag):
            if attr in attrs and (value is None or attrs[attr] == value):
                results.append((tag, attrs, _strip_tags(inner)))
    return results


_SIGN_OUT_TEXT_RE = re.compile(r"sign[\s-]?out|log[\s-]?out", re.IGNORECASE)


def _find_sign_out_control(html: str) -> tuple[str, str] | None:
    """Best-effort discovery of the sign-out control from rendered shell
    markup, rather than a guessed route (decision 2 above)."""
    for attrs, inner in _find_paired_tags(html, "form"):
        if _SIGN_OUT_TEXT_RE.search(inner):
            return attrs.get("method", "get").upper(), attrs.get("action", "")
    for attrs, inner in _find_paired_tags(html, "a"):
        if _SIGN_OUT_TEXT_RE.search(inner):
            return "GET", attrs.get("href", "")
    return None


_ROOT_VAR_RE = re.compile(r"--([a-zA-Z0-9_-]+)\s*:\s*([^;]+);")
_VAR_REF_RE = re.compile(r"var\(\s*--([a-zA-Z0-9_-]+)\s*(?:,\s*([^)]+))?\)")


def _custom_properties(css_text: str) -> list[tuple[str, str]]:
    return [(m.group(1), m.group(2).strip()) for m in _ROOT_VAR_RE.finditer(css_text)]


def _custom_property_counts(css_text: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for name, _ in _custom_properties(css_text):
        counts[name] = counts.get(name, 0) + 1
    return counts


def _custom_property_last_values(css_text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for name, value in _custom_properties(css_text):
        values[name] = value
    return values


def _resolve_color(value: str, root_vars: dict[str, str], _depth: int = 0) -> str:
    value = value.strip()
    if _depth > 5:
        return value
    m = _VAR_REF_RE.match(value)
    if m:
        name, fallback = m.group(1), m.group(2)
        if name in root_vars:
            return _resolve_color(root_vars[name], root_vars, _depth + 1)
        if fallback:
            return _resolve_color(fallback, root_vars, _depth + 1)
    return value


def _extract_braced_block(css_text: str, start_marker_regex: str) -> str | None:
    """Depth-counted extraction of the ``{ ... }`` body following the first
    match of ``start_marker_regex``. Handles one level of nesting correctly
    (needed for ``@media (...) { selector { decls } }``), unlike a bare
    ``[^{}]*`` regex which cannot."""
    m = re.search(start_marker_regex, css_text)
    if not m:
        return None
    brace_start = css_text.find("{", m.end())
    if brace_start == -1:
        return None
    depth = 0
    i = brace_start
    while i < len(css_text):
        if css_text[i] == "{":
            depth += 1
        elif css_text[i] == "}":
            depth -= 1
            if depth == 0:
                return css_text[brace_start + 1 : i]
        i += 1
    return None


def _body_color_declaration(css_text: str) -> str | None:
    """The ``color`` value of a rule whose selector contains the whole word
    ``body`` (or ``html``) outside any ``@media`` wrapper check -- a plain,
    single-level search, since a base text-color rule is not expected to
    live inside a media query."""
    for m in re.finditer(r"([^{}]*\b(?:body|html)\b[^{}]*)\{([^{}]*)\}", css_text):
        decls = m.group(2)
        color_m = re.search(r"(?<![-\w])color\s*:\s*([^;]+);", decls)
        if color_m:
            return color_m.group(1).strip()
    return None


def _background_color_declaration(css_text: str) -> str | None:
    """The page background color, resolved with a **disclosed preference
    order**, since nothing specifies whether the background is declared on
    ``body``, ``html``, or a wrapping container, and guessing a container
    class would be exactly the kind of invention ``AGENTS.md`` forbids:
    a rule naming ``body`` is checked first (the element body text actually
    sits against) across *every* ``body``-matching rule in the stylesheet;
    only if none of them declares a background at all does this fall back
    to ``html``. Within a matching rule, ``background-color`` is checked
    ahead of the ``background`` shorthand (the shorthand may carry an image
    or position token ahead of or after the color); when only the
    shorthand is present, the first ``var(...)`` reference or
    hex/``rgb()``/``rgba()``-shaped token within it is taken as its color
    component."""
    color_token_re = re.compile(
        r"var\([^)]*\)|#[0-9a-fA-F]{3,8}\b|rgba?\([^)]*\)", re.IGNORECASE
    )
    for selector_keyword in ("body", "html"):
        for m in re.finditer(
            rf"([^{{}}]*\b{selector_keyword}\b[^{{}}]*)\{{([^{{}}]*)\}}", css_text
        ):
            decls = m.group(2)
            bg_color_m = re.search(
                r"(?<![-\w])background-color\s*:\s*([^;]+);", decls
            )
            if bg_color_m:
                return bg_color_m.group(1).strip()
            bg_m = re.search(r"(?<![-\w])background\s*:\s*([^;]+);", decls)
            if bg_m:
                token_m = color_token_re.search(bg_m.group(1))
                if token_m:
                    return token_m.group(0)
    return None


_HEX_COLOR_RE = re.compile(r"^#([0-9a-fA-F]{3}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$")
_RGB_FUNC_RE = re.compile(
    r"^rgba?\(\s*([\d.]+)\s*,?\s*([\d.]+)\s*,?\s*([\d.]+)\s*(?:[,/]\s*[\d.%]+\s*)?\)$",
    re.IGNORECASE,
)


def _parse_rgb(color: str) -> tuple[int, int, int] | None:
    """Parses a hex (``#rgb`` / ``#rrggbb`` / ``#rrggbbaa``, alpha ignored)
    or ``rgb()``/``rgba()`` color into ``(r, g, b)`` channel values 0-255.
    Returns ``None`` for anything else (a named color, a gradient, an
    unresolved ``var()`` with no matching ``:root`` declaration) -- callers
    must treat ``None`` as "cannot evaluate this as a color" and fail
    loudly, never silently skip or guess a value."""
    color = color.strip()
    hex_m = _HEX_COLOR_RE.match(color)
    if hex_m:
        digits = hex_m.group(1)
        if len(digits) == 3:
            r, g, b = (int(c * 2, 16) for c in digits)
        else:
            r, g, b = (int(digits[i : i + 2], 16) for i in (0, 2, 4))
        return r, g, b
    rgb_m = _RGB_FUNC_RE.match(color)
    if rgb_m:
        r, g, b = (round(float(x)) for x in rgb_m.groups())
        return r, g, b
    return None


def _relative_luminance(rgb: tuple[int, int, int]) -> float:
    """WCAG 2.x relative luminance: each sRGB channel (0-255) is first
    normalised to 0-1, linearised (the standard sRGB gamma decoding step),
    then combined with the fixed Rec. 709 coefficients 0.2126 / 0.7152 /
    0.0722 for red / green / blue."""

    def _linearise(channel_255: int) -> float:
        c = channel_255 / 255.0
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = rgb
    return 0.2126 * _linearise(r) + 0.7152 * _linearise(g) + 0.0722 * _linearise(b)


def _contrast_ratio(rgb_a: tuple[int, int, int], rgb_b: tuple[int, int, int]) -> float:
    """WCAG 2.x contrast ratio: ``(L1 + 0.05) / (L2 + 0.05)`` with L1 the
    lighter of the two relative luminances, so the result is always
    ``>= 1`` regardless of which color is passed first."""
    l_a = _relative_luminance(rgb_a)
    l_b = _relative_luminance(rgb_b)
    lighter, darker = max(l_a, l_b), min(l_a, l_b)
    return (lighter + 0.05) / (darker + 0.05)


# ---------------------------------------------------------------------------
# fixtures and small test-local helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def client(migrated_database):
    with TestClient(main.app) as c:
        yield c


def _assert_html(resp) -> None:
    assert resp.status_code == 200, resp.text[:500]
    assert "text/html" in resp.headers.get("content-type", ""), resp.headers


def _post_sign_in(client, email: str, password: str, *, follow_redirects: bool = True):
    return client.post(
        SIGN_IN_PATH,
        data={"email": email, "password": password},
        follow_redirects=follow_redirects,
    )


def _assert_route_exists(resp, *, what: str) -> None:
    """Guard against a vacuous pass: several tests below assert an
    *absence* (no password echoed, no session established, no hex color
    rendered). Absence is trivially true against a bare 404 "Not Found",
    so every such test must first prove something real was returned, or it
    would keep passing even after a genuine regression hid behind a broken
    route."""
    assert resp.status_code not in (404, 405), (
        f"{what} returned {resp.status_code} -- the route does not exist, "
        "so the absence check this guards would pass vacuously"
    )


def _can_reach_shell_directly(client) -> bool:
    """Operationalises "a session is established" without reading a cookie
    name nothing specifies (decision 3 above): can this same client reach
    the protected shell right now, with no further action?"""
    probe = client.get(SHELL_PATH, follow_redirects=False)
    return probe.status_code == 200


def _sign_out(client, shell_html: str):
    found = _find_sign_out_control(shell_html)
    assert found is not None, (
        "No sign-out control found in the rendered shell markup (no <form> "
        "or <a> whose text says 'sign out' / 'log out'). Group 2 requires "
        "this control to exist; its absence is the defect the test above "
        "already reports -- this helper cannot invent a route to call."
    )
    method, action = found
    if method == "GET":
        return client.get(action, follow_redirects=True)
    return client.request(method, action, follow_redirects=True)


def _fetch_single_stylesheet(client, html: str) -> str:
    hrefs = _stylesheet_hrefs(html)
    assert len(hrefs) == 1, (
        f"expected exactly one <link rel=stylesheet>, found {len(hrefs)}: {hrefs!r}"
    )
    resp = client.get(hrefs[0])
    assert resp.status_code == 200, (
        f"the one linked stylesheet {hrefs[0]!r} did not load: {resp.status_code}"
    )
    assert "css" in resp.headers.get("content-type", ""), resp.headers
    return resp.text


def _seed_manager(c, *, label: str, password: str):
    return _create_user(c, role="manager", password=password)


# ---------------------------------------------------------------------------
# Group 1 — routes and rendering
# ---------------------------------------------------------------------------


def test_ui01_g1_root_returns_200_html_for_anonymous_visitor(client):
    resp = client.get(ROOT_PATH, follow_redirects=False)
    _assert_html(resp)


def test_ui01_g1_sign_in_returns_200_html_for_anonymous_visitor(client):
    resp = client.get(SIGN_IN_PATH, follow_redirects=False)
    _assert_html(resp)


def test_ui01_g1_anonymous_protected_page_redirects_to_sign_in(client):
    direct = client.get(SHELL_PATH, follow_redirects=False)
    assert direct.status_code in (301, 302, 303, 307, 308), (
        f"an anonymous request to the protected shell ({SHELL_PATH}) must "
        f"redirect to sign-in, got {direct.status_code} directly"
    )
    location = direct.headers.get("location", "")
    assert SIGN_IN_PATH in location, (
        f"redirected to {location!r}, not to the sign-in screen"
    )


def test_ui01_g1_anonymous_protected_page_never_shows_protected_content(
    client, migrated_database
):
    """The redirect alone is not the real claim -- a redirect that still
    rendered the destination underneath it would pass the status check
    above and still leak data. A decoy store/assignment exists (F-23
    hermeticity) even though an anonymous caller has no identity at all to
    possibly match it against."""
    with _committed(migrated_database) as c:
        decoy_store = _create_store(c, f"G1 Decoy Store {uuid.uuid4().hex[:8]}")
        decoy_user = _create_user(c, role="manager", password="g1-decoy-password")
        _assign(c, decoy_user["id"], decoy_store)

    followed = client.get(SHELL_PATH, follow_redirects=True)
    # An absence check alone is vacuously true against a bare 404 -- that
    # would pass today for the wrong reason (nothing rendered at all) and
    # keep passing even once a real leak exists, as long as the leak were
    # ever to hide behind a broken route. Require a real page first.
    assert followed.status_code == 200, (
        "no page rendered at all for the anonymous protected-page request "
        f"({followed.status_code}) -- the absence check below would pass "
        "vacuously against this"
    )
    assert "G1 Decoy Store" not in followed.text
    assert str(decoy_store) not in followed.text


def test_ui01_g1_authenticated_visitor_on_sign_in_is_not_shown_the_form(
    client, migrated_database
):
    password = "g1-authed-correct-horse-battery"
    with _committed(migrated_database) as c:
        user = _create_user(c, role="manager", password=password)

    signed_in = _post_sign_in(client, user["email"], password)
    assert _can_reach_shell_directly(client), (
        "setup failed: sign-in did not establish a usable session, so this "
        "test cannot tell 'shown no form because authenticated' apart from "
        "'shown no form because nothing rendered at all'"
    )

    revisit = client.get(SIGN_IN_PATH, follow_redirects=True)
    assert revisit.status_code == 200
    password_inputs = [i for i in _inputs(revisit.text) if i.get("type") == "password"]
    assert not password_inputs, (
        "an already-authenticated visitor to /sign-in was shown a password "
        "field -- they should not be offered the sign-in form again"
    )


def test_ui01_g1_every_rendered_page_links_exactly_one_stylesheet(
    client, migrated_database
):
    password = "g1-stylesheet-correct-horse-battery"
    with _committed(migrated_database) as c:
        user = _create_user(c, role="manager", password=password)

    anon_sign_in = client.get(SIGN_IN_PATH, follow_redirects=True)
    assert len(_stylesheet_hrefs(anon_sign_in.text)) == 1

    _post_sign_in(client, user["email"], password)
    shell = client.get(SHELL_PATH, follow_redirects=True)
    assert len(_stylesheet_hrefs(shell.text)) == 1


# ---------------------------------------------------------------------------
# Group 2 — the sign-in form
# ---------------------------------------------------------------------------


def test_ui01_g2_email_field_is_labelled(client):
    page = client.get(SIGN_IN_PATH, follow_redirects=True)
    inputs = _inputs(page.text)
    labels = _label_for_text(page.text)

    email_inputs = [
        i for i in inputs if i.get("type", "text") in ("email", "text") and
        ("email" in i.get("name", "").lower() or "email" in i.get("id", "").lower())
    ]
    assert email_inputs, "no input resembling an email field was found at all"
    field = email_inputs[0]
    has_label = field.get("id") in labels
    has_aria_label = bool(field.get("aria-label"))
    assert has_label or has_aria_label, (
        f"email input {field!r} has neither a <label for=...> nor an "
        "aria-label -- it is not identified for assistive technology"
    )


def test_ui01_g2_password_field_is_labelled_and_masked(client):
    page = client.get(SIGN_IN_PATH, follow_redirects=True)
    inputs = _inputs(page.text)
    labels = _label_for_text(page.text)

    password_inputs = [i for i in inputs if i.get("type") == "password"]
    assert password_inputs, (
        "no <input type=\"password\"> found -- a password field must mask "
        "input by its HTML type, not merely by CSS"
    )
    field = password_inputs[0]
    has_label = field.get("id") in labels
    has_aria_label = bool(field.get("aria-label"))
    assert has_label or has_aria_label, (
        f"password input {field!r} has neither a <label for=...> nor an "
        "aria-label"
    )


def test_ui01_g2_sign_in_form_submits_by_post(client):
    page = client.get(SIGN_IN_PATH, follow_redirects=True)
    forms = _find_paired_tags(page.text, "form")
    password_bearing_forms = [
        (attrs, inner)
        for attrs, inner in forms
        if '"password"' in inner.lower() or "'password'" in inner.lower()
    ]
    assert password_bearing_forms, "no <form> contains a password input"
    attrs, _ = password_bearing_forms[0]
    method = attrs.get("method", "get").upper()
    assert method == "POST", f"sign-in form method is {method!r}, not POST"


def test_ui01_g2_successful_sign_in_establishes_a_usable_session(
    client, migrated_database
):
    password = "g2-success-correct-horse-battery"
    with _committed(migrated_database) as c:
        user = _create_user(c, role="manager", password=password)

    assert not _can_reach_shell_directly(client), (
        "setup problem: the shell is reachable before signing in at all"
    )

    _post_sign_in(client, user["email"], password)

    assert _can_reach_shell_directly(client), (
        "a correct email/password did not establish a session that makes "
        "the protected shell reachable"
    )


def test_ui01_g2_successful_sign_in_lands_in_the_shell(client, migrated_database):
    """Distinct from the probe above: the sign-in POST's own rendered
    response (after following whatever redirect it issues) must actually be
    the shell, not a form the caller has to separately know to revisit."""
    password = "g2-lands-correct-horse-battery"
    with _committed(migrated_database) as c:
        user = _create_user(c, role="manager", password=password)

    landed = _post_sign_in(client, user["email"], password)
    assert landed.status_code == 200, landed.text[:300]
    password_inputs = [i for i in _inputs(landed.text) if i.get("type") == "password"]
    assert not password_inputs, (
        "after a successful sign-in, the rendered page still shows a "
        "password field -- this did not land in the shell"
    )


def test_ui01_g2_shell_shows_the_signed_in_users_identity(client, migrated_database):
    password = "g2-identity-correct-horse-battery"
    with _committed(migrated_database) as c:
        user = _create_user(c, role="manager", password=password)

    _post_sign_in(client, user["email"], password)
    shell = client.get(SHELL_PATH, follow_redirects=True)
    assert user["email"] in shell.text, (
        "the shell does not display the signed-in user's identity anywhere "
        "(looked for their email, the one identity attribute every user "
        "is guaranteed to have)"
    )


def test_ui01_g2_shell_shows_a_sign_out_control(client, migrated_database):
    password = "g2-signout-control-correct-horse"
    with _committed(migrated_database) as c:
        user = _create_user(c, role="manager", password=password)

    _post_sign_in(client, user["email"], password)
    shell = client.get(SHELL_PATH, follow_redirects=True)
    assert _find_sign_out_control(shell.text) is not None, (
        "no sign-out control (a <form> or <a> whose text says 'sign out' / "
        "'log out') was found in the shell"
    )


def test_ui01_g2_failed_sign_in_does_not_establish_a_session(client, migrated_database):
    password = "g2-failed-correct-horse-battery"
    with _committed(migrated_database) as c:
        user = _create_user(c, role="manager", password=password)

    rejected = _post_sign_in(client, user["email"], password + "-wrong")
    _assert_route_exists(rejected, what="POST /sign-in with a wrong password")
    assert not _can_reach_shell_directly(client), (
        "a wrong password still left the client able to reach the "
        "protected shell directly -- this is a false success"
    )


def test_ui01_g2_failed_sign_in_preserves_the_typed_email(client, migrated_database):
    password = "g2-preserve-correct-horse-battery"
    with _committed(migrated_database) as c:
        user = _create_user(c, role="manager", password=password)

    rejected = _post_sign_in(client, user["email"], password + "-wrong")
    assert user["email"] in rejected.text, (
        "the typed email was not preserved in the re-rendered form after a "
        "failed sign-in -- the user would have to retype it"
    )


def test_ui01_g2_failed_sign_in_never_echoes_the_plaintext_password(
    client, migrated_database
):
    """US-001 F-05: a password must not appear anywhere in a response body,
    including an error page -- a Pydantic 422 default is what leaked it
    last time, on a different endpoint; this is the same discipline applied
    to the HTML form."""
    password = "g2-f05-never-echoed-correct-horse"
    with _committed(migrated_database) as c:
        user = _create_user(c, role="manager", password=password)

    # Correct email, wrong password on purpose -- neither the typo nor the
    # real password may appear anywhere in the response.
    wrong_password = password + "-typo"
    rejected = _post_sign_in(client, user["email"], wrong_password)
    _assert_route_exists(rejected, what="POST /sign-in with a wrong password")
    assert wrong_password not in rejected.text
    assert password not in rejected.text


def test_ui01_g2_failed_sign_in_message_does_not_reveal_account_existence(
    client, migrated_database
):
    """F-02's enumeration rule, applied to the rendered screen: an unknown
    email and a real email with the wrong password must produce the exact
    same accessible error text. Different wording for the two is itself the
    oracle, independent of timing."""
    password = "g2-enum-correct-horse-battery"
    with _committed(migrated_database) as c:
        user = _create_user(c, role="manager", password=password)

    unknown_email = _new_email("g2-enum-unknown")
    unknown_resp = _post_sign_in(client, unknown_email, "whatever-password")
    wrong_password_resp = _post_sign_in(client, user["email"], password + "-wrong")

    assert unknown_resp.status_code == wrong_password_resp.status_code

    unknown_alerts = _elements_with_attr(unknown_resp.text, "role", "alert")
    unknown_alerts += _elements_with_attr(unknown_resp.text, "aria-live")
    wrong_alerts = _elements_with_attr(wrong_password_resp.text, "role", "alert")
    wrong_alerts += _elements_with_attr(wrong_password_resp.text, "aria-live")

    assert unknown_alerts, (
        "no accessible error region (role=alert or aria-live) found for an "
        "unknown email -- see also US-032 AC-04 below"
    )
    assert wrong_alerts, (
        "no accessible error region found for a known email, wrong password"
    )
    assert unknown_alerts[0][2] == wrong_alerts[0][2], (
        f"unknown-account message {unknown_alerts[0][2]!r} differs from "
        f"wrong-password message {wrong_alerts[0][2]!r} -- this discloses "
        "whether the account exists"
    )


@pytest.mark.skip(
    reason=(
        "F-13: open contradiction, not decided here. "
        "walmart-user-stories.md:90 wants a temporary lockout the user can "
        "act on; api-service-design.md:160 wants identical wording for "
        "every failure case including an unknown account. Both cannot hold "
        "at once for the locked-account case specifically. Asserting either "
        "answer would invent a rule; this test stays skipped, not deleted, "
        "so the open question is visible in every run until the Owner "
        "resolves it (see B- items in PROGRESS.md)."
    )
)
def test_f13_account_locked_disclosure_is_an_open_question():
    raise AssertionError("not reached -- see skip reason")


# ---------------------------------------------------------------------------
# Group 3 — US-001 AC-04: cached data from a previous session
# ---------------------------------------------------------------------------


def test_us_001_ac_04_sign_out_then_protected_page_shows_no_previous_user_data(
    client, migrated_database
):
    password = "g3-signout-data-correct-horse"
    store_name = f"G3 First Store {uuid.uuid4().hex[:8]}"
    with _committed(migrated_database) as c:
        store = _create_store(c, store_name)
        user = _create_user(c, role="manager", password=password)
        _assign(c, user["id"], store)
        # Decoy, present but never assigned to this user (F-23 hermeticity).
        decoy_store = _create_store(c, f"G3 Decoy {uuid.uuid4().hex[:8]}")
        decoy_user = _create_user(c, role="manager", password="g3-decoy-password")
        _assign(c, decoy_user["id"], decoy_store)

    _post_sign_in(client, user["email"], password)
    before = client.get(SHELL_PATH, follow_redirects=True)
    assert store_name in before.text, (
        "setup failed: the user's own store never appeared in the shell"
    )

    shell_for_sign_out = client.get(SHELL_PATH, follow_redirects=True)
    _sign_out(client, shell_for_sign_out.text)
    assert not _can_reach_shell_directly(client), "sign-out did not take effect"

    after = client.get(SHELL_PATH, follow_redirects=True)
    assert store_name not in after.text, (
        "business data from the signed-out session is still rendered after "
        "sign-out"
    )
    assert user["email"] not in after.text


def test_us_001_ac_04_sign_out_then_sign_in_as_a_different_user_shows_no_trace(
    client, migrated_database
):
    """The sharper half of AC-04: not merely "nothing renders", but
    "nothing of the *first* user's data appears once a *second*, unrelated
    user is signed in on the same client"."""
    password_a = "g3-alpha-correct-horse-battery"
    password_b = "g3-bravo-correct-horse-battery"
    store_a_name = f"G3 Store Alpha {uuid.uuid4().hex[:8]}"
    store_b_name = f"G3 Store Bravo {uuid.uuid4().hex[:8]}"
    with _committed(migrated_database) as c:
        store_a = _create_store(c, store_a_name)
        store_b = _create_store(c, store_b_name)
        user_a = _create_user(c, role="manager", password=password_a)
        user_b = _create_user(c, role="manager", password=password_b)
        _assign(c, user_a["id"], store_a)
        _assign(c, user_b["id"], store_b)

    _post_sign_in(client, user_a["email"], password_a)
    shell_a = client.get(SHELL_PATH, follow_redirects=True)
    assert store_a_name in shell_a.text

    _sign_out(client, shell_a.text)

    _post_sign_in(client, user_b["email"], password_b)
    shell_b = client.get(SHELL_PATH, follow_redirects=True)

    assert store_b_name in shell_b.text, "setup failed: user B's own store never rendered"
    assert store_a_name not in shell_b.text, (
        "the previous user's store name leaked into the new user's shell"
    )
    assert user_a["email"] not in shell_b.text, (
        "the previous user's email leaked into the new user's shell"
    )
    assert str(store_a) not in shell_b.text


def test_us_001_ac_04_the_shell_route_itself_requires_fresh_authentication_after_sign_out(
    client, migrated_database
):
    """Distinct from the data-content checks above: the *route* must
    refuse directly, not merely omit content while still returning 200."""
    password = "g3-route-correct-horse-battery"
    with _committed(migrated_database) as c:
        store = _create_store(c, f"G3 Route Store {uuid.uuid4().hex[:8]}")
        user = _create_user(c, role="manager", password=password)
        _assign(c, user["id"], store)

    _post_sign_in(client, user["email"], password)
    shell = client.get(SHELL_PATH, follow_redirects=True)
    _sign_out(client, shell.text)

    direct = client.get(SHELL_PATH, follow_redirects=False)
    assert direct.status_code in (301, 302, 303, 307, 308), (
        f"after sign-out, GET {SHELL_PATH} returned {direct.status_code} "
        "directly instead of redirecting to sign-in"
    )


# ---------------------------------------------------------------------------
# Group 4 — US-032 accessibility
# ---------------------------------------------------------------------------


def test_us_032_ac_04_stylesheet_has_a_reduced_motion_media_query(client):
    """theme.txt specifies 700-1000ms motion for some transitions; without
    an explicit reduced-motion override, AC-04 fails by construction."""
    page = client.get(SIGN_IN_PATH, follow_redirects=True)
    css = _fetch_single_stylesheet(client, page.text)

    block = _extract_braced_block(
        css, r"@media\s*\(\s*prefers-reduced-motion\s*:\s*reduce\s*\)"
    )
    assert block is not None, (
        "no @media (prefers-reduced-motion: reduce) block found in the "
        "stylesheet"
    )
    assert re.search(r"\b(transition|animation)\b", block), (
        "the reduced-motion block exists but does not mention transition "
        "or animation at all, so it neutralises nothing"
    )


def test_us_032_ac_04_failed_sign_in_explains_itself_in_text(client, migrated_database):
    password = "g4-text-correct-horse-battery"
    with _committed(migrated_database) as c:
        user = _create_user(c, role="manager", password=password)

    rejected = _post_sign_in(client, user["email"], password + "-wrong")
    alerts = _elements_with_attr(rejected.text, "role", "alert")
    alerts += _elements_with_attr(rejected.text, "aria-live")
    assert alerts, "no accessible text region explains the failed sign-in"
    text = alerts[0][2]
    assert len(text) > 3 and re.search(r"[A-Za-z]", text), (
        f"the 'error' region's text is not real explanatory text: {text!r}"
    )


def test_us_032_ac_04_failed_sign_in_is_announced_not_only_coloured(
    client, migrated_database
):
    """AC-04: color is never the only signal. The structural check here is
    that an accessible announcement mechanism (role=alert or aria-live)
    carries the message -- a CSS class alone (e.g. class="error" with no
    announcement) would fail this, which is exactly the risk AC-04 names.
    Whether the colour itself has sufficient contrast is a rendered-pixel
    question this file cannot answer; see the session report."""
    password = "g4-announce-correct-horse-battery"
    with _committed(migrated_database) as c:
        user = _create_user(c, role="manager", password=password)

    rejected = _post_sign_in(client, user["email"], password + "-wrong")
    announced = _elements_with_attr(rejected.text, "role", "alert") or \
        _elements_with_attr(rejected.text, "aria-live")
    assert announced, (
        "the failed sign-in has no role=alert / aria-live region at all -- "
        "if the only signal is a CSS class, assistive technology never "
        "hears about the failure"
    )


def test_us_032_ac_01_stylesheet_defines_a_visible_focus_style(client):
    page = client.get(SIGN_IN_PATH, follow_redirects=True)
    css = _fetch_single_stylesheet(client, page.text)

    focus_rules = re.findall(r"[^{}]*:focus(?:-visible)?[^{}]*\{([^{}]*)\}", css)
    assert focus_rules, "no :focus or :focus-visible rule exists in the stylesheet"

    has_visible_indicator = any(
        re.search(r"box-shadow\s*:", decls)
        or re.search(r"outline\s*:\s*(?!none\b|0\b)", decls)
        or re.search(r"border(?:-color)?\s*:", decls)
        for decls in focus_rules
    )
    assert has_visible_indicator, (
        "every :focus/:focus-visible rule found neutralises the indicator "
        "(or declares nothing visible) rather than providing one"
    )


def test_us_032_ac_01_no_blanket_outline_none_without_a_replacement(client):
    page = client.get(SIGN_IN_PATH, follow_redirects=True)
    css = _fetch_single_stylesheet(client, page.text)

    for m in re.finditer(r"([^{}]*)\{([^{}]*)\}", css):
        selector, decls = m.group(1), m.group(2)
        if re.search(r"outline\s*:\s*(none|0)\b", decls):
            assert re.search(r"box-shadow\s*:", decls), (
                f"selector {selector.strip()!r} removes the default focus "
                "outline (outline: none/0) without declaring a replacement "
                "(no box-shadow in the same rule) -- AC-01 requires a "
                "visible focus indicator to survive this removal"
            )


def test_us_032_ac_02_failed_submit_does_not_lose_entered_data(client, migrated_database):
    password = "g4-ac02-data-correct-horse-battery"
    with _committed(migrated_database) as c:
        user = _create_user(c, role="manager", password=password)

    rejected = _post_sign_in(client, user["email"], password + "-wrong")
    assert user["email"] in rejected.text, (
        "AC-02: a failed submit must not lose the data the user already "
        "entered"
    )


def test_us_032_ac_02_failed_submit_never_shows_false_success(client, migrated_database):
    password = "g4-ac02-success-correct-horse"
    with _committed(migrated_database) as c:
        user = _create_user(c, role="manager", password=password)

    rejected = _post_sign_in(client, user["email"], password + "-wrong", follow_redirects=False)
    _assert_route_exists(rejected, what="POST /sign-in with a wrong password")
    if rejected.status_code in (301, 302, 303, 307, 308):
        assert SHELL_PATH not in rejected.headers.get("location", ""), (
            "a failed sign-in redirected to the protected shell -- false success"
        )
    assert not _can_reach_shell_directly(client), (
        "AC-02: a failed submit must never show false success -- the shell "
        "is reachable after a rejected sign-in"
    )


# ---------------------------------------------------------------------------
# Group 5 — theme-agnostic engineering hygiene
#
# None of the four tests below pin a literal value from theme.txt (see the
# module docstring / B-16). Each instead asserts the underlying engineering
# or accessibility property the old, literal-value tests happened to also
# be checking -- true regardless of which palette or fonts eventually get
# chosen.
# ---------------------------------------------------------------------------


def test_ui01_g5_color_tokens_are_declared_once_as_css_custom_properties(client):
    """The *shape* kept from the original test: a color palette exists as
    CSS custom properties (``--name: value;``), each declared exactly
    once -- not twice, which is how two definitions can silently drift
    apart. The minimum count below is a disclosed judgement call for "a
    real palette exists" (a handful of tokens), not a literal count any
    document specifies, and no particular hex value is required anywhere."""
    page = client.get(SIGN_IN_PATH, follow_redirects=True)
    css = _fetch_single_stylesheet(client, page.text)

    counts = _custom_property_counts(css)

    MINIMUM_CUSTOM_PROPERTIES = 5
    assert len(counts) >= MINIMUM_CUSTOM_PROPERTIES, (
        f"found only {len(counts)} CSS custom propert"
        f"{'y' if len(counts) == 1 else 'ies'} declared at all "
        f"({sorted(counts)!r}) -- expected at least "
        f"{MINIMUM_CUSTOM_PROPERTIES}, enough for a real design-token "
        "palette rather than a couple of one-off variables"
    )

    duplicated = {name: n for name, n in counts.items() if n > 1}
    assert not duplicated, (
        f"custom propert{'y' if len(duplicated) == 1 else 'ies'} declared "
        f"more than once: {duplicated!r} -- a token must be declared "
        "exactly once or two definitions can silently drift"
    )


def test_ui01_g5_templates_do_not_hardcode_hex_colors_in_markup(client, migrated_database):
    password = "g5-hex-correct-horse-battery"
    with _committed(migrated_database) as c:
        user = _create_user(c, role="manager", password=password)

    sign_in_resp = client.get(SIGN_IN_PATH, follow_redirects=True)
    _post_sign_in(client, user["email"], password)
    shell_resp = client.get(SHELL_PATH, follow_redirects=True)

    hex_re = re.compile(r"#[0-9a-fA-F]{3,8}\b")
    data_uri_re = re.compile(r'url\(["\']?data:[^)"\']*["\']?\)')

    for label, resp in (("sign-in", sign_in_resp), ("shell", shell_resp)):
        # An absence check (no hex found) is vacuously true against a bare
        # 404 body -- require a real rendered page first.
        assert resp.status_code == 200, (
            f"the {label} page did not render at all ({resp.status_code}) "
            "-- the hex-color absence check below would pass vacuously"
        )
        cleaned = data_uri_re.sub("", resp.text)
        found = hex_re.findall(cleaned)
        assert not found, (
            f"{label} page markup hardcodes hex color(s) {found!r} instead "
            "of going through CSS custom properties -- this is exactly how "
            "a later screen could drift from the design system"
        )


def test_ui01_g5_base_text_color_meets_a_minimum_contrast_ratio_against_its_background(
    client,
):
    """Replaces the old exact-hex pin (``resolved == FOREGROUND_HEX``) with
    a real, objective, theme-agnostic floor: WCAG AA for normal body text
    requires a contrast ratio of at least 4.5:1 between text and the
    background it sits on. This holds no matter which palette is chosen --
    it is not a theme.txt value.

    Source-level approximation, not a rendered computed-style check (see
    decision 5 above): resolves ``body``'s (or ``html``'s) ``color``
    declaration, and the page background's declared color (see
    ``_background_color_declaration``'s docstring for the disclosed
    body-then-html preference used when it is ambiguous which element the
    background is declared on), each through one level of ``var(--x)``
    indirection against the stylesheet's own ``:root`` declarations, then
    computes the actual WCAG relative-luminance contrast ratio between
    them."""
    page = client.get(SIGN_IN_PATH, follow_redirects=True)
    css = _fetch_single_stylesheet(client, page.text)

    root_vars = {name: value for name, value in _custom_properties(css)}

    text_declaration = _body_color_declaration(css)
    assert text_declaration is not None, (
        "no rule for `body` (or `html`) declares a `color` at all"
    )
    background_declaration = _background_color_declaration(css)
    assert background_declaration is not None, (
        "no rule for `body` (or `html`) declares a `background-color` or "
        "`background` at all -- there is nothing to compute contrast "
        "against"
    )

    text_resolved = _resolve_color(text_declaration, root_vars).strip()
    background_resolved = _resolve_color(background_declaration, root_vars).strip()

    text_rgb = _parse_rgb(text_resolved)
    background_rgb = _parse_rgb(background_resolved)
    assert text_rgb is not None, (
        f"body text color resolved to {text_resolved!r}, which this "
        "hex/rgb()/rgba()-only parser cannot evaluate as a color"
    )
    assert background_rgb is not None, (
        f"background color resolved to {background_resolved!r}, which "
        "this hex/rgb()/rgba()-only parser cannot evaluate as a color"
    )

    ratio = _contrast_ratio(text_rgb, background_rgb)
    WCAG_AA_NORMAL_TEXT_MINIMUM = 4.5
    assert ratio >= WCAG_AA_NORMAL_TEXT_MINIMUM, (
        f"body text ({text_resolved}) against its background "
        f"({background_resolved}) has a contrast ratio of {ratio:.2f}:1 -- "
        f"WCAG AA for normal body text requires at least "
        f"{WCAG_AA_NORMAL_TEXT_MINIMUM}:1"
    )


def test_ui01_g5_typography_tokens_are_declared_as_variables(client):
    """Not which fonts are chosen (that is theme.txt mood/inspiration, not
    a rule -- see module docstring) but a real engineering-hygiene
    requirement kept from the original test: heading typography and body
    typography must each be driven by their *own*, independently-settable
    CSS custom property -- two distinct tokens, not one font applied
    everywhere -- so a later re-theme changes two ``:root`` lines instead
    of hunting every template for a hardcoded font name.

    Disclosed assumption: since no document names the actual tokens, a
    typography token is identified here by a naming convention (the
    property name contains "font" together with a heading-ish or
    body-ish word), not by its value -- the same kind of documented,
    change-here-not-there assumption as the route constants above."""
    page = client.get(SIGN_IN_PATH, follow_redirects=True)
    css = _fetch_single_stylesheet(client, page.text)

    values = _custom_property_last_values(css)

    def _is_font_token(name: str) -> bool:
        return bool(re.search(r"font", name, re.IGNORECASE))

    heading_vars = {
        name: value
        for name, value in values.items()
        if _is_font_token(name)
        and re.search(r"heading|display|serif", name, re.IGNORECASE)
    }
    body_vars = {
        name: value
        for name, value in values.items()
        if _is_font_token(name) and re.search(r"\bbody\b|\bbase\b|\bsans\b", name, re.IGNORECASE)
    }

    assert heading_vars, (
        "no CSS custom property looks like a heading/display typography "
        "token (a name containing both 'font' and 'heading'/'display'/"
        "'serif') -- heading typography must be driven by its own "
        "variable, not a hardcoded font-family value repeated in templates"
    )
    assert body_vars, (
        "no CSS custom property looks like a body typography token (a "
        "name containing both 'font' and 'body'/'base'/'sans') -- body "
        "typography must be driven by its own variable"
    )
    assert not (set(heading_vars) & set(body_vars)), (
        "at least one custom property name was classified as both a "
        "heading and a body typography token -- the naming convention "
        "this test relies on is ambiguous for it"
    )


# ---------------------------------------------------------------------------
# Group 6 — project invariants
# ---------------------------------------------------------------------------


def test_ui01_g6_shared_badge_partial_never_collapses_to_a_single_status_value(client):
    """walmart-user-stories.md:1242 / Invariant #1: fulfillment, refund,
    reconciliation, and period protection are four independent dimensions,
    never merged behind one `status` prop. This screen has no reason to
    render a status badge at all -- but the day a shared badge/pill Jinja
    macro is introduced anywhere under src/templates, a macro whose *only*
    parameter is named `status` or `state` is exactly the collapse this
    invariant forbids, and this test is written to catch it on day one
    rather than once four screens already depend on the wrong shape.

    Right now there is no src/templates directory at all, so this fails on
    that absence -- which is the correct, honest failure for a screen that
    has not been built, not a vacuous pass for an invariant nobody checked.
    """
    import importlib.util
    from pathlib import Path

    spec = importlib.util.find_spec("src")
    assert spec is not None and spec.submodule_search_locations
    src_root = Path(next(iter(spec.submodule_search_locations)))
    templates_root = src_root / "templates"

    assert templates_root.is_dir(), (
        f"{templates_root} does not exist yet -- there is nothing to scan "
        "for a collapsed-status badge macro. This is the expected failure "
        "today; once templates exist, the scan below is the real check."
    )

    offending: list[str] = []
    macro_re = re.compile(
        r"\{%-?\s*macro\s+(\w*(?:badge|pill|chip)\w*)\s*\(\s*(status|state)\s*\)",
        re.IGNORECASE,
    )
    for template_file in templates_root.rglob("*.html"):
        text = template_file.read_text(encoding="utf-8")
        for m in macro_re.finditer(text):
            offending.append(f"{template_file}: macro {m.group(1)}({m.group(2)})")

    assert not offending, (
        "found a badge/pill macro accepting a single collapsed status/state "
        f"value: {offending!r} -- fulfillment, refund, reconciliation, and "
        "period protection must stay independent"
    )


def test_ui01_g6_no_money_decimal_or_currency_is_introduced_by_this_screen(
    client, migrated_database
):
    password = "g6-money-correct-horse-battery"
    with _committed(migrated_database) as c:
        user = _create_user(c, role="manager", password=password)

    sign_in_html = client.get(SIGN_IN_PATH, follow_redirects=True).text
    sign_in_css = _fetch_single_stylesheet(client, sign_in_html)
    _post_sign_in(client, user["email"], password)
    shell_html = client.get(SHELL_PATH, follow_redirects=True).text

    combined = sign_in_html + sign_in_css + shell_html

    assert not re.search(r"\bDecimal\b", combined), (
        "the sign-in/shell screens mention 'Decimal' -- this screen has no "
        "monetary content"
    )
    assert not re.search(r"\$\d", combined), (
        "a dollar amount appears to be rendered on the sign-in/shell screens"
    )
    assert not re.search(r"\b(USD|EUR|GBP)\b", combined), (
        "a currency code appears on the sign-in/shell screens"
    )


def test_ui01_g6_shell_does_not_disclose_a_store_outside_the_callers_scope(
    client, migrated_database
):
    password = "g6-scope-correct-horse-battery"
    own_store_name = f"G6 Own Store {uuid.uuid4().hex[:8]}"
    other_store_name = f"G6 Other Store {uuid.uuid4().hex[:8]}"
    with _committed(migrated_database) as c:
        own_store = _create_store(c, own_store_name)
        other_store = _create_store(c, other_store_name)
        user = _create_user(c, role="manager", password=password)
        _assign(c, user["id"], own_store)
        # other_store is deliberately never assigned to this user.
        other_user = _create_user(c, role="manager", password="g6-other-password")
        _assign(c, other_user["id"], other_store)

    _post_sign_in(client, user["email"], password)
    shell = client.get(SHELL_PATH, follow_redirects=True)

    assert own_store_name in shell.text, "setup failed: the assigned store never rendered"
    assert other_store_name not in shell.text, (
        "a store outside the signed-in user's scope is disclosed in the shell"
    )
    assert str(other_store) not in shell.text
