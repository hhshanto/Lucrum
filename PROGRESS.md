# Progress

Volatile session state. **Update this at the end of every session.**

Stable rules live in `AGENTS.md` and `docs/`. Nothing in this file is a business rule ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â if you find one here, it is in the wrong place.

---

## Current position

**Slice 0 walking skeleton: done except the step 5 CI proof below. Slice 1: US-001 done and audited. US-002 split; US-002a audited and blocked on B-7.**

**US-002 was split, because it spans two modules.** AC-01 and AC-02 are
server-side (query layer + HTTP edge); AC-03 ("explicit discard/cancel before
leaving unsaved input") and AC-04 ("clear selected rows and incompatible
filters on switch") are UI-shell criteria. There is no UI shell, no form, no
filtered list and no selected row anywhere in `src/` yet, so AC-03/AC-04 could
only be built by inventing a shell *and* stub screens to have something to
discard and clear.

- **US-002a (AC-01, AC-02)** -- server half. Implemented, audited, **not
  done**: blocked on B-7.
- **US-002b (AC-03, AC-04)** -- UI shell. **Blocked on an Owner decision**
  (B-8) and should land with slice 2, when the orders list supplies a real
  filtered list and a real unsaved-input form to act on.

US-002a required **no production code**. The scope spine built during US-001
(`StoreScope`, `resolve_scope`, `list_stores`/`get_store`, the `OutOfScope`
-> 404 handler) already satisfies AC-01 and AC-02, so the deliverable is nine
pinning tests in `tests/test_us002_select_store.py`. **105 tests pass**,
scanner clean (18 files, 0 suppressions), `git diff src/` empty.

Because the tests were green on first run, there was no red step, and a test
that has never failed has never been shown to constrain anything. They were
therefore validated by **mutation instead**: nine deliberate breaks, each
caught by the expected test -- admin treated as owner; `sql_filter` returning
`true` for an empty scope; the owner special case removed; 404 changed to 403;
the store id interpolated into the 404 body; the scope filter dropped from
`list_stores`; `resolve_scope` ignoring `user_id`; `get_store` filtering in
Python after the fetch; a header present only when the store exists.

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

**Outstanding:** step 5 of slice 0 — push a commit containing `order_status`
and confirm CI rejects it, then revert. Not yet demonstrated. US-001 shipping
does not discharge this: the clean scan above is only as trustworthy as this
unrun proof.

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

Slice 0, in order:

1. `src/` skeleton ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â FastAPI app, one route, one table, `psycopg` connection
2. Migration runner for plain `.sql` files in `src/db/migrations/`
3. The cross-cutting spine, all of it before any feature:
   - `Decimal` money type convention, floats banned
   - currency column beside every monetary column
   - store scoping in the query layer
   - `operation_id` accepted on every write
   - audit event writer, used by the first endpoint
   - UTC storage, timezone conversion at the edges only
4. CI green: guardrail scan, invariant DDL, schema self-test, pytest
5. **Prove the guardrails block.** Push a commit containing `order_status` and confirm CI rejects it, then revert. An unproven guardrail manufactures false confidence.

Slice 0 is done when a deployed route returns real data from a migrated database, CI is green, and step 5 has been demonstrated.

## Slice status

| Slice | Stories | Status |
|---|---|---|
| 0 Skeleton | ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â | not started |
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
| B-7 | **Is an inactive but assigned store offered in the selector?** Audit F-21: `list_stores` hides it (`include_inactive=False`), `get_store` has no `is_active` filter at all -- so `GET /stores` omits it while `GET /stores/{id}` returns it in full, 200, with name, timezone and currency. The spec is silent. No test pins either answer, and flipping the default passes all 105 | **US-002a done** | **You** |
| B-8 | **Where does the current store selection live?** Session row, cookie, URL, or nowhere (re-derived per request). `user_sessions` has no `current_store_id`; neither document defines a switch endpoint. A shared silence, not a contradiction | US-002b (AC-03, AC-04) | **You** |

B-5 is the remaining critical-path item, and only when you first deploy. Local development is unblocked. B-2 and B-3 run in parallel and should start now.

B-7 is small and worth closing quickly: it is one sentence of decision, it blocks
declaring US-002a done, and the asymmetry between the two read paths is a live
inconsistency regardless of which way it is settled.

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

## Session log

Newest first. One line each: what was completed, what is now true that was not before.

| Date | Session outcome |
|---|---|
| 2026-10-24 | **US-002 split; US-002a (AC-01, AC-02) delivered as tests only, audited, blocked on B-7.** AC-03/AC-04 are UI-shell criteria with no shell, no form and no filtered list to act on, and rest on an undecided question (B-8) -- split out as US-002b rather than inventing a shell plus stub screens. US-002a needed **no production code**: the US-001 scope spine already satisfies both criteria, so the deliverable is nine pinning tests. **105 pass**, scan clean, 0 suppressions, `git diff src/` empty. No red step was available, so the tests were validated by **nine mutations** instead, each caught by the expected test. The auditor failed pass 1 on F-21 (the inactive-but-assigned asymmetry -> B-7) and named three mutation survivors: F-22 scope filter could move from SQL into Python undetected; F-23 the file was not hermetic and only passed because earlier files leave assignments behind; F-24 BT-08 ignored headers. All three now closed and each re-verified by re-running its mutation. F-21 escalated, **not** resolved in code -- choosing a default would have been inventing the rule. |
| 2026-10-24 | **US-001 delivered and audited.** `src/access/` (passwords, repository, service), migration `002_access.sql`, session-backed `request_scope`, `POST`/`DELETE /auth/session`, `GET /stores/{store_id}`. **96 tests pass**, scan clean, 0 suppressions. Auditor failed it twice (6 blocking, then 1); F-01...F-12 all closed. Two pre-existing `test_api.py` tests were corrected -- they pinned the slice-0 placeholder and realised BT-08 with an *unauthenticated* caller, which the register defines as "request both as a scoped user". Both came out stricter; BT-08 write-path coverage preserved. Rate limiting still unimplemented (access-v1 gives no window/multiplier/cap) -- escalated, not invented. |
| 2026-10-23 | Golden orders loaded: 20 real cases, profit-v1 verified against them. Found a workbook error (GR-06) and corrected estimate precision to 4dp. |
| 2026-10-23 | Specification completed and all gates closed. Guardrail layer built and verified. Agent roster created. Slice plan written. Previous build moved to `old_pro/` and untracked. Stack decided. |
