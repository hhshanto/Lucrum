# Progress

Volatile session state. **Update this at the end of every session.**

Stable rules live in `AGENTS.md` and `docs/`. Nothing in this file is a business rule — if you find one here, it is in the wrong place.

---

## Current position

**Slice 0 — walking skeleton. Not started.**

Nothing of the application exists yet. `src/` has not been created.

## Next action

Slice 0, in order:

1. `src/` skeleton — FastAPI app, one route, one table, `psycopg` connection
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
| 0 Skeleton | — | not started |
| 1 Access and stores | US-001…004 | not started |
| 2 Orders | US-005…007 | not started |
| 3 Costing and estimates | US-008…014, US-030 | not started |
| 4 Settlement evidence | US-015…017 | not started |
| 5 Reconciliation | US-018…021, US-031 | not started |
| 6 Month close | US-022…025 | not started |
| 7 Reports | US-026…028 | not started |
| 8 Overview | US-033 | not started |

Full contents and per-slice traps: `docs/walmart-slice-plan.md`.

## Open blockers

| # | Blocker | Blocks | Owner |
|---|---|---|---|
| B-1 | `guardrails/golden/profit_v1_cases.json` has **0 of 20** real orders. Source workbooks are the Swiftzar `.xlsx` at repo root | Slice 3 cannot be verified | **You** — no code needed, do this early |
| B-2 | C-01 settlement category mappings empty | Slice 4 commit | `walmart-probe` against the live store |
| B-3 | C-02 supported report catalogue empty | Slice 2 import, slice 4 | `walmart-probe` |
| B-4 | No Postgres instance provisioned | Slice 0 | **You** — pick Neon, Railway or RDS |
| B-5 | No deployment target chosen | Slice 0 "deployed" criterion | **You** |

B-1 and B-4 are on the critical path. B-2 and B-3 run in parallel and should start now.

## Decisions log

Append here when something is settled that is not a business rule. Business rules go in `docs/walmart-user-stories.md` instead.

| Date | Decision |
|---|---|
| 2026-10-23 | Stack: Python 3.12, FastAPI, Jinja + htmx, plain managed PostgreSQL, raw SQL migrations, pytest |
| 2026-10-23 | Layout: app in `src/`, spec in `docs/`, previous build in untracked `old_pro/` |
| 2026-10-23 | All decision gates D-01…D-10 closed; recorded as seven versioned policies |

## Session log

Newest first. One line each: what was completed, what is now true that was not before.

| Date | Session outcome |
|---|---|
| 2026-10-23 | Specification completed and all gates closed. Guardrail layer built and verified. Agent roster created. Slice plan written. Previous build moved to `old_pro/` and untracked. Stack decided. |
