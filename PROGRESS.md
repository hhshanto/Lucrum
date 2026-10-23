# Progress

Volatile session state. **Update this at the end of every session.**

Stable rules live in `AGENTS.md` and `docs/`. Nothing in this file is a business rule Ã¢â‚¬â€ if you find one here, it is in the wrong place.

---

## Current position

**Slice 0 Ã¢â‚¬â€ walking skeleton. Not started.**

Nothing of the application exists yet. `src/` has not been created.

## Next action

Slice 0, in order:

1. `src/` skeleton Ã¢â‚¬â€ FastAPI app, one route, one table, `psycopg` connection
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
| 0 Skeleton | Ã¢â‚¬â€ | not started |
| 1 Access and stores | US-001Ã¢â‚¬Â¦004 | not started |
| 2 Orders | US-005Ã¢â‚¬Â¦007 | not started |
| 3 Costing and estimates | US-008Ã¢â‚¬Â¦014, US-030 | not started |
| 4 Settlement evidence | US-015Ã¢â‚¬Â¦017 | not started |
| 5 Reconciliation | US-018Ã¢â‚¬Â¦021, US-031 | not started |
| 6 Month close | US-022Ã¢â‚¬Â¦025 | not started |
| 7 Reports | US-026Ã¢â‚¬Â¦028 | not started |
| 8 Overview | US-033 | not started |

Full contents and per-slice traps: `docs/walmart-slice-plan.md`.

## Open blockers

| # | Blocker | Blocks | Owner |
|---|---|---|---|
| ~~B-1~~ | ~~Golden orders~~ | Ã¢â‚¬â€ | **Closed 2026-10-23.** 20 real cases loaded and verified |
| B-2 | C-01 settlement category mappings empty | Slice 4 commit | `walmart-probe` against the live store |
| B-3 | C-02 supported report catalogue empty | Slice 2 import, slice 4 | `walmart-probe` |
| B-4 | No Postgres instance provisioned | Slice 0 | **You** Ã¢â‚¬â€ pick Neon, Railway or RDS |
| B-5 | No deployment target chosen | Slice 0 "deployed" criterion | **You** |
| B-6 | No refunded order in the golden set. The refund and cost-recovery path is covered only by synthetic fixtures | Slice 5 real-data confidence | **You** Ã¢â‚¬â€ add 1Ã¢â‚¬â€œ2 refunded orders to the workbook |

B-5 is the remaining critical-path item, and only when you first deploy. Local development is unblocked. B-2 and B-3 run in parallel and should start now.

## What the golden orders proved

Twenty real orders were loaded on 2026-10-23 and `profit-v1` was checked against them:

| Rule | Result |
|---|---|
| Cost formula and profit | Reproduces 19 of 20 **exactly** |
| ROI = profit ÃƒÂ· total cost | Matches the workbook's "Margin" column |
| Warehouse charged once per order | Confirmed at quantity 1, 2, 3 and 5 Ã¢â‚¬â€ flat 1.50 throughout |
| Unit price Ãƒâ€” quantity = total | All 20 |

Two things came out of it that changed the specification:

- **GR-06 found a real error in the business workbook.** A cancelled order omits the 1.50 warehouse charge it actually incurred, so that loss is understated. The golden case records the corrected Ã¢Ë†â€™9.20 and says so, to stop anyone "fixing" the test back.
- **Estimate precision was wrong in `profit-v1`.** Estimated fees carry more than 2 decimals (4.095, 5.1152); rounding them to 2 dp before summing shifted two of twenty orders by a cent. `profit-v1` now stores estimates at 4 dp and settled amounts at 2 dp.

## Decisions log

Append here when something is settled that is not a business rule. Business rules go in `docs/walmart-user-stories.md` instead.

| Date | Decision |
|---|---|
| 2026-10-23 | Stack: Python 3.12, FastAPI, Jinja + htmx, plain managed PostgreSQL, raw SQL migrations, pytest |
| 2026-10-23 | Layout: app in `src/`, spec in `docs/`, previous build in untracked `old_pro/` |
| 2026-10-23 | profit-v1 amended: estimates stored at 4dp, settled amounts at 2dp. Verified against 20 real orders. |
| 2026-10-23 | All decision gates D-01Ã¢â‚¬Â¦D-10 closed; recorded as seven versioned policies |

## Session log

Newest first. One line each: what was completed, what is now true that was not before.

| Date | Session outcome |
|---|---|
| 2026-10-23 | Golden orders loaded: 20 real cases, profit-v1 verified against them. Found a workbook error (GR-06) and corrected estimate precision to 4dp. |
| 2026-10-23 | Specification completed and all gates closed. Guardrail layer built and verified. Agent roster created. Slice plan written. Previous build moved to `old_pro/` and untracked. Stack decided. |
