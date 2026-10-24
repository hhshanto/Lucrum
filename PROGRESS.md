# Progress

Volatile session state. **Update this at the end of every session.**

Stable rules live in `AGENTS.md` and `docs/`. Nothing in this file is a business rule ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â if you find one here, it is in the wrong place.

---

## Current position

**Slice 0: COMPLETE** -- the step 5 guardrail proof was demonstrated 2026-10-24 (see below). **Slice 1:** US-001 done and audited. US-002 split; US-002a implemented, audited and committed (`10b6fda`) -- closable once the Owner pastes the B-7 amendment into `docs/` (B-10).

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

Slice 0 is complete. Slice 1 continues.

**US-003 � Create and configure a store** is next in the delivery order
(`docs/walmart-slice-plan.md:73-82`), then US-004.

Two things to settle alongside it, neither blocking the start:

- **B-10** is one paste and closes US-002a.
- **Screens.** The slice plan gives slice 1 `UI-01 sign in` and `UI-02 store
  select and create` (`walmart-slice-plan.md:80`), and none exist -- slice 1
  has been built API-only. US-003 should carry its screen rather than
  deferring it again, or slice 1 closes with no way for the Owner to check
  any of it without reading tests.

Carried from slice 0, now discharged: step 5 was demonstrated 2026-10-24.

## Slice status

| Slice | Stories | Status |
|---|---|---|
| 0 Skeleton | -- | **done.** Step 5 guardrail proof demonstrated 2026-10-24 |
| 1 Access and stores | US-001-004 | US-001 done. US-002a (AC-01, AC-02) audited, blocked on B-7. US-002b (AC-03, AC-04) blocked on B-8, deferred to slice 2. US-003, US-004 not started |
| 2 Orders | US-005ÃƒÂ¢Ã¢â€šÂ¬Ã‚Â¦007 | not started |
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
| B-2 | C-01 settlement category mappings empty | Slice 4 commit | `walmart-probe` against the live store |
| B-3 | C-02 supported report catalogue empty | Slice 2 import, slice 4 | `walmart-probe` |
| B-4 | No Postgres instance provisioned | Slice 0 | **You** ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â pick Neon, Railway or RDS |
| B-5 | No deployment target chosen | Slice 0 "deployed" criterion | **You** |
| B-6 | No refunded order in the golden set. The refund and cost-recovery path is covered only by synthetic fixtures | Slice 5 real-data confidence | **You** ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â add 1ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Å“2 refunded orders to the workbook |
| ~~B-7~~ | ~~Inactive but assigned store in the selector~~ | -- | **Decided 2026-10-24.** Offered. See Decisions log. **Spec amendment still pending** -- see B-10 |
| ~~B-8~~ | ~~Where the current store selection lives~~ | -- | **Decided 2026-10-24.** Server-side session row. See Decisions log |
| B-9 | **Does an inactive store accept writes?** Currently **yes, by default** -- `get_store` has no `is_active` filter, so `POST /stores/{id}/notes` succeeds against a deactivated store and writes an audit event. Pre-existing, not introduced by `10b6fda`, but B-7 now *puts the store in the selector*, so users will find the door. The Owner settled visibility only and explicitly did not settle this | First store-scoped write story in slice 2 | **You** |
| B-10 | **B-7 amendment not yet in `docs/`.** The agent is deny-listed from editing `docs/**`. Until the sentence is pasted after `docs/walmart-user-stories.md:109`, the rule exists only in a `repository.py` docstring citing a blocker id, which is not a specification | **Declaring US-002a done** | **You** -- one paste |

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

## Session log

Newest first. One line each: what was completed, what is now true that was not before.

| Date | Session outcome |
|---|---|
| 2026-10-24 | **Slice 0 step 5 demonstrated -- slice 0 is complete.** Pushed `guardrail-proof` (`9c98bf6`) carrying `order_status` in `src/`. The **Static invariant scan** job failed at the scan step while **Tests** and **Schema invariants** both stayed green, and clean `main` (`6766b06`) passed the same workflow. The guardrail provably blocks. Two earlier branches at `3b27e44` had also gone red but proved nothing -- they predated the `APP_ENV` fix and were failing for an unrelated reason, which is why step 5 had stayed open. Branch deleted both sides. Every "scanner clean" before today was an unvalidated signal; from now on it is real. |
| 2026-10-24 | **US-002 split; US-002a (AC-01, AC-02) implemented, audited and committed (`10b6fda`).** AC-03/AC-04 are UI-shell criteria with no shell, no form and no filtered list to act on -- split out as US-002b rather than inventing a shell plus stub screens. AC-02 needed no production code; **AC-01 did**: `is_active` was acting as an unagreed visibility filter, and the two read paths disagreed about it (F-21). Escalated rather than defaulted; the Owner closed it as B-7, and `include_inactive` was then **removed** rather than flipped to `True`, so the exclusion cannot be reintroduced by a keyword argument. **21 lines of production change, 14 tests, 110 passing**, scan clean, 0 suppressions. Three audit passes: pass 1 failed on F-21; **pass 2 failed because mutation testing had reverted the uncommitted fix with `git checkout`** and the tree genuinely had no fix in it -- the auditor was right and the status report was wrong; pass 3 failed on F-25, this file contradicting its own commit. F-22/F-23/F-24 closed (SQL-spy pin, decoy users for hermeticity, header comparison). Still open: B-9 writes-to-inactive, B-10 spec amendment, and B-6 narrowed-not-closed (two spy mutations survive -- cursor bypass, textual-not-semantic scope match). |
| 2026-10-24 | **US-001 delivered and audited.** `src/access/` (passwords, repository, service), migration `002_access.sql`, session-backed `request_scope`, `POST`/`DELETE /auth/session`, `GET /stores/{store_id}`. **96 tests pass**, scan clean, 0 suppressions. Auditor failed it twice (6 blocking, then 1); F-01...F-12 all closed. Two pre-existing `test_api.py` tests were corrected -- they pinned the slice-0 placeholder and realised BT-08 with an *unauthenticated* caller, which the register defines as "request both as a scoped user". Both came out stricter; BT-08 write-path coverage preserved. Rate limiting still unimplemented (access-v1 gives no window/multiplier/cap) -- escalated, not invented. |
| 2026-10-23 | Golden orders loaded: 20 real cases, profit-v1 verified against them. Found a workbook error (GR-06) and corrected estimate precision to 4dp. |
| 2026-10-23 | Specification completed and all gates closed. Guardrail layer built and verified. Agent roster created. Slice plan written. Previous build moved to `old_pro/` and untracked. Stack decided. |
