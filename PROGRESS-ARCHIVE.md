# Progress archive

Full per-story detail: what shipped, what each audit pass caught, carried-
forward findings. Moved out of `PROGRESS.md` so that file stays small enough
to be auto-loaded in full every session without paying for this file's
weight every time. **Not auto-loaded** -- read on demand when resuming a
story that already has a section here, or when a blocker citation points
back to one.

Append a new section here at the end of every story, in the same style as
the ones already below. Newest-first order is not required here (unlike
`SESSION-LOG.md`) -- sections are read by story id, not by recency.

Known issue, not yet cleaned up: there are two separate, non-identical
"## UI-01" sections below (both titled the same). They were both left in
the file by an earlier session rather than merged or one deleted -- each
carries real content not present in the other. Preserved as-is rather than
silently merged; worth a human read-through to decide what, if anything,
should be consolidated.

---
## US-005b-1 -- durable, queryable import-run record (backend half of AC-03)

Full pipeline, `spec-oracle` -> `test-author` -> `story-implementer` ->
`invariant-auditor`, **two audit passes**, both independently verified by me
(git diff/log, full suite re-run, scanner re-run myself, not taken from
either agent's report). Final state `89707a9`.

**Split decision, made before any test was written.** The checkpoint left at
the end of the prior session required a fresh `spec-oracle` pass before
`test-author`, because two Owner decisions (G-6: build a real screen; B-18:
reject a multi-SKU order whole, visibly) postdated the existing seven-item
brief. The fresh brief's own section G laid out the evidence plainly: AC-03
plus the B-18 fix touches `src/orders/` (new tables, a revision to
already-merged US-005a code) **and** `src/web/` + `src/templates/` (UI-04,
which does not exist in any form). Two modules. Split, per the standing
rule -- **US-005b-1** (this story) is the backend half: a durable, queryable
import-run record. **US-005b-2** (not started) is the UI-04 screen that
reads it. **AC-03 is not fully covered until US-005b-2 lands** -- "viewing
the list" literally has nothing to view yet; only the data it would read
exists.

**B-18 was folded into b-1, not deferred.** Same module, same migration,
same `import_errors` mechanism AC-03's "failure visible" half needed anyway
-- and without it, AC-03's failure-visibility tests would have had only a
synthetic failure to test against rather than the Owner's actual ruling.

| AC / rule | Status |
|---|---|
| AC-03.1 successful run durably recorded | **covered** |
| AC-03.2 failed run recorded, last-success marker not advanced | **covered** |
| AC-03.3 stale data not labeled current (total-failure shape) | **covered** |
| AC-03.3 (mixed/partial-failure shape) | **covered** -- closed in the fix round (F-95) |
| AC-03.4 unknown is not zero (never-imported is a distinct state) | **covered** |
| B-18 multi-SKU order rejected whole, visibly (within-batch) | **covered** |
| B-18 same-SKU duplicate lines rejected whole, visibly | **covered** -- closed in the fix round (F-96), raised as interim **B-21** |
| B-18 cross-batch SKU disagreement (a later import contradicting a stored row) | **deliberately not built** -- undecided business question, raised as **B-19**, its test written in full and skipped, citing the blocker |
| AC-03 screen ("viewing the list") | **out of scope** -- US-005b-2 |

**What shipped.** One migration (`004_import_tracking.sql`), `import_batches`
and `import_errors` at full documented shape
(`database-schema.md:965-998`), enum `CHECK`s at the database layer (the
same DB-01 discipline US-005a applied to `fulfillment_status_code`), not
only in Python. `src/orders/repository.py` gained
`create_import_batch`/`finish_import_batch`/`insert_import_error`/
`get_import_run_state` -- the last one answers three distinguishable
states for a store (never imported / last run succeeded / last run failed,
with the earlier success still reported), scope composed into the SQL via
a CTE (the F-22 discipline, re-applied), never fetched then filtered in
Python. `src/orders/service.py`'s `import_orders` now groups submitted
items by `walmart_order_id`; a group spanning more than one SKU, or more
than one line sharing the same SKU, is rejected whole and visibly, while
every other order in the batch still commits; the function opens, tracks
and closes an `import_batches` row (`completed`/`partial`/`failed`).

**No staleness threshold was invented.** `spec-oracle`'s fresh brief
re-verified, independently, that no staleness window exists anywhere in
the specification -- no `period-v1` clause, no policy table, no config
row -- and that AC-03's own text requires only that data is *not labeled
current* after a failure, not a computed stale/fresh boolean. The auditor
confirmed this structurally, not just by reading: `get_import_run_state`
performs no time arithmetic of any kind; the "never labeled current"
guarantee comes from the four-key response shape itself (pinned by set
equality in a test), not a threshold.

**Four engineering inventions, each disclosed at its point of use** (same
posture as B-12's `store_created`): the `import_errors.error_code` value
for a multi-SKU rejection (`MULTIPLE_SKUS_FOR_ORDER`) and, after the fix
round, a second, distinct code for a same-SKU duplicate-line rejection
(`DUPLICATE_ORDER_LINE`) so the two conditions stay distinguishable in
durable data; the 422 status for a wholly-rejected batch; the counting
granularity (per distinct `walmart_order_id`, not per raw submitted
line); and reusing `_EVENT_TYPE = "orders_imported"` across success,
partial and failure outcomes rather than coining three.

**One interim authority ruling, labelled as such, raised as a new
blocker.** Who may read a store's import run state is ungoverned by any
capability-matrix row. Applied **store-scope-only** -- the same gate as
triggering an import -- not the Owner-only B-13/B-15 shape, because those
concerned administrative power over *other users*, and this is operational
data about a store the caller already has scope to; a Manager who may
trigger an import but may not see whether it failed would defeat AC-03's
purpose. Labelled interim in the code, raised as **B-20**.

**`failed` vs `partial` classification for a mixed batch is deliberately
left unpinned (G-7).** No document anywhere defines this transition rule
for order-import batches -- the only transition language in the spec
governs *settlement* batches, a different table, a different policy. The
implementer's choice (`partial`) is disclosed as judgment, not a spec
reading, and the tests assert only the AC-03-mandated negative (`!=
"completed"`), never the literal value, so an Owner ruling on `failed` vs
`partial` can still land without touching a test.

### Pass 1 (2 blocking)

**F-95, the sharp one.** Mutating `outcome = "partial"` -> `"completed"`
survived the entire suite. Under the mutation a refresh that rejected an
order would advance the store's last-success marker and report
`last_attempt_status == "completed"` -- verbatim AC-03's forbidden
outcome, "stale data is labeled current." No test submitted a mixed batch
(one valid order, one rejected) at all.

**F-96.** Two submitted lines sharing one `walmart_order_id` with the
*same* SKU hit an unguarded fallthrough (`items[0]`, `items[1:]`
discarded) -- no error row, no count, no comment -- while the batch
affirmatively reported `success_count=1, error_count=0, completed`. An
undisclosed behaviour change to already-merged, already-audited US-005a
code (which previously upserted every line, last-wins), and precisely the
silent skip the Owner's B-18 ruling forbids.

**The fix round closed both.** F-95 needed **no code change** -- the
classification logic was already correct, only unproven; a new test
(asserting only the AC-03-mandated negative, not the undecided `partial`
literal) was added and validated by mutation (applied, confirmed red,
reverted, confirmed `src/` clean via `git diff`). F-96 got a genuine third
branch: a group with `len(skus) == 1` and `len(items) > 1` is now rejected
whole, under a distinct error code, citing interim blocker **B-21** (is a
same-SKU duplicate a legitimate quantity split, or a BR-02 violation? --
undecided; "there are no order line items" is BR-02's operative clause
and does not turn on whether the SKUs match, so the interim ruling follows
BR-02's literal text and the Owner's explicit "never a silent skip," and
is reversible in one branch). I verified both independently before
requesting pass 2: `tests/` byte-identical to the pre-fix-round test
commit, 318 passed / 4 skipped, scanner clean, zero suppressions, and the
diff confined to `src/orders/service.py` + `src/orders/__init__.py`.

### Pass 2: pass, with one re-opened finding and four closed

The auditor traced every new branch to its test (or to the honest absence
of one) rather than accepting either fix's description. F-95: confirmed
the classification code is byte-identical to the pre-fix commit via hunk
arithmetic on the diff, and traced three independent ways the new test
goes red under the mutation (batch-row assertion, last-success-at
assertion, last-attempt-status assertion). F-96: confirmed the new
branch's ordering cannot starve the multi-SKU check (`skus` is a set,
checked first), confirmed zero `orders` rows survive (the `continue`
precedes the write), confirmed every new line is load-bearing (each one
independently, by deletion), and confirmed B-21's ruling is genuinely
"reject whole" with no stronger/different behaviour (no quantity summing
anywhere).

**One of the four non-blocking items the fix round claimed to close was
not actually closed, and is carried forward rather than marked done:**
**F-99**, a comment misattributing which object's `__exit__` performs the
rollback that makes an explicit `conn.commit()` necessary before a
mid-transaction raise. The first fix round's attempted correction
*replaced one wrong claim with a different wrong one* -- it now asserts
the pool's own context-manager `__exit__` performs the rollback, when the
auditor traced it, against the installed library's actual source
(`psycopg_pool/pool.py`), to `psycopg.Connection.__exit__`, entered via a
plain `with conn:` inside the pool's generator. I read the comment myself
afterward and confirm the auditor's finding; it is still wrong, just
differently. Not blocking -- the `conn.commit()` call itself is correct,
necessary, and proven by two tests; only the prose explaining *why* is
wrong, the exact F-87/F-91 shape (a comment correct about *whether*,
wrong about *why*) now a third time running. **Left open, not closed, in
this record** -- the next person to touch `src/orders/service.py` should
fix the wording, not re-trust it.

Three other non-blocking items were genuinely closed and independently
re-verified: **F-98** (the package docstring no longer claims AC-03 is
unimplemented, and makes no new overclaim either), **F-101**
(`WholeBatchImportRejected.batch_id` is now `uuid.UUID` on both the
original-raise and replay-reconstruction paths -- itself untested, since
nothing over HTTP surfaces it yet, correctly left that way until
US-005b-2), **F-102** (the module docstring's "invented conventions" list
now names all four, including the `_EVENT_TYPE` reuse across outcomes).

### Carried forward from US-005b-1 (non-blocking)

**F-99** (re-opened, see above) the `conn.commit()` comment at
`service.py:600-617` still misattributes the rollback mechanism. Fix the
prose; the commit itself is correct.
**F-103** the `audit.DuplicateOperation` concurrency backstop on the new
write path is structurally present and unexercised by any
single-threaded test -- the same acknowledged class as US-003's,
US-004's, and US-005a's equivalent backstops (F-75, F-92). Needs BT-04
concurrency work, not another audit round.
**F-105** `get_import_run_state` has no HTTP caller anywhere in `src/` --
correct and expected for a backend-half story; it becomes load-bearing
the moment US-005b-2 calls it.
**B-21** (new) is cited in the code (`service.py`) but, until this entry,
did not exist in this file's blockers table -- the auditor caught the
dangling citation (F-97) in pass 1; closed by this record existing.

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

