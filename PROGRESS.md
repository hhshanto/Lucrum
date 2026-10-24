# Progress

Volatile session state. **Update this at the end of every session.**

Stable rules live in `AGENTS.md` and `docs/`. Nothing in this file is a business rule ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â if you find one here, it is in the wrong place.

---

## Current position

**Slice 0 walking skeleton: done except the step 5 CI proof below. Slice 1: US-001 done and audited.**

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
| 1 Access and stores | US-001…004 | US-001 done; US-002…004 not started |
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

B-5 is the remaining critical-path item, and only when you first deploy. Local development is unblocked. B-2 and B-3 run in parallel and should start now.

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
| 2026-10-24 | **US-001 delivered and audited.** `src/access/` (passwords, repository, service), migration `002_access.sql`, session-backed `request_scope`, `POST`/`DELETE /auth/session`, `GET /stores/{store_id}`. **96 tests pass**, scan clean, 0 suppressions. Auditor failed it twice (6 blocking, then 1); F-01...F-12 all closed. Two pre-existing `test_api.py` tests were corrected -- they pinned the slice-0 placeholder and realised BT-08 with an *unauthenticated* caller, which the register defines as "request both as a scoped user". Both came out stricter; BT-08 write-path coverage preserved. Rate limiting still unimplemented (access-v1 gives no window/multiplier/cap) -- escalated, not invented. |
| 2026-10-23 | Golden orders loaded: 20 real cases, profit-v1 verified against them. Found a workbook error (GR-06) and corrected estimate precision to 4dp. |
| 2026-10-23 | Specification completed and all gates closed. Guardrail layer built and verified. Agent roster created. Slice plan written. Previous build moved to `old_pro/` and untracked. Stack decided. |
