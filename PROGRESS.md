# Progress

Volatile session state. **Update this at the end of every session.**

Stable rules live in `AGENTS.md` and `docs/`. Nothing in this file is a business rule ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â if you find one here, it is in the wrong place.

---

## Current position

**US-005a implemented and audited to a pass (`663eaae`), two rounds.** AC-01
and AC-02 of US-005 ("Import and refresh orders") both covered by passing
tests. 295 passed, 3 skipped; guardrail scan clean, 28 files, 0 suppressions.
Full account in its own section below, ahead of slice 0.

**Slice 0: COMPLETE** -- the step 5 guardrail proof was demonstrated 2026-10-24 (see below). **Slice 1:** US-001 done and audited. US-002 split; US-002a implemented, audited and committed (`10b6fda`) -- closable once the Owner pastes the B-7 amendment into `docs/` (B-10). US-003 implemented and audited through to a pass (`cda7b0e`); not fully closable -- AC-02's third clause is blocked on B-11 and the story carries B-12, B-13, B-14. **US-004 implemented and audited to a clean pass** (`a9ea6aa`, two audit rounds, both verified independently rather than on reported counts) -- all four ACs covered by a passing test; carries B-15 (new) plus eight non-blocking engineering carry-forwards (F-73...F-81). **All four of slice 1's stories now exist in code and are audited.** The slice itself is not "done" by this project's own definition (every AC passing + guardrail green + **deployed**) -- B-5 (no deployment target chosen) is still open, and B-10/11/12/13/14/15 remain Owner items.

**This session: `.env` credentials fixed and verified against the live
Walmart API (B-3 closed); `walmart-probe` completed a full recording pass
and surfaced one serious new contradiction (B-18 -- a real order violates
BR-02's "one SKU per order" guardrail). A spec-oracle brief for US-005b
was also produced and returned seven open items (G-1...G-7), two of which
are judgement calls rather than clean reads. Full detail in the "US-005b"
section below and in the blockers table.**

**A second, unrelated item surfaced mid-session:** an uncommitted test file (`tests/test_ui001_sign_in_screen.py`) and an untracked root-level `theme.txt` were found sitting in the tree from an apparently-interrupted prior session. `theme.txt` read as a binding "design-system contract" (exact hex palette, Google Fonts, mandatory SVG grain overlay, pill buttons, arch imagery) that **contradicted** `docs/walmart-store-manager-design.md`'s own UX baseline. Escalated as **B-16** rather than built on, deleted, or silently rewritten. **The Owner closed it the same session**: `theme.txt` is intentional for this project but advisory only -- "the idea and inspiration," not a literal palette/font/technique requirement, and does not override the data-dense/sober baseline. See the Decisions log.

**UI-01 (sign-in screen and authenticated shell) built on the strength of that ruling.** The test file was revised first (test-author, before any implementation) to drop every literal-`theme.txt`-value assertion and replace it with the underlying theme-agnostic engineering/accessibility property instead (a real WCAG contrast floor, token-declared-once discipline, independently-settable heading/body typography) -- committed `a1b61c7`. The screen was then implemented against that revision (`src/templates/`, `src/static/css/app.css`, `src/web/routes.py`, committed `fd2b0fe`) and audited to a **clean pass on the first round** -- no blocking findings, no fix round needed. **34 passed, 1 skipped** (F-13, unchanged) against that file alone, **262 passed, 3 skipped** for the full suite, guardrail scan clean (24 files, 0 suppressions). No new business rule was implemented -- every route calls `access_service.sign_in` / `authenticate` / `resolve_scope` / `sign_out` and `stores_repository.list_stores` exactly as the JSON API already does, through a cookie instead of a bearer header. Full detail below, in its own "UI-01" section ahead of US-004.

---

## US-005b -- import run visibility and freshness (spec-oracle brief only, step 1 of 4)

AC-03 only: "Given a failed refresh, when viewing the list, then the last
successful update and failure are visible; stale data is not labeled
current" (`walmart-user-stories.md:791`). `spec-oracle` ran once, reused
nothing from the US-005a brief (this is a distinct AC with its own schema
surface), and returned a genuinely open brief rather than a clean one --
seven items, not forced into a false resolution.

**What exists already, confirmed against the running code, not assumed:**
`orders.last_imported_at` (`database-schema.md:454`, explicitly cited to
this AC) is already a column, already populated on every import by
US-005a's service (`src/orders/service.py:264`). The "last successful
update" half of AC-03 already has its data source. The "failure ...
visible" half does not: `import_batches`/`import_errors`
(`database-schema.md:965-998`) are fully specified in the schema document
but **no migration has ever created them** -- confirmed by search, zero
hits in `src/`.

**Seven gaps, none filled by inference:**

| # | Gap | Resolution path |
|---|---|---|
| G-1 | No endpoint lists import batches by store ("most recent run for store X"). `GET /imports/orders/{batchId}` (`api-service-design.md:820`) needs an id the caller must already hold -- a user viewing the list later has no documented way to get one. | Engineering gap, not a business rule; build what's needed, disclose the invented shape, same posture as B-12's `store_created` event name. |
| G-2 | No audit event type exists for an import batch's completion, failure, or partial result (checked all 19 rows of the catalogue, `api-service-design.md:1182-1209`). | Same as G-1 -- invent, disclose, confirm later. |
| G-3 | No capability-matrix row says who may *view* import history, distinct from who may *trigger* an import (`:674`, Yes/Yes/Yes -- a different question). | Same shape as B-13/B-15 -- apply the narrowest-reading interim ruling, log as a new blocker. |
| G-4 | No staleness threshold or freshness policy value exists anywhere (no `period-v1` clause, no policy table, exhaustive grep empty). | Not actually blocking: AC-03's own text only requires "not labeled current" after a failure, not a computed time-based stale/fresh boolean. Satisfiable without inventing a threshold. |
| G-5 | "The list" in AC-03's own sentence is not pinned to a screen or endpoint. | Resolvable by the narrowest supportable reading -- the orders list (UI-04), by analogy to US-006 AC-03's identical phrase (`:807`) -- not a document statement, a judgement call, disclosed as such. |
| G-6 | Whether AC-03 is satisfiable API-only (the precedent every story since US-002a has used) or requires a rendered screen -- its phrasing ("when viewing the list") reads more UI-shaped than any prior story's AC language did. | **Not resolved by the brief itself, deliberately** -- bigger scope commitment than the other six, flagged for a call before test-author runs. |
| G-7 | `import_batches.status_code` (`pending`/`processing`/`completed`/`failed`/`partial`) has no documented transition rule -- what makes a batch `failed` vs `partial` is unspecified. | Needed before a test fixture can deterministically produce "a failed refresh" (AC-03's own Given clause). Narrow it for this story: a batch that raises before any row is written is `failed`; anything else is out of scope for AC-03 specifically. |

**Status: step 1 of 4 complete, test-author not yet run.** Two calls (G-5,
G-6) are judgement calls serious enough that they're surfaced to the Owner
rather than folded into "the implementer's reasoned discretion" the way
G-1/G-2/G-7 are -- G-6 in particular changes how big this story is (API
response shape only, vs. a rendered orders-list screen, which no earlier
slice-2 story has built).

---

## US-005a -- import and refresh orders

Full pipeline, `spec-oracle` -> `test-author` -> `story-implementer` ->
`invariant-auditor`, **two audit passes**, both independently verified by me
(diffs, test runs, and the scanner re-executed myself rather than taken from
either agent's report). Final state `663eaae`.

US-005 is split three ways (see Decisions log, below): **US-005a is AC-01 and
AC-02 only.** AC-03 is US-005b. AC-04 through AC-08 (posting-period
assignment under `period-v1`) are US-005c, not started -- it is blocked on
**B-17**, a new gap this session surfaced (no story owns accounting-month
provisioning, and US-005c consumes provisioned months it cannot create).

| AC | Status |
|---|---|
| AC-01 all eight named fields preserved exactly | **covered** |
| AC-02 no duplicate row, identity retained across refresh, per-store not global uniqueness | **covered**, including the idempotent-replay path (closed in the fix round, below) |

**`spec-oracle` ran once, covering all three sub-stories' shared ground, and
the brief was reused verbatim downstream** rather than re-derived per agent
-- the fix for the previous session's acknowledged waste (four agents each
re-reading the same schema and story documents). It returned three real
gaps, each ruled on under the established B-13/B-15 narrowest-reading
precedent rather than escalated for a round-trip the Owner had already said
they didn't want: "a supported order payload" is undefined (C-02 turns out
to govern settlement reports, not orders -- no unsupported-payload branch
was built); month provisioning is unowned (**B-17**, not folded into any of
005a/b/c); an inactive store refuses writes, visibly (**B-9**, interim). It
also settled the retrofit question that shaped the whole migration:
`orders` must carry its **full** documented column shape -- including
`accounting_month_id` (nullable, never derived, DB-06), `scope_status_code`,
`posting_status_code` -- in its *first* migration, even though US-005a
populates only part of it, and `accounting_months` must exist at full shape
purely to satisfy the FK, empty and unpopulated.

**What shipped.** One migration (`003_orders.sql`, both `orders` and
`accounting_months` at full documented shape), one new module (`src/orders/`
-- repository, service, following `src/stores/`'s and `src/team/`'s shape),
one endpoint (`POST /imports/orders` in `src/main.py`, inline with the
existing stores/team endpoints rather than a separate router file, matching
established precedent). No role gate -- Owner, Admin, Manager all import,
per the capability matrix row (`walmart-user-stories.md:674`) -- the only
authorization control is store scope, enforced as a SQL predicate inside
`stores_repository.get_store` (the F-22 trap from US-003, re-checked and
confirmed still avoided). Upsert keyed on `(store_id, walmart_order_id)` --
a real per-store unique constraint (DB-12), `UPDATE ... SET` over named
source-owned columns, never delete-then-insert, `id`/`created_at` proven to
survive a refresh unchanged. `fulfillment_status_code` is DDL-constrained to
exactly four values (DB-01); `refunded`/`settlement_pending`/`reconciled`
rejected at the database layer, not only in application code.

**One disclosed, reasoned decision beyond the two literal ACs**:
`scope_status_code` (`managed`/`pre_onboarding`, BR-14) is computed and
written on every row -- not deferred to US-005c -- because defaulting every
order to `managed` would be factually wrong for a genuinely pre-onboarding
order and would silently include it in a future financial total, which is
AGENTS.md non-negotiable #8 directly. `posting_status_code` follows from
documented defaults (`out_of_scope` for pre-onboarding; `unpostable_no_period`
for managed, honestly true since nothing provisions a month here).
Flagged in the module's own docstring as correct-by-construction but
**unproven by any test in this story's file** -- every fixture store's
`managed_from` lands every test order as `managed`. Carried forward as
**F-90**, first thing for US-005c to close.

### Pass 1 (2 blocking)

**F-88, the sharp one.** No test reused an `Idempotency-Key`, or omitted one
beyond a single 422 check. The auditor proved it by deletion: the pre-check,
the `store_id` guard inside the replay-target check, the concurrency
backstop, and **the audit write itself** could all be deleted and the suite
stayed green. The reachable defect: a key spent on an import into store A,
reused on an import into store B, returns `200 {"replayed": true}` carrying
store A's order ids, having written nothing to store B -- the caller is told
the import succeeded. This is the F-72 shape from US-004 recurring, one
level worse (the audit write itself was unproven, not just a guard clause).

**F-89.** The suite was red: a schema test failed on `.fetchone()[0]`
against a `dict_row`-configured connection -- a genuine bug in the
test-author's own committed file, unrelated to the implementation,
confirmed independently by the auditor reading `conftest.py`. Still
blocking, because `pytest` must be green and DB-08 ("the single
highest-risk regression," per the guardrail's own comment) had no passing
assertion at all.

**The fix round touched only the test file.** Three new tests closed F-88
(honest retry, cross-store key reuse, creation-key reuse against an
import) -- `_check_import_replay_target` itself was not touched, because it
was already correct, only unproven. F-89 closed with the one-line
`.fetchone()[0]` -> `.fetchone()["to_regclass"]` fix, no assertion
loosened. I verified the "tests only" constraint myself (`git diff --stat`,
full suite, scanner) before sending it back for pass 2, rather than trusting
the implementer's report.

### Pass 2: pass, with five non-blocking findings and one process note

The auditor traced each new test's deletion-proof **at the source**, not
from the test's own name or docstring -- and caught something the fix round
got wrong in the process: the honest-retry test's docstring claims deleting
the pre-check would be caught; it would not be. Deleting it routes through
the concurrency backstop instead, which produces an **observationally
identical** result (same status, same order id, one audit row) because
`audit.record` already converts a caught `UniqueViolation` into a clean
replay. The docstring misstates the mechanism -- carried as **F-90** (the
F-87 shape: a comment wrong about *why*, not *whether*, something works).
The pre-check and the backstop turned out to be **mutually redundant** under
any single-threaded test -- the suite proves *at least one* replay path
works, not which. Not blocking: no live defect, a missing pin on an already
over-determined guard.

**A repeat process gap, flagged by the auditor itself (F-94):** the entire
implementation was still uncommitted when pass 2 ran, so "confirm production
code is byte-for-byte unchanged since pass 1" was not a checkable claim by
git -- only by line-anchor inspection (four citations from pass 1 still
landing exactly on the same constructs, treated as strong but not equivalent
evidence). This is the third occurrence of the shape F-18 and the US-002a
`git checkout` incident already named. **Commit before requesting a second
audit pass, not after it passes** -- now done; `663eaae` holds the audited
tree.

### Carried forward from US-005a (non-blocking)

**F-90** `scope_status_code`/`posting_status_code` correct by reading,
unproven by any test -- every fixture store's `managed_from` lands every
order as `managed`. One test (a store with `managed_from` after the order's
date, asserting `pre_onboarding`/`out_of_scope`) closes it. First thing for
US-005c.
**F-91** the honest-retry test's docstring misstates which branch catches a
deleted pre-check (see above) -- fix the comment before this pattern is
copied elsewhere.
**F-92** the concurrency backstop (`service.py:280-288`) is structurally
present and unreachable by any single-threaded test -- same acknowledged
class as US-003's and US-004's equivalent backstops (F-75). Needs BT-04
concurrency work, not another audit pass.
**F-93** an honest replay returns `201`, a genuine second import of new
orders also can return `201` -- the two are not distinguished by status code
alone (a replay's `after_state` carries `created: true` from the original
call). Decide and pin a convention before a caller depends on distinguishing
them.
**F-94** (nit) the replay test never cross-checks the returned `order_id`
against the surviving database row's own `id` -- adequate today only because
of the uniqueness constraint; a direct comparison would close it outright.
**`StoreNotConfiguredForImport`** (409, missing `timezone` or `managed_from`)
is correct by reading and untested -- every fixture configures both. Reports
one missing item at a time rather than the full list `activation_blockers`
would give. Disclosed in the module's own docstring, F-70 shape.
A naive (non-UTC) `order_placed_at_utc` currently surfaces as a 500, not a
422 -- `clock.ensure_utc`'s bare `ValueError` is uncaught at the HTTP edge.
Untested, fails closed (nothing written).
`Decimal("NaN")`/`Decimal("Infinity")`/`"1e400"` all pass the request
model's validator and then fail deeper (at `Money` construction or at the
`numeric(12,2)` column itself) as an unhandled 500 rather than a 422.
Untested, fails closed.

### Unresolved, separate from the story: `.env`'s Walmart credentials

Mid-session the Owner stated `WALMART_CLIENT_ID` was already in `.env`; a
direct read showed otherwise (only `WALMART_SECRET_KEY`), confirmed
empirically via `walmart-probe`, which correctly refused to proceed rather
than synthesise fixtures against incomplete credentials -- recorded as **B-3,
reframed** in the previous session. The Owner then added two lines, but
**under the wrong names** (`clientID`/`clientSecret`, not
`WALMART_CLIENT_ID`/`WALMART_SECRET_KEY` -- nothing in `.env.example` or any
code reads those names) **and carrying a different secret value** than the
one already on the `WALMART_SECRET_KEY` line. Not resolved by me -- picking
which secret is current without knowing which portal screen it came from is
exactly the guess this project exists to avoid. **B-3 remains open**, now
for a third, more specific reason. See the blockers table.

---

## UI-01 -- sign-in screen and authenticated shell

Built against `tests/test_ui001_sign_in_screen.py` as committed at `a1b61c7`
-- the test-author's own revision, written *after* the Owner closed B-16
("`theme.txt` is inspiration, not a literal spec"), so every Group-5
assertion in that file is already theme-agnostic (a token declared once, two
independently-settable typography tokens, a real WCAG contrast floor) rather
than pinned to any exact hex or font name. Nothing in this file was
weakened, rewritten, or skipped to make it pass; the one skip already in it
(`test_f13_account_locked_disclosure_is_an_open_question`) is the
pre-existing, deliberate F-13 skip, untouched.

**No new business rule was implemented.** Every route in `src/web/routes.py`
calls a function `src/access/service.py` already implements and
`test_us001_sign_in.py` already proves: `sign_in`, `authenticate`,
`resolve_scope`, `sign_out`, plus `stores_repository.list_stores` for the
shell's store list. The only new work is transport -- an opaque,
server-validated session token carried in an `HttpOnly`, `SameSite=Lax`
cookie instead of an `Authorization: Bearer` header -- and presentation
(`src/templates/*.html`, `src/static/css/app.css`). `python-multipart` was
added to `requirements.txt`; its necessity was confirmed by reading the
installed Starlette's own `Request.form()` (`site-packages/starlette/
requests.py`), which asserts `parse_options_header is not None` before it
even looks at the content type, so the dependency is required for the
plain `application/x-www-form-urlencoded` sign-in post too, not only a
multipart/file upload, exactly as the test file's own docstring says.

**Routes added**, all in `src/web/routes.py`, registered on `app` via
`app.include_router` in `src/main.py` (plus one `app.mount("/static", ...)`
line in `main.py` itself, since an `APIRouter` cannot mount a
sub-application):

| Route | Behaviour |
|---|---|
| `GET /` | 200 html, anonymous landing page |
| `GET /sign-in` | 200 html form if not authenticated; 303 to `/app` if a valid session cookie is already present (never re-renders the password field to an authenticated visitor) |
| `POST /sign-in` | form-encoded `email`/`password`; success sets the cookie and 303s to `/app`; failure re-renders the form 200 with the typed email preserved, the password never echoed, and one generic, non-disclosing error message shared by every `AuthenticationFailed` subtype |
| `GET /app` | anonymous or invalid-cookie -> 303 to `/sign-in`, never a 200 with any protected content, even transiently; authenticated -> 200, shows the caller's email, a sign-out `<form>`, and exactly the stores `repository.list_stores` returns for their freshly-resolved scope -- no currency, no dollar amount, no `Decimal` anywhere |
| `POST /sign-out` | revokes the session server-side via `access_service.sign_out` (not merely a dropped cookie), deletes the cookie, 303s to `/sign-in` |

**Audit: one pass, clean, no fix round.** Verified independently (re-ran
the suite, re-read `src/web/routes.py` in full, traced the scope-freshness
call site, confirmed no second implementation of authentication/lockout/MFA
exists anywhere in the new module). Six non-blocking findings, none
touching a protected invariant:

- **F-82** -- `GET /app`'s and `_optional_session`'s `except SessionInvalid`
  branches are correct but untested (every "after sign-out" test in the
  file drops the cookie from the client entirely rather than leaving a
  merely-stale one, so the "cookie present but no longer valid" branch is
  never reached). **Fail-closed, not blocking**: deleting the branch still
  surfaces the shared `SessionInvalid` exception handler (`main.py`), which
  returns a 401 with the same non-disclosing body -- no protected content
  leaks either way, the cost is a worse error page for the single most
  common real-world case (an idle session), not a security hole. First
  thing to close when UI-02 opens.
- **F-83** -- scope *freshness* at `GET /app` (resolved fresh per request,
  never cached) is correct by reading the code but unpinned by any test;
  the existing freshness test (`test_us001_sign_in.py`) only covers the
  JSON/bearer call site. Latent until B-8's store-selection session row
  lands in US-002b.
- **F-84** -- the MFA-on-HTML-form disclosure (Owner/Admin signing in via
  the browser form gets the generic failure message, never a code prompt)
  is correct by construction (verified: `MfaRequired` is raised before the
  session is created, so no bypass is possible) but untested.
- **F-85** -- the comment disclosing the absent `secure` cookie flag
  (correct for local HTTP dev, needed once deployed behind TLS) cites
  "PROGRESS.md" generally rather than a specific blocker id -- the F-81
  shape recurring. Fold into **B-5** (deployment) explicitly rather than
  leaving a dangling reference.
- **F-86** -- `POST /sign-out` with no cookie at all (a harmless no-op) is
  untested.
- **F-87** -- a comment justifying an explicit `conn.commit()` after a
  caught `AuthenticationFailed` misstates *why* it's needed (the exception
  is caught inside the `with` block, so the context manager would commit
  on the clean `return` regardless) -- the commit itself is still correct
  and harmless, only the stated reasoning is wrong. Worth fixing before the
  comment is copied to a route where the distinction actually matters.

None of the six touch a protected invariant (no shared implementation was
duplicated, no state dimension collapsed, no policy value hardcoded, no
suppression added) -- the auditor's own recommendation was to accept and
carry these forward rather than force a second round.

**Disclosed gaps -- found by self-review before the audit even ran, per the
brief's own instruction to report rather than leave for an auditor:** the
MFA gap (F-84, above, disclosed in the route module's own docstring) and
the stale-cookie branch (F-82, confirmed empirically with a throwaway probe
script against the real cookie jar behaviour before being reported, not
assumed).

---

## US-004 -- manage store assignments

Full pipeline: `spec-oracle` -> `test-author` -> `story-implementer` ->
`invariant-auditor`, **two audit passes**, both verified independently
(commit hashes, diffs, and test runs re-executed myself, not taken from
either agent's own report). Final state `a9ea6aa`.

| AC | Status |
|---|---|
| AC-01 two Managers, one store, independent access | **covered** |
| AC-02 one Manager, two stores, no duplicate identity | **covered** |
| AC-03 revoked access denied on next request | **covered** (F-72 closed a real route to silently breaking this) |
| AC-04 audited; no retroactive effect | **covered** (the "historical orders" clause honestly deferred -- no `order_assignments` table exists yet; the currently-reachable form, store/user rows untouched, is tested and independently confirmed by reading the code) |

**Scope was clean going in, thanks to spec-oracle.** Before any test was
written, spec-oracle established: this story is Manager-assignment only
(every AC names Managers specifically; no document names who may assign an
*Admin* to a store -- left as a disclosed skip, not guessed); no
user-creation endpoint exists or is needed (AC-02 presumes the identity
already exists); the schema as already built (`user_store_assignments`, a
bare composite-PK table, no surrogate key, no timestamps) is sufficient for
every AC -- the richer `store_manager_assignments` table in
`database-schema.md` was never built and this story does not need it, so
**no migration was written**; and no AC requires a rendered screen, so this
was built API-only, same as US-001/002a/003.

**Pass 1 (3 blocking).** `GET /stores/{store_id}/managers` shipped with
*zero* test coverage (F-70). Its read authority had been set to
"any caller with scope to the store" -- letting a Manager enumerate every
other user assigned to their own store -- with no capability-matrix row
answering the question and no blocker raised for inventing the permissive
answer (F-71). And the sharpest one: the replay-target guard's extra
`store_id`/`action` checks (beyond what `audit.check_replay_target` itself
checks) were **real and correct but entirely unproven** -- delete either and
the suite stays green while a grant's `Idempotency-Key` reused for a revoke
silently "replays" as a no-op, leaving the Manager's access intact. That is
AC-03 breaking in exactly two HTTP calls, undetected (F-72).

**The fix round, not a new story.** F-70/F-72 closed with tests (seven new
tests; `_check_assignment_replay_target` itself was not touched -- it was
already correct, only unproven, and the implementer resisted the pressure
to "make the fix look real" by editing code that didn't need it). F-71 was
resolved the way this project already resolved an identical shape of gap --
**B-13**'s precedent: take the intersection of all readings, since the
narrower one cannot grant a power no document grants. `GET .../managers`
now gates on the same Owner-only check as grant/revoke, labelled **interim**
in both docstrings, explicitly *"NOT a confirmed rule."*

**Pass 2: pass, with one new non-blocking finding (F-81).** The auditor
could not mutate the committed guard clauses itself (read-only by design)
and used a source-level discrimination proof instead -- for each new F-72
test, it traced which single clause is the sole source of the 409 with the
other clause provably inert, which it judged stronger than a mutation run
because it shows *why*, not just *that*. F-81: the F-71 fix's own docstrings
said "flagged as a new blocker" without a blocker actually existing in
`PROGRESS.md` to point to -- closed by **this entry**, recorded as **B-15**.

### Carried forward from US-004 (non-blocking; engineering or Owner-decision debt, not a defect)

**B-15** (new, from F-81) -- **who may read a store's team list?** No
capability-matrix row or AC names this. Resolved *for now* the same way
B-13 was: narrowest-available reading (Owner-only), enforced, labelled
interim in `src/main.py:569-588` and `src/team/service.py:148-160`. Needs
an Owner ruling; until then the gate stays Owner-only.

**F-73** the grant/revoke discriminator (`action: "granted"/"revoked"`) is
pinned only by a `json.dumps(dict(row))` substring match over the whole
audit row -- would pass even if the string appeared in an unrelated field.
The *implementation* is correct (`after_state` is a real, dumpable jsonb
column); only the test's precision is weak. Tighten to
`row["after_state"]["action"] == "granted"` before this pattern is copied
elsewhere.
**F-74** revoke's role-ordering is only half-pinned: grant has both
directions (in-scope store *and* nonexistent store both 403 for a
Manager/Admin); revoke only has the in-scope direction tested.
**F-75** the `UniqueViolation` concurrency backstop (two racing fresh keys)
is structurally present but unexercised by any test -- same acknowledged
gap as US-003's equivalent backstop. The constraint name
(`user_store_assignments_pkey`) was verified against the migration's DDL,
not the live catalog (`psql` was unavailable to the auditor in its
sandbox) -- worth a one-time confirmation against the running database.
**F-76** `grant_manager` validates the *actor's* role but never the
*target's* -- an Owner can today assign an Admin or even another Owner as
a "manager" via this endpoint. No path reaches it except this one call
site, so nothing is silently exposed, but the disclosed skip
(`TestGapAdminAssignmentAuthorityIsUndefined`) is no longer an honest
"undecided" until this is named explicitly as a known, narrow gap.
**F-77** the two "missing Idempotency-Key" tests assert `422` only, not the
error's `loc` -- would pass for a coincidental 422 from some unrelated
cause.
**F-78** a raw `ForeignKeyViolation` -> 500 remains theoretically possible
if the target user or store is deleted between the pre-check and the
`INSERT` (narrow: users are deactivated, not deleted, today).
**F-79** revoking a never-assigned pair returns `200 {"replayed": false}`,
indistinguishable from a real revoke to the caller, writes no audit row,
and consumes no `operation_id`. Disclosed, untested, unspecified by any AC.
**F-80** (nit) a docstring typo: "Assign managers to stores to stores."

### The lesson this story adds to the register

US-003 found: *"When a fix adds a branch ahead of an existing one, re-prove
the existing branch is still reachable, or merge the two away."* US-004
adds the sibling case, from F-72: **a branch can be correct from the
moment it is written and still be worth nothing until a test is shown to
depend on it.** The guard clauses that make `_check_assignment_replay_target`
more than `audit.check_replay_target` already were right in the very first
commit (`da5b2fa`) -- pass 1 failed anyway, because "correct" and "proven"
are different claims, and this project's own register exists to stop the
first one being mistaken for the second.

Also worth keeping: this is the **first story that did not reach outside
its own named module** (US-003 reached into `audit.py`, `scoping.py`, and
slice-0's `add_store_note` three times). `src/team/*` only ever *calls*
`stores_repository.get_store` and `audit.*`; it edits neither. Not a rule,
but a sign the module boundaries drawn in slice 0 are holding.

---

## UI-01 -- sign-in screen and authenticated shell

Built against `tests/test_ui001_sign_in_screen.py` as committed at `a1b61c7`
-- the test-author's own revision, written *after* the Owner closed B-16
("`theme.txt` is inspiration, not a literal spec"), so every Group-5
assertion in that file is already theme-agnostic (a token declared once, two
independently-settable typography tokens, a real WCAG contrast floor) rather
than pinned to any exact hex or font name. Nothing in this file was
weakened, rewritten, or skipped to make it pass; the one skip already in it
(`test_f13_account_locked_disclosure_is_an_open_question`) is the
pre-existing, deliberate F-13 skip, untouched.

**No new business rule was implemented.** Every route in `src/web/routes.py`
calls a function `src/access/service.py` already implements and
`test_us001_sign_in.py` already proves: `sign_in`, `authenticate`,
`resolve_scope`, `sign_out`, plus `stores_repository.list_stores` for the
shell's store list. The only new work is transport -- an opaque,
server-validated session token carried in an `HttpOnly`, `SameSite=Lax`
cookie instead of an `Authorization: Bearer` header -- and presentation
(`src/templates/*.html`, `src/static/css/app.css`). `python-multipart` was
added to `requirements.txt`; its necessity was confirmed by reading the
installed Starlette's own `Request.form()` (`site-packages/starlette/
requests.py`), which asserts `parse_options_header is not None` before it
even looks at the content type, so the dependency is required for the
plain `application/x-www-form-urlencoded` sign-in post too, not only a
multipart/file upload, exactly as the test file's own docstring says.

**Routes added**, all in `src/web/routes.py`, registered on `app` via
`app.include_router` in `src/main.py` (plus one `app.mount("/static", ...)`
line in `main.py` itself, since an `APIRouter` cannot mount a
sub-application):

| Route | Behaviour |
|---|---|
| `GET /` | 200 html, anonymous landing page |
| `GET /sign-in` | 200 html form if not authenticated; 303 to `/app` if a valid session cookie is already present (never re-renders the password field to an authenticated visitor) |
| `POST /sign-in` | form-encoded `email`/`password`; success sets the cookie and 303s to `/app`; failure re-renders the form 200 with the typed email preserved, the password never echoed, and one generic, non-disclosing error message shared by every `AuthenticationFailed` subtype |
| `GET /app` | anonymous or invalid-cookie -> 303 to `/sign-in`, never a 200 with any protected content, even transiently; authenticated -> 200, shows the caller's email, a sign-out `<form>`, and exactly the stores `repository.list_stores` returns for their freshly-resolved scope -- no currency, no dollar amount, no `Decimal` anywhere |
| `POST /sign-out` | revokes the session server-side via `access_service.sign_out` (not merely a dropped cookie), deletes the cookie, 303s to `/sign-in` |

**Test results.** This file alone: **34 passed, 1 skipped** (the pre-existing
F-13 skip). Full suite: **262 passed, 3 skipped**, zero regressions in any
other test file. Guardrail scan: **clean, 24 files, 0 suppressions**.

**Disclosed gaps -- found by checking my own new routes for the shape of gap
US-003/US-004's audits kept finding (a branch that is correct but
unexercised), per the brief's own instruction to report this rather than
leave it for an auditor:**

- **`GET /app`'s `except access_service.SessionInvalid` branch, and the
  identical branch inside `_optional_session` (used by `GET /sign-in`), are
  both present but untested by this file.** Confirmed empirically, not
  assumed: `response.delete_cookie()` on sign-out causes the TestClient's
  own cookie jar to drop the cookie entirely (verified with a throwaway
  probe script against the real pool), so every "after sign-out" test in
  this file exercises the *no-cookie-at-all* branch (`if not token:
  redirect`), never the *cookie-present-but-the-token-no-longer-
  validates* branch (expired, idle-timed-out, or revoked through some
  other path while the cookie itself survives -- e.g. two tabs, or natural
  idle/absolute expiry). The code is the same guard `main._authenticate_
  request`'s header path already proves correct (`authenticate` raising
  `SessionInvalid` is exactly what `test_us001_sign_in.py` exercises for
  the JSON API), reused here rather than reimplemented -- but reuse of a
  proven function is not the same claim as "this call site, this branch,
  is exercised", and US-004's own register exists precisely to stop those
  two being mistaken for each other. Not fixed by adding a test myself:
  the brief is explicit that this is reported, not silently patched by
  rewriting the spec file beyond what was asked.
- **`POST /sign-out` with no cookie at all** (the `if token:` guard's false
  branch, a harmless no-op) is also untested -- lower risk, noted for
  completeness.
- **MFA for the HTML form is deliberately not built.** No test in this file
  signs in as an Owner or Admin through `POST /sign-in`; `access_service.
  sign_in` already raises `MfaRequired` for a privileged account with no
  TOTP code, and this form's single `except AuthenticationFailed` renders
  it as the same generic failure message, with no second field to collect
  a code. Disclosed in `src/web/routes.py`'s own module docstring as a
  narrow, deliberate gap, not a silent omission -- the same discipline the
  brief asked for.

---

## US-003 -- create and configure a store

**Five audit passes. Four of them failed.** Final state `cda7b0e`:
**195 passed, 1 skipped**, scanner clean (19 files, 0 suppressions), story file
alone against a fresh database **80+ passed, 1 skipped** -- hermetic.

| AC | Status |
|---|---|
| AC-01 stable identifier distinct from display name | **covered** |
| AC-02 missing configuration identified, not guessed | **partially covered** -- currency and timezone covered at DB *and* response; third clause blocked on **B-11** |
| AC-03 Manager rejected from creation and settings changes | **covered**, both matrix rows, both 403/404 directions, ordering pinned |
| AC-04 rename preserves identity and links | **covered** |

The one skipped test is AC-02's "required integration configuration" clause.
It is skipped with a reason citing B-11 so the gap is visible in every run,
**not invented**.

**Built API-only again.** The slice plan gives slice 1 `UI-01` and `UI-02`
(`walmart-slice-plan.md:80`) and neither exists. This was deliberate: none of
US-003's four ACs are UI criteria, and the spec says nothing normative about
either screen's content, so building one meant inventing a shell -- the US-002b
trap. But slice 1 is now three stories deep with no way for the Owner to see
any of it without reading tests. **Decide before US-004** whether the screens
are a story of their own or slice 1 closes blind.

### What each audit pass caught, because the pattern matters more than the list

- **Pass 1 (8 blocking).** No authentication gate on either write endpoint
  (F-28 -- this was F-01 recurring); AC-02 proven at the database but never at
  the response, where a guess actually reaches a user (F-29); the identity
  UPDATE carried no scope predicate and `scope` was not even a required
  argument (F-30, the F-22 class); replay bound to neither entity nor event
  type, so a key spent on a note made `POST /stores` return someone else's
  store with 200 (F-31).
- **Pass 2 (3 blocking).** `extra='ignore'` meant `PATCH
  {"reporting_currency_code": "EUR"}` returned **200 with the old value** --
  and that is the real column name; only the update mapping calls it
  `currency_code` (F-44). `add_store_note` was left running the replay pattern
  the same commit declared broken, while its docstring advertised itself as
  the pattern to copy (F-46).
- **Pass 3 (2 blocking).** **F-55 is the one worth remembering.** The pass-2
  fix for duplicate `walmart_store_key` resolved the uniqueness conflict
  *before* the `operation_id`, so an identical payload sent twice under one
  Idempotency-Key -- the exact case idempotency exists to serve -- was refused
  as a duplicate registration, while a *different* payload under the same key
  replayed happily. The honest retry punished, the sloppy one rewarded.
  Also F-56: `timezone` was never validated, so a store configured
  `"Mars/Olympus"` reported `activation_blockers == []`, i.e. "fully
  configured" -- AC-02 wrong in the confident direction.
- **Pass 4 (4 blocking).** All one family: creation-path behaviour that no test
  constrained. The `store_created` event type -- the token keeping two
  endpoints' idempotency keys apart -- was free to change (F-62). The F-55
  pre-check had **orphaned** the concurrency backstop, so F-31's and F-50's
  guarantees held on one of two paths (F-63). No test ever successfully
  *changed* a reporting currency (F-64).
- **Pass 5: pass.**

### The lesson to carry, stated as a rule

**Every fix round in this story created a new unaudited path.** Pass-1 fixes
produced F-44/F-45; the pass-2 fix produced F-55; the pass-3 fix produced
F-63/F-65. The sharpest form is F-63: a fix that adds a branch *ahead* of an
existing one silently orphans it, and the orphaned branch keeps its tests'
names without keeping their coverage.

> **When a fix adds a branch ahead of an existing one, re-prove the existing
> branch is still reachable, or merge the two away.**

Two more, both earned here:

> **A comment explaining why a test avoids an input is a defect report.**
> `tests:1832-1839` described the F-55 bug in full and used it to justify
> steering the test away from the input that would expose it. Escalate it;
> do not encode it.

> **When a finding is fixed on one endpoint, fix it on the sibling in the same
> commit.** F-37 pinned the rename audit event in pass 1; the identical gap on
> creation survived to pass 4 as F-62. F-44 forbade unknown fields on the store
> models; `StoreNote` kept accepting them until pass 4 (F-58).

### What the implementer got right, and should keep doing

It self-reported a weak test it caught during its own mutation run -- its first
F-59 test passed under mutation for the wrong reason -- rather than presenting
only the corrected version. That is the behaviour that makes mutation evidence
worth anything.

### Carried open from US-003 (engineering, no decision needed)

**F-67** timezone validity is enforced at the HTTP edge only; there is no
`CHECK` on `stores.timezone`, and `activation_blockers` still cannot tell
"unconfigured" from "configured with nonsense". `currency_code` has two layers,
timezone has one. Any future non-HTTP writer -- importer, fixture, migration --
re-opens F-56 unnoticed. Carry to whichever story first writes `stores` from
outside the API.
**F-69** `_replay_created_store` collapsed the replay *body*, but each call
site still passes its own `scope`, and the backstop call site is not pinned
against being handed `StoreScope.owner()`. Latent only while creation stays
Owner-only; live escalation the moment that widens.
**F-60** `_store_body` is no longer round-trippable through `PATCH` under
`extra="forbid"` (`id`, `walmart_store_key`, `activation_blockers` are
read-only). Correct behaviour; US-002b's store form will meet it immediately.
**F-51** an anonymous request now consumes a pool connection before being
rejected -- folds into the pending rate-limiting work.
**F-54** the story reached outside its named module three times (`audit.py`,
`scoping.py`, and slice-0's `add_store_note`). All three were forced by audit
findings and judged correct, but it is not precedent.

---

**US-002 was split, because it spans two modules.** AC-01 and AC-02 are
server-side (query layer + HTTP edge); AC-03 ("explicit discard/cancel before
leaving unsaved input") and AC-04 ("clear selected rows and incompatible
filters on switch") are UI-shell criteria. There is no UI shell, no form, no
filtered list and no selected row anywhere in `src/` yet, so AC-03/AC-04 could
only be built by inventing a shell *and* stub screens to have something to
discard and clear.

- **US-002a (AC-01, AC-02)** -- server half. Implemented, audited, committed
  as `10b6fda`. **One step from done:** the Owner must paste the B-7
  amendment into `docs/walmart-user-stories.md` after line 109. Until then
  the only written statement of the rule is a docstring citing a blocker id,
  which is not a specification.
- **US-002b (AC-03, AC-04)** -- UI shell. Unblocked on B-8 (session row), but
  still has no shell, form or filtered list to act on. Lands with slice 2.

**What US-002a shipped.** AC-02 needed no production code -- the US-001 scope
spine already satisfied it. AC-01 did: `is_active` was silently acting as a
visibility filter nobody had agreed to. `list_stores` appended `and is_active`
while `get_store` had no such filter, so a Manager assigned to a deactivated
store could not see it in `GET /stores` but could open it in full, 200, via
`GET /stores/{id}`. The Owner settled it (B-7, below); `include_inactive` was
removed outright rather than defaulted to `True`, so scope is the only filter
`list_stores` can compose and no future call site can reintroduce the
exclusion with one keyword argument.

**21 lines of production change, 14 tests, 110 passing**, scanner clean (18
files, 0 suppressions).

The AC-02 tests were green on first run, so there was no red step, and a test
that has never failed has never been shown to constrain anything. They were
validated by **mutation instead** -- admin treated as owner; `sql_filter`
returning `true` for an empty scope; the owner special case removed; 404
changed to 403; the store id interpolated into the 404 body; the scope filter
dropped from `list_stores`; `resolve_scope` ignoring `user_id`; `get_store`
filtering in Python after the fetch; a header present only when the store
exists; `and is_active` re-introduced. Each was caught by the expected test.
The AC-01/B-7 tests did have a real red step.

**A process failure worth not repeating.** Mutation testing reverted with
`git checkout -- <file>` while the implementation was still uncommitted, which
destroyed the B-7 fix. Audit pass 2 then ran against a tree with no fix in it
and correctly failed the story, contradicting a status report that claimed it
was green. The auditor caught it; the summary was the optimistic one. **Commit
before mutation testing**, and treat any report that disagrees with
`git diff` as wrong.

The auditor **failed the first pass** (F-21, below) and found three mutation
survivors, all now closed:

- **F-22** `get_store` could move its scope filter out of SQL into a Python
  `permits()` check after the row was already fetched, and the whole suite
  still passed. The row is read before being discarded -- a leak waiting for
  a log line. Now pinned by spying on the SQL actually sent.
- **F-23** the file was **not hermetic**. "Scope is filtered by *this* user"
  held only because `test_api.py` and `test_us001_sign_in.py` run first
  alphabetically and leave committed assignments behind. Dropping
  `where user_id = %s` from `resolve_scope` survived when the file ran alone
  against a fresh database -- which is exactly what it will meet once B-4
  lands. Both tests now seed a decoy user.
- **F-24** BT-08 compared status and body but not headers, so a header
  present only for an existing store survived.

`src/` now has an `access` module (US-001 -- sign in securely): `users`,
`user_sessions`, `user_store_assignments`, `access_policy` tables; password
hashing (bcrypt cost 12), session issue/validate/revoke, lockout, MFA
enforcement for Owner/Admin, and real store-scope resolution wired into
`main.request_scope`. **96 tests pass.** Scanner clean (18 files, 0
suppressions); schema self-test clean.

US-001 went through the full pipeline: `spec-oracle` -> tests-first ->
implement -> `invariant-auditor`. The auditor **failed the first pass with six
blocking findings** and a second pass with one. All twelve findings (F-01...F-12)
are closed. The significant ones, because they are the kind that would have been
expensive later:

- **F-01** there was no authentication gate at all -- a missing credential
  resolved to the same `StoreScope` as an authenticated user with no
  assignments, so the system could filter by scope but never *require* auth.
- **F-02** account-enumeration timing oracle (unknown email skipped bcrypt).
- **F-03** failed TOTP codes did not count toward lockout, leaving the Owner /
  Admin second factor with no brute-force brake at all.
- **F-04** deactivating a user did not invalidate their live sessions.
- **F-05** the plaintext password was echoed in FastAPI's 422 validation body.
- **F-12** the fix for F-08 *introduced* an automatic unlock path that was
  itself unaudited.

**Slice 0 step 5: DEMONSTRATED 2026-10-24. Slice 0 is now complete.**

Branch `guardrail-proof` (`9c98bf6`) added `src/_guardrail_probe.py` containing
`order_status = "shipped"`, and was pushed. Result:

| Job | Result |
|---|---|
| **Static invariant scan** | **failure, at step 4 "Scan source for prohibited patterns"** |
| Schema invariants | success |
| Tests | success -- pytest was entirely unaffected |

Against `main` (`6766b06`, clean) the same workflow is **success**. So the
scanner catches a violation, passes clean code, and a violation genuinely
**fails the build** rather than printing a warning nobody reads. Branch
deleted locally and on the remote; `src/` verified clean, exit 0, 18 files.

**The trap this proof had to avoid, and which an earlier attempt fell into.**
Two prior branches (`prove-guardrails-block`, `prove/guardrails-block`, both
at `3b27e44`) were pushed earlier the same day and both went red -- yet step 5
stayed open, correctly. `3b27e44` predates `752e231` ("Give CI the `APP_ENV`
it is designed to refuse to guess"), so those builds were failing for an
unrelated reason and proved nothing about the guardrail. **A red build is
worth exactly as much as a red test: nothing, unless it is red for the right
reason.** That is why the job/step breakdown above is recorded rather than a
bare "CI failed", and it is why the run was checked per-step: `Tests` and
`Schema invariants` both had to stay green for the result to mean anything.

Every "scanner clean" recorded before today was an unvalidated signal. From
this commit on it is a real one.

**Needs an Owner decision — blocked, not deferred:**
- **Endpoint surface.** The implementation uses `POST /auth/session` +
  `DELETE /auth/session`; `api-service-design.md:153-169` specifies
  `POST /auth/login`, `POST /auth/mfa/verify`, `POST /auth/logout`.
  `walmart-user-stories.md` names no paths, and `spec-oracle` confirms the API
  document is binding for endpoint shape where the authoritative document is
  silent. Either conform the code, or the Owner amends the API document.
  Recording it without doing one of those leaves the contradiction open.
- Per-account / per-IP rate limiting with exponential backoff (access-v1)
  is specified but has no window, multiplier, or cap given, and was left
  unimplemented rather than inventing numbers.
- The sign-in lockout threshold (5 attempts) and lockout duration (15
  minutes) in `access_policy` were chosen, not specified, because access-v1
  says only "temporary lockout after repeated failures." Both are
  configuration rows, not constants, so the Owner can revise either without
  a deploy -- but the initial values should be reviewed.
- **F-13 contradiction.** `ACCOUNT_LOCKED` is a 5-request account-existence
  oracle. `walmart-user-stories.md:90` wants a *temporary lockout* (only useful
  if the user is told); `api-service-design.md:160` wants identical wording for
  unknown account and wrong password. Both authoritative-ish, mutually
  exclusive. Resolve alongside rate limiting -- a per-IP limiter satisfies both.
- Password strength / breach-list policy (`walmart-user-stories.md:88`). No
  password-set flow exists yet for it to attach to; recorded in
  `passwords.py`'s docstring so it is not silently lost.
- **Migration immutability rule.** `002_access.sql` was edited after being
  applied locally (it had never been committed). Agree the boundary *now*:
  a migration becomes immutable when it merges to main, not when it is first
  applied locally. From slice 2 the golden fixtures make local resets
  expensive; once B-4 lands, a shared instance makes them impossible.

**Carried forward — engineering work, no decision needed:**
F-14 allowlist the Pydantic `ctx` keys before the first custom validator lands
(the current sanitiser is a denylist); F-15 error envelope does not match
`api-service-design.md:108-120`; F-16 unaudited defence-in-depth `MfaRequired`
branch; F-17 `/health` leaks `migrations_applied` to anonymous callers;
F-18 commit boundary (US-001 is one uncommitted blob, so the auditor could not
diff passes in isolation); F-19 `get_user_credentials` reads without
`FOR UPDATE`, so a concurrent threshold-crossing lock can be cleared without an
unlock event -- fold into the rate-limiting/BT-04 concurrency work;
F-20 the F-12 test asserts the event type but not `reason == "lock_expired"`.

**Carried into slice 3 (from audit finding F-11):** `money.py` makes "no floats"
and "currency is inseparable" structural, but "unknown is not zero" remains a
convention between call sites. Before the six cost components exist, add a
result type that carries *which* component is missing — BT-05 requires the
missing component to be named, and no guardrail currently catches a call site
that substitutes `Money.zero()`.

## Next action

**CHECKPOINT, 2026-10-28. Session stopped here deliberately -- "this one got
bigger," start the next session fresh rather than pushing on.** Working
tree is clean, everything below is committed (`f13ba5c`), suite green
(295 passed, 3 skipped), scanner clean (28 files, 0 suppressions).

**Exact next step, not yet started: re-run `spec-oracle` for US-005b before
`test-author`.** The brief on file (see the "US-005b" section below) answered
G-1 through G-7, but two of them were left for the Owner rather than decided
-- **both are now decided** and change the brief's own conclusions enough
that it needs a fresh pass, not a patch:

- **G-6 (screen vs API-only): decided -- build a real rendered screen.**
  "Screen would be fine. I wanna see things and interact." This is the
  first slice-2 story to build a screen as part of its own scope (every
  earlier slice-1/2 story deferred rendering to a dedicated UI story, e.g.
  UI-01). G-5 ("which list") is resolved by the same answer: the orders
  list, UI-04.
- **B-18 (the multi-SKU contradiction): decided -- reject the whole order
  at import time, never split, never partially imported, and the rejection
  must be visible, not a silent skip.** This was flagged as a live defect in
  US-005a's own already-audited, already-merged code
  (`src/orders/service.py`), not a US-005b design question -- the
  `(store_id, walmart_order_id)` upsert key has no SKU component, so two
  lines sharing one `purchaseOrderId` would silently overwrite one another
  today. **"Flag it somewhere" and AC-03's "failure ... visible" turn out to
  need the same infrastructure** -- a durable, visible record of an
  import-time problem (`import_batches`/`import_errors`,
  `database-schema.md:965-998`, specified but never migrated -- see G-1/G-2
  in the US-005b section). Building both together, sharing one migration,
  is deliberate, not scope creep: confirm this reasoning holds before
  committing to it.

**What the next session's spec-oracle call needs to resolve, specifically:**
1. Exact design for detecting a multi-SKU conflict -- both within one
   import batch (two `OrderImportItem`s, same `walmart_order_id`, different
   `sku`) and across repeated imports (a new item's `sku` disagreeing with
   an existing row's). The current code only has the first shape to protect
   against; the second is equally real and currently equally unprotected.
2. What "visible" concretely resolves to -- an `import_errors` row surfaced
   on the UI-04 orders list screen is the leading candidate, not yet
   confirmed against any doc citation.
3. Whether this is still one story (US-005b, now carrying both the screen
   and the B-18 fix) or needs splitting -- it touches `src/orders/` (backend
   logic, a revision to already-merged US-005a code) and a new `src/web/` +
   `src/templates/` surface (the screen itself). Two modules, but one AC
   requires both; previous splits (US-002, US-005 itself) were triggered by
   genuinely separable concerns, not by touching two directories as such.
   Decide this once the brief is back, the way US-002/US-005's splits were
   decided after their own spec-oracle briefs, not before.

**Do not skip straight to test-author.** The existing seven-item brief is a
good foundation but was written before either decision landed; at minimum
G-5/G-6's answers and the B-18 fix's design need folding in before a test
is written against them.

**US-005c (AC-04-08, posting-period assignment) still cannot start** until
**B-17** is answered -- nobody owns accounting-month provisioning, confirmed
by exhaustive search across every story in the document, not inferred.
Needs a new US-id or an explicit amendment naming an existing story.

**B-3 (`.env` credentials) is closed, verified against the live API.** Full
account in the "US-005a" section below and the Decisions log. One near-miss
caught and reverted in the process: real credentials had briefly landed in
the tracked `.env.example` template, caught before `git add`.

**Slice 1 is still not fully closable** -- B-5 (deployment) and B-10 through
B-15 remain open Owner one-liners. None of them block slice 2's code.

**Process note carried forward:** commit before requesting a second audit
pass, every time (F-94/F-18's lesson, repeated three times now across
US-002a, US-005a). Also carried: when a fix touches already-merged,
already-audited code from an earlier story (as the B-18 fix will, in
`src/orders/service.py`), that is not exempt from the same pipeline --
spec-oracle, a failing test first, implementation, then an audit pass --
even though the story itself (US-005a) is closed. The code is not closed;
the story's audit was a verdict on a point in time.

Carried from slice 0, now discharged: step 5 was demonstrated 2026-10-24.

## Slice status

| Slice | Stories | Status |
|---|---|---|
| 0 Skeleton | -- | **done.** Step 5 guardrail proof demonstrated 2026-10-24 |
| 1 Access and stores | US-001-004, UI-01 | US-001 done. US-002a (AC-01, AC-02) audited, blocked on B-10. US-002b (AC-03, AC-04) blocked on B-8, deferred to slice 2. US-003 audited to a pass, blocked on B-11; carries B-12, B-13, B-14. **US-004 implemented and audited to a pass** (`a9ea6aa`); carries B-15. **UI-01 (sign-in screen, authenticated shell) implemented and green** against `tests/test_ui001_sign_in_screen.py` (34 passed, 1 skipped alone; 262 passed, 3 skipped full suite). All four stories exist in code; **slice not deployed (B-5)** |
| 2 Orders | US-005…007 | **US-005 split three ways. US-005a (AC-01, AC-02) implemented and audited to a pass** (`663eaae`, 295 passed/3 skipped, scan clean). US-005b (AC-03) not started. US-005c (AC-04-08) blocked on B-17 |
| 3 Costing and estimates | US-008ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â¦014, US-030 | not started |
| 4 Settlement evidence | US-015ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â¦017 | not started |
| 5 Reconciliation | US-018ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â¦021, US-031 | not started |
| 6 Month close | US-022ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â¦025 | not started |
| 7 Reports | US-026ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â¦028 | not started |
| 8 Overview | US-033 | not started |

Full contents and per-slice traps: `docs/walmart-slice-plan.md`.

## Open blockers

| # | Blocker | Blocks | Owner |
|---|---|---|---|
| ~~B-1~~ | ~~Golden orders~~ | ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â | **Closed 2026-10-23.** 20 real cases loaded and verified |
| B-2 | **C-01 narrowed, not closed.** `walmart-probe` ran against the live store and proposed 13 settlement-category mappings in `docs/walmart/settlement-recon-v1.md`, each marked confirmed or explicitly uncertain, with one real category (`Fee/Reimbursement`) deliberately left unmapped and recommended for suspense rather than guessed. **No row has been applied to any running config/table** — the proposal is evidence, not a decision. Needs Owner review before `settlement_category_mappings` (or equivalent) is actually populated | Slice 4 commit | **You** — review `docs/walmart/settlement-recon-v1.md` and approve/amend the 13 proposed rows |
| ~~B-3~~ | ~~`.env` credential naming/value conflict~~ | -- | **Closed this session.** You renamed `clientID`/`clientSecret` to `WALMART_CLIENT_ID`/`WALMART_SECRET_KEY`, but left the old, pre-existing `WALMART_SECRET_KEY` line in place too — two lines, same name, two values. Verified empirically (`dotenv_values`) that `python-dotenv` resolves duplicate keys last-wins, so the app already saw the correct new pair; I deleted the stale first line for clarity (no behaviour change) and confirmed the resolved values were unchanged before and after. `walmart-probe` then authenticated successfully against the live API (`200` from Walmart's token endpoint) and completed a full fixture-recording pass. **Separately, caught and reverted a near-miss**: an earlier edit had pasted the live credential values into `.env.example` — the tracked template file, never meant to hold real secrets (its own line 1 says so) — uncommitted, caught before `git add`, reverted with `git checkout -- .env.example`. `.env` itself remains correctly gitignored throughout; confirmed with `git check-ignore -v` |
| ~~B-18~~ | ~~A real Walmart order contradicts BR-02~~ | -- | **Decided this session.** Reject the whole order at import time (never split it, never silently import only the first line); make the rejection visible, not a silent skip. This is the "reject visibly" shape the probe described, not a recommendation. **Spec amendment still pending, same relationship as B-7/B-10** — this is a rule about order-import behaviour and belongs in `docs/walmart-user-stories.md` (BR-02 itself, or a new AC), not only in this file or a code docstring, once the exact wording is worked out in US-005b's implementation. See Decisions log |
| B-17 | **Nobody owns accounting-month provisioning.** `walmart-slice-plan.md:106` assigns "the month provisioning job" to slice 2. `api-service-design.md:266` makes `POST /months/provision` the **only** way a month comes into existence, and D-09 (`walmart-user-stories.md:1154`) forbids import from ever creating one. A capability row exists for the *authority* (`:673`, Owner/Admin yes, Manager no) — but **no acceptance criterion in US-005, US-006, US-007, US-022–025 or US-030 builds the endpoint or the job.** Confirmed by exhaustive search, not inference. US-005c *consumes* provisioned months; consuming is not building. **Not folded into US-005a/b/c** — that would invent an owner | **US-005c** (AC-05/06/07 are untestable without it) | **You** — a new US-id, or amend an existing story to own it |
| B-4 | No Postgres instance provisioned | Slice 0 | **You** ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â pick Neon, Railway or RDS |
| B-5 | No deployment target chosen | Slice 0 "deployed" criterion | **You** |
| B-6 | No refunded order in the golden set. The refund and cost-recovery path is covered only by synthetic fixtures | Slice 5 real-data confidence | **You** ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â add 1ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Å“2 refunded orders to the workbook |
| ~~B-7~~ | ~~Inactive but assigned store in the selector~~ | -- | **Decided 2026-10-24.** Offered. See Decisions log. **Spec amendment still pending** -- see B-10 |
| ~~B-8~~ | ~~Where the current store selection lives~~ | -- | **Decided 2026-10-24.** Server-side session row. See Decisions log |
| B-9 | **Does an inactive store accept writes?** Currently **yes, by default** -- `get_store` has no `is_active` filter, so `POST /stores/{id}/notes` succeeds against a deactivated store and writes an audit event. Pre-existing, not introduced by `10b6fda`, but B-7 now *puts the store in the selector*, so users will find the door. The Owner settled visibility only and explicitly did not settle this | First store-scoped write story in slice 2 | **You** |
| B-10 | **B-7 amendment not yet in `docs/`.** The agent is deny-listed from editing `docs/**`. Until the sentence is pasted after `docs/walmart-user-stories.md:109`, the rule exists only in a `repository.py` docstring citing a blocker id, which is not a specification | **Declaring US-002a done** | **You** -- one paste |
| B-11 | **AC-02's "required integration configuration" is undefined.** No document says what it consists of. The only adjacent text is US-030 AC-04's *integration secrets*, a different story in slice 3; the only plausible column is `walmart_store_key`, but nothing says so -- and it is `NOT NULL` at creation, so it is a validation error, not an activation blocker. The clause has a **skipped test** citing B-11 rather than an invented third blocker | **Declaring US-003 done** | **You** -- define it or rule it out of US-003 |
| B-12 | **No audit event type for store creation.** `api-service-design.md:1186-1209` names `store_settings_updated`, `store_timezone_changed`, `store_assignment_changed` -- but nothing for creation. Slice 0 makes an audit event mandatory on every write, so one *must* be emitted; only the type string is unspecified. `store_created` was chosen by convention, disclosed at the emit site. It is now **load-bearing**: it is the token keeping `POST /stores` and `PATCH` idempotency keys apart (F-31, F-62) | Nothing today; confirm before slice 2 copies the convention | **You** -- confirm the string |
| B-13 | **Who may move `managed_from`?** The capability matrix (`walmart-user-stories.md:671`) grants Admin "change store settings". But `:171` says `managed_from` is set by **the Owner**, `database-schema.md:193` says "**Owner** may change it, audited", and `:693` denies Admin "re-post an order into or out of a protected period" -- which is the effect of moving `managed_from` across the `pre_onboarding` boundary. **Interim: Owner-only**, the intersection of all readings, since the narrower one cannot grant a power no document grants. Enforced in code and labelled interim | A rule is being applied that no document states | **You** -- rule on it |
| B-14 | **`managed_from` auto-default is not implemented.** `walmart-user-stories.md:171` and `database-schema.md:193` both say it defaults to the month the store is added; the code stores `NULL` and reports it as an activation blocker. The spec is **circular here**: "the month the store is added" is a calendar month in the store's timezone, which AC-02 forbids guessing and which may itself be `NULL` at creation. The safe direction was taken | Slice 2, when `scope_status_code` is computed against a NULL boundary | **You** -- resolve the circularity |
| B-15 | **Who may read a store's team list?** No capability-matrix row or AC names who may call `GET /stores/{store_id}/managers`. The two nearest rows answer different questions ("View stores in scope" is *which stores*, not *who else is on this one*; "Administer users" is user administration generally). **Interim: Owner-only** -- the same narrowest-available-reading precedent as B-13 -- enforced in code and labelled interim in both `src/main.py:569-588` and `src/team/service.py:148-160` | Nothing today; confirm before any other story reads this list | **You** -- rule on it |
| ~~B-16~~ | ~~Is `theme.txt` this project's design-system contract?~~ | -- | **Decided 2026-10-27.** Inspiration only, not literal. See Decisions log. UI-01 built on the strength of this ruling |

B-5 is the remaining critical-path item, and only when you first deploy. Local development is unblocked. B-2 and B-3 run in parallel and should start now.

B-10 is one paste and closes US-002a.

## What the golden orders proved

Twenty real orders were loaded on 2026-10-23 and `profit-v1` was checked against them:

| Rule | Result |
|---|---|
| Cost formula and profit | Reproduces 19 of 20 **exactly** |
| ROI = profit ÃƒÆ’Ã‚Â· total cost | Matches the workbook's "Margin" column |
| Warehouse charged once per order | Confirmed at quantity 1, 2, 3 and 5 ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â flat 1.50 throughout |
| Unit price ÃƒÆ’Ã¢â‚¬â€ quantity = total | All 20 |

Two things came out of it that changed the specification:

- **GR-06 found a real error in the business workbook.** A cancelled order omits the 1.50 warehouse charge it actually incurred, so that loss is understated. The golden case records the corrected ÃƒÂ¢Ã‹â€ Ã¢â‚¬â„¢9.20 and says so, to stop anyone "fixing" the test back.
- **Estimate precision was wrong in `profit-v1`.** Estimated fees carry more than 2 decimals (4.095, 5.1152); rounding them to 2 dp before summing shifted two of twenty orders by a cent. `profit-v1` now stores estimates at 4 dp and settled amounts at 2 dp.

## Decisions log

Append here when something is settled that is not a business rule. Business rules go in `docs/walmart-user-stories.md` instead.

| Date | Decision |
|---|---|
| 2026-10-23 | Stack: Python 3.12, FastAPI, Jinja + htmx, plain managed PostgreSQL, raw SQL migrations, pytest |
| 2026-10-23 | Layout: app in `src/`, spec in `docs/`, previous build in untracked `old_pro/` |
| 2026-10-23 | profit-v1 amended: estimates stored at 4dp, settled amounts at 2dp. Verified against 20 real orders. |
| 2026-10-23 | All decision gates D-01…D-10 closed; recorded as seven versioned policies |
| 2026-10-24 | US-001 lockout values chosen (not specified by access-v1): threshold 5 failed attempts, lockout duration 15 minutes. Both are `access_policy` rows, reviewable by the Owner without a deploy. |
| 2026-10-24 | **B-7 closed by the Owner.** `is_active` is not a scope or visibility filter. Owner sees all stores; Admin and Manager see exactly their assignments, inactive ones included. Visibility only -- the write question is explicitly *not* settled (now B-9). This is a **business rule**, so it belongs in `docs/walmart-user-stories.md` after line 109, not here; it is recorded here only until the Owner pastes it (B-10). |
| 2026-10-24 | **B-8 closed by the Owner.** The current store selection lives in a **server-side session row**, chosen as the option the client cannot tamper with. **It is a pointer, never a grant:** scope must still be resolved per request and the stored selection re-checked against it, or revoking an assignment stops taking effect on the next request as `access-v1` requires. Not yet implemented -- lands with US-002b. |
| 2026-10-27 | **US-005 split three ways by the Owner.** US-005a = AC-01, AC-02 (order identity and idempotent persistence). US-005b = AC-03 (import run visibility and freshness). US-005c = AC-04…AC-08 (posting-period assignment under `period-v1`). Eight ACs across eight tables and three distinct concerns is more than one module; the split is per the standing rule to narrow rather than widen. One consequence, from `database-schema.md:1102-1103`: `orders` must carry its **full** documented column shape in its first migration — including `accounting_month_id` (nullable, no derivation), `scope_status_code`, `posting_status_code` — even though US-005a never populates them. So US-005a carries the schema cost and 005b/005c are logic only. `accounting_months` must also be created at full shape to satisfy the FK, empty and unpopulated. |
| 2026-10-27 | **B-9 given an interim ruling, not an Owner decision.** The Owner declined to decide ("take the most logical one, I can patch later"). Applying the **B-13/B-15 precedent** — narrowest reading that no document contradicts, enforced, labelled interim in code, reversible in one line: **an inactive store refuses writes, visibly.** The argument is asymmetry of reversal, not preference: refuse-then-relax is a boolean flip; allow-then-restrict is data cleanup. It must fail *visibly*, never silently skip. `spec-oracle` confirmed the gap is real — `is_active` has **zero matches** in `walmart-user-stories.md`, and no capability-matrix row is conditioned on store activity. **Still an Owner item.** |
| 2026-10-27 | **"A supported order payload" (US-005 AC-01) is an undefined term — gap, logged here rather than guessed.** C-02 as actually written (`walmart-user-stories.md:1175`) governs **settlement reports**, not orders; the matching table is `settlement_report_definitions`. There is no order-payload catalogue, no version set, and no AC saying what an *unsupported* order payload does. Decision: **build no unsupported-payload rejection branch.** A payload missing a required field fails as a missing field. Borrowing US-015 AC-04's "fails visibly" for orders would invent behaviour with no AC behind it. Reversible once the Owner rules. |
| 2026-10-27 | **B-16 closed by the Owner.** `theme.txt` is intentional for this project -- it is not to be followed literally (exact hex values, exact fonts, the mandatory SVG grain technique are not requirements), only as a loose aesthetic direction ("the idea and inspiration"). This does not override `docs/walmart-store-manager-design.md`'s UX baseline (data-dense, compact typography, sober operations tool) -- it is a palette/mood steer layered on top of that baseline, not a replacement for it. Non-normative UX/design guidance, not a business rule, so recorded here rather than requiring a `docs/` amendment (same tier as B-8). The orphaned test file (`tests/test_ui001_sign_in_screen.py`) pinned literal theme.txt compliance throughout its Group 5 and one Group 1 test -- being revised by test-author to test the actual, theme-agnostic engineering/accessibility requirements instead of exact token matches, before any screen is built against it. |
| 2026-10-27 | `theme.txt` added to `.gitignore` at the Owner's instruction -- "not wanted tracked." No further action; the file stays untracked and advisory, per B-16. |
| 2026-10-28 | **B-18 closed by the Owner.** A real order with two SKUs under one `purchaseOrderId` (contradicting BR-02) is rejected **whole** at import time, never split, never partially imported -- and the rejection is **visible**, not a silent skip. This is a **business rule about order-import behaviour**, so it belongs in `docs/walmart-user-stories.md` (BR-02 itself, or a new AC), not only here -- same relationship as B-7/B-10: recorded here now, Owner pastes the exact wording once US-005b works out the precise mechanics (what "visible" resolves to -- an `import_errors` row, surfaced on the orders list). |
| 2026-10-28 | **G-6 closed by the Owner.** US-005b's AC-03 is built with a real rendered screen, not API-only -- "I wanna see things and interact." This is the first slice-2 story to build a screen as part of its own scope rather than deferring rendering to a dedicated UI story the way US-002a/US-003/US-004/US-005a all did; justified here because AC-03's own text ("when viewing the list") names the screen directly, unlike any earlier slice-1/2 AC. |

## Session log

Newest first. One line each: what was completed, what is now true that was not before.

| Date | Session outcome |
|---|---|
| 2026-10-27 | **US-005a implemented and audited to a pass (`663eaae`), continuing from the prior session's step 2.** `story-implementer` built one migration (`003_orders.sql`, `orders` and `accounting_months` both at full documented shape) and one module (`src/orders/`), confirming the module-boundary scope before editing. Audit pass 1 failed on two blocking findings: **F-88**, the entire idempotent-replay path (pre-check, `store_id` guard, the audit write itself, the concurrency backstop) was provably deletable with the suite staying green -- the F-72 shape from US-004, one level worse; **F-89**, the suite was red on a `.fetchone()[0]`-vs-`dict_row` bug in the test-author's own committed file, unrelated to the implementation. Fix round touched only the test file (three new replay tests, one-line row-access fix) -- verified independently before requesting pass 2 (`git diff --stat`, full suite, scanner), which passed, with a correction worth keeping: the fix round's own new test docstring misstated which guard branch a deletion would be caught by, and the auditor traced the actual mechanism rather than trusting the test's name. Five non-blocking findings carried forward (F-90…F-94). **Separately, verified a user claim before acting on it rather than after:** asked to "run it" for `walmart-probe` on the strength of "the client ID is in the env," checked `.env` directly first and found two problems the user hadn't -- `WALMART_CLIENT_ID` was never added (the new lines are `clientID`/`clientSecret`, wrong names, read by nothing) and the new `clientSecret` value contradicts the pre-existing `WALMART_SECRET_KEY` line. Reported precisely rather than proceeding or guessing which secret is current. B-3 updated accordingly; probe not re-run. Committed implementation before requesting the second audit pass this time, closing the F-94/F-18 process gap the previous two stories both hit. |
| 2026-10-27 | **Slice 2 opened. US-005 split three ways; US-005a is at pipeline step 2 of 4 — failing tests written and committed, no implementation yet.** `spec-oracle` produced a cited brief (reused verbatim downstream instead of being re-derived per agent, which was the main source of wasted time in earlier stories). It returned **three gaps**, all ruled on under the B-13/B-15 narrowest-reading precedent rather than escalated for another round-trip: "supported order payload" is undefined (C-02 governs settlement reports, not orders) so **no** unsupported-payload branch is built; month provisioning is unowned (**B-17**); inactive-store writes refuse visibly (**B-9** interim). It also settled the retrofit question: `orders` carries its full column shape — `accounting_month_id` nullable and underived (DB-06), `scope_status_code`, `posting_status_code` — from the **first** migration, and `accounting_months` is created at full shape purely to satisfy the FK. `test-author` wrote **30 failing tests** (`tests/test_us005a_import_orders.py`), each annotated with the mutation it would catch, and **self-caught a defect in its own first draft**: the out-of-scope-store test passed with zero production code because both calls hit FastAPI's generic routing 404 — the exact US-004 trap — fixed with a control import that must return 201. **`walmart-probe` ran in parallel and correctly refused to proceed:** I passed it the Owner's claim that `WALMART_CLIENT_ID` was in `.env` as established fact; it verified, found only `WALMART_SECRET_KEY`, and confirmed empirically with a `400` from Walmart's token endpoint. It wrote no fixtures rather than synthesising plausible ones. B-3 reframed accordingly. **My error, not the probe's** — I restated an unverified claim as fact in a task brief. |
| 2026-10-27 | **UI-01 (sign-in screen and authenticated shell) implemented and made green against `tests/test_ui001_sign_in_screen.py` (`a1b61c7`).** No new business rule -- every route calls `access_service.sign_in`/`authenticate`/`resolve_scope`/`sign_out` and `stores_repository.list_stores` exactly as the JSON API already does, through an `HttpOnly`/`SameSite=Lax` session cookie instead of a bearer header. Added `src/templates/` (`index.html`, `sign_in.html`, `shell.html`), `src/static/css/app.css` (design tokens: an 18-property `:root` palette each declared once, independent `--font-heading`/`--font-body` tokens, a real `:focus-visible` indicator, a `prefers-reduced-motion` block, body-text-on-background contrast verified at ~13.8:1 against a 4.5:1 floor), and `src/web/routes.py` (`GET /`, `GET`/`POST /sign-in`, `GET /app`, `POST /sign-out`), wired into `src/main.py` via `app.include_router` plus one `app.mount("/static", ...)`. Added `python-multipart` to `requirements.txt` -- confirmed necessary, not assumed, by reading Starlette's own `Request.form()`, which asserts `parse_options_header is not None` before even checking content type. **34 passed, 1 skipped** (pre-existing F-13 skip, untouched) for this file alone; **262 passed, 3 skipped** full suite, zero regressions; guardrail scan clean, 24 files, 0 suppressions. Self-reviewed for the shape of gap US-003/US-004's audits kept finding (a branch correct but unproven) and found one: `GET /app`'s and `_optional_session`'s `except SessionInvalid` branches are both present but untested by this file, because every "after sign-out" scenario the test file builds drops the cookie entirely (confirmed empirically with a throwaway probe script) rather than leaving a merely stale one behind -- disclosed in the "UI-01" section above rather than silently left for an auditor. MFA entry for the HTML form was deliberately not built (no test signs in as Owner/Admin through it) -- disclosed in the route module's own docstring. |
| 2026-10-27 | **US-004 implemented and audited to a clean pass (`a9ea6aa`).** Full pipeline, spec-oracle first (clean scoping: Manager-only, no migration needed, API-only, no AC requires a screen). Test-author wrote 26 tests against all four ACs plus role-ordering and the F-55 idempotency discipline, one disclosed skip (who may assign an Admin -- unnamed by any document). Pass 1 failed on three blocking findings, independently verified by me rather than taken from either agent's report: `GET /stores/{store_id}/managers` shipped with zero test coverage (F-70); its read authority had been silently set to "any caller with scope" with no capability-matrix row answering the question and no blocker raised (F-71); and the sharpest one, F-72 -- the replay-target guard's extra `store_id`/`action` checks were correct but entirely unproven, and deleting either would silently let a revoke replay as a no-op grant, breaking AC-03 in two HTTP calls. Fix round closed all three: F-70/72 with seven new tests (the guard code itself untouched -- it was already correct, only unproven); F-71 resolved via the B-13 precedent (narrowest reading, Owner-only, labelled interim, raised as **B-15**). Pass 2: clean, one new non-blocking finding (F-81, the F-71 fix's docstring citing a blocker that didn't yet exist in this file -- closed by this entry). **Also this session:** found an uncommitted, orphaned test-author artifact (`tests/test_ui001_sign_in_screen.py`) built against an untracked `theme.txt` that contradicts this project's own documented UX baseline and reads as foreign content (wrong stack referenced throughout). Escalated as **B-16** rather than building on it, deleting it, or silently rewriting it -- neither file touched. |
| 2026-10-27 | **US-003 implemented and audited to a pass (`cda7b0e`). Not done -- B-11 blocks AC-02's third clause.** Full pipeline, five audit passes, **four failures**: no auth gate on either new write endpoint (F-28, F-01 recurring); AC-02 proven only at the database (F-29); the identity UPDATE with no scope predicate (F-30); replay bound to neither entity nor event type (F-31); `extra='ignore'` returning 200 with the old value for the *real* column name (F-44); `add_store_note` left on the replay pattern its own commit declared broken (F-46); **F-55**, where the duplicate-key fix made the honest retry a 409 while the sloppy one replayed; `timezone` unvalidated so nonsense reported as "fully configured" (F-56); and a final family of creation-path behaviour no test constrained (F-62…F-65). **195 passed, 1 skipped**, scan clean, 0 suppressions, hermetic alone. The lesson: **every fix round created a new unaudited path** -- F-63 is the sharpest, a fix added *ahead* of an existing branch silently orphaned it. Also: a test comment explaining why an input is avoided is a defect report, not a justification. Three new blockers raised rather than guessed (B-11, B-12, B-13) plus B-14. Built API-only again -- slice 1 still has no screens. |
| 2026-10-24 | **Slice 0 step 5 demonstrated -- slice 0 is complete.** Pushed `guardrail-proof` (`9c98bf6`) carrying `order_status` in `src/`. The **Static invariant scan** job failed at the scan step while **Tests** and **Schema invariants** both stayed green, and clean `main` (`6766b06`) passed the same workflow. The guardrail provably blocks. Two earlier branches at `3b27e44` had also gone red but proved nothing -- they predated the `APP_ENV` fix and were failing for an unrelated reason, which is why step 5 had stayed open. Branch deleted both sides. Every "scanner clean" before today was an unvalidated signal; from now on it is real. |
| 2026-10-24 | **US-002 split; US-002a (AC-01, AC-02) implemented, audited and committed (`10b6fda`).** AC-03/AC-04 are UI-shell criteria with no shell, no form and no filtered list to act on -- split out as US-002b rather than inventing a shell plus stub screens. AC-02 needed no production code; **AC-01 did**: `is_active` was acting as an unagreed visibility filter, and the two read paths disagreed about it (F-21). Escalated rather than defaulted; the Owner closed it as B-7, and `include_inactive` was then **removed** rather than flipped to `True`, so the exclusion cannot be reintroduced by a keyword argument. **21 lines of production change, 14 tests, 110 passing**, scan clean, 0 suppressions. Three audit passes: pass 1 failed on F-21; **pass 2 failed because mutation testing had reverted the uncommitted fix with `git checkout`** and the tree genuinely had no fix in it -- the auditor was right and the status report was wrong; pass 3 failed on F-25, this file contradicting its own commit. F-22/F-23/F-24 closed (SQL-spy pin, decoy users for hermeticity, header comparison). Still open: B-9 writes-to-inactive, B-10 spec amendment, and B-6 narrowed-not-closed (two spy mutations survive -- cursor bypass, textual-not-semantic scope match). |
| 2026-10-24 | **US-001 delivered and audited.** `src/access/` (passwords, repository, service), migration `002_access.sql`, session-backed `request_scope`, `POST`/`DELETE /auth/session`, `GET /stores/{store_id}`. **96 tests pass**, scan clean, 0 suppressions. Auditor failed it twice (6 blocking, then 1); F-01...F-12 all closed. Two pre-existing `test_api.py` tests were corrected -- they pinned the slice-0 placeholder and realised BT-08 with an *unauthenticated* caller, which the register defines as "request both as a scoped user". Both came out stricter; BT-08 write-path coverage preserved. Rate limiting still unimplemented (access-v1 gives no window/multiplier/cap) -- escalated, not invented. |
| 2026-10-23 | Golden orders loaded: 20 real cases, profit-v1 verified against them. Found a workbook error (GR-06) and corrected estimate precision to 4dp. |
| 2026-10-23 | Specification completed and all gates closed. Guardrail layer built and verified. Agent roster created. Slice plan written. Previous build moved to `old_pro/` and untracked. Stack decided. |
