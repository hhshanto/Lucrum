# Walmart Store Manager — Build Plan

**Version:** 1.0  
**Date:** 23 October 2026  
**Status:** Execution plan. **Non-normative** — states no business rules.

## Authority

`walmart-user-stories.md` is authoritative. This document says *when* things get built, never *what the rules are*. If it appears to contradict a policy section, the policy wins and this file is the defect.

## How to use it

Each slice is a **vertical cut**: migration, service, endpoint, UI, tests. Not a layer. You do not build "the database" and then "the UI".

Run every story through the agent pipeline:

```
/story US-001
```

which is `spec-oracle` → `test-author` → `story-implementer` → `invariant-auditor`, in that order. One story per session. One story per PR.

A slice is done when every story in it has every acceptance criterion covered by a passing test, the guardrail scan is green, and it is deployed. Not when it demos.

---

## Parallel track: start now, runs alongside slices 0–2

**Walmart API discovery** — agent `walmart-probe`.

This blocks slice 2 and gates slice 4, so it starts immediately and runs in the background.

Produces:
- recorded fixtures in `fixtures/walmart/`
- a field map per report type
- **C-02** the supported report catalogue
- **C-01** the category mappings

One finding can stop everything: if the Walmart selling amount turns out to **include** buyer sales tax, `profit-v1` is wrong and every profit figure in the system changes. Confirm that early and report it rather than working around it.

**Golden numbers** — you, not an agent. Fill `guardrails/golden/profit_v1_cases.json` with twenty real orders from the spreadsheet. Needs no code and gates slice 3.

---

## Slice 0 — Walking skeleton

**Goal:** one trivial route, served from a migrated database, deployed, with CI that genuinely blocks a bad commit.

No stories. No features. Days, not weeks.

**Establishes the spine** — every item here is catastrophic to retrofit because it touches every file written afterwards:

- exact decimal money type; binary floats banned at the type level
- a currency column beside every monetary column
- store scoping in the query layer, as a pattern
- `operation_id` accepted on every write path
- the audit event writer, used by the first endpoint
- UTC storage with timezone conversion at the edges only
- migration tooling with additive-first discipline

**Guardrails wired into CI:**

```
python guardrails/check_invariants.py src
psql -v ON_ERROR_STOP=1 "$DATABASE_URL" -f guardrails/sql/001_invariants.sql
psql -v ON_ERROR_STOP=1 "$DATABASE_URL" -f guardrails/sql/002_selftest.sql
```

**Done when:** the deployed route returns real data, CI is green, **and a deliberately bad commit fails the build.** Push a commit containing `order_status` and confirm CI rejects it. An unproven guardrail is worse than none, because it produces false confidence.

---

## Slice 1 — Access and stores

**Goal:** an Owner can create a store and assign people to it; everyone else sees only what they should.

| | |
|---|---|
| Stories | US-001, US-002, US-003, US-004 |
| Screens | UI-01 sign in, UI-02 store select and create, UI-14 team |
| Policy | `access-v1` |
| Tables | `users`, `roles`, `user_role_assignments`, `user_sessions`, `password_reset_tokens`, `stores`, `store_settings`, `store_manager_assignments` |
| Behavioural tests | BT-08 |

**This is not a warm-up.** In-app credentials means Argon2id or bcrypt-12, TOTP enforced for Owner and Admin, per-account and per-IP rate limiting, lockout with backoff, server-side session revocation on sign-out and password change. It is the hardest non-domain work in the project.

**Store creation must capture `timezone`, `reporting_currency_code`, and `managed_from`.** Everything downstream depends on all three, and US-003 AC-02 forbids guessing any of them.

**Traps:** Admin is store-scoped, not global. Only the Owner creates stores and administers users. An unassigned store must be byte-identical to a non-existent one in the response. `password_hash` and `mfa_secret` are excluded at the repository layer, not stripped per response.

---

## Slice 2 — Orders

**Goal:** real Walmart orders appear, correctly scoped and correctly posted.

| | |
|---|---|
| Stories | US-005, US-006, US-007 |
| Screens | UI-04 orders list, UI-05 order detail |
| Policy | `period-v1` |
| Tables | `accounting_months`, `orders`, `order_fulfillment_history`, `order_posting_history`, `order_assignments`, `import_batches`, `import_errors`, `raw_import_records` |
| Behavioural tests | BT-01, BT-09, BT-11 |
| Depends on | `walmart-probe` output, C-02 |

Also build the **month provisioning job** here — a scheduled task maintaining the configured horizon per store, plus manual provisioning for backfill.

**Traps:**

- The scope check runs **before** posting. A `pre_onboarding` order gets no posting period and is never deferred forward. Without this, a store with three years of history dumps all of it into your current open month.
- `accounting_month_id` is not derived from `order_date`. No trigger, no generated column.
- `walmart_order_id` is unique **per store**.
- Four state dimensions render separately. No component takes a `status` prop.

**Done when** a real store's orders import idempotently, pre-onboarding orders are visibly inert, and a late in-scope order posts forward while leaving its true month alone.

---

## Slice 3 — Costing and estimates

**This is the slice that validates the rebuild.** Everything after it sits on these numbers.

| | |
|---|---|
| Stories | US-008 to US-014, US-030 |
| Screens | UI-15 store settings, UI-06 cost editing, UI-05 estimate panel, UI-10 minimal |
| Policy | `profit-v1`, `cost-edit-v1` |
| Tables | `cost_edit_policies`, `order_cost_profiles`, `order_cost_field_edits`, `edit_override_requests`, `order_estimate_versions` |
| Behavioural tests | BT-03, BT-04, BT-05 |
| Depends on | golden cases populated |

**Build UI-15 before UI-06.** The allowance must come from `cost_edit_policies` and the warehouse default from versioned `store_settings`. Build the cost form first and the limit gets hardcoded "temporarily" — and SC-02 will block the commit anyway.

**Stop here and verify.** Before anything is built on top: `profit-v1` must reproduce at least twenty real orders from your spreadsheet, exactly. If a number disagrees, find out which side is wrong. That disagreement is the most valuable signal in the whole rebuild, and it is cheap now and very expensive once month close is sitting on it.

**Traps:** no numeric limit in code; unknown never coerced to zero; warehouse applied once per order regardless of quantity; one half-up rounding at the very end; `profit-v1` implemented exactly once, server-side, with any UI preview visibly advisory.

---

## Slice 4 — Settlement evidence

**Goal:** payout evidence enters the system without ever doing so by accident.

| | |
|---|---|
| Stories | US-015, US-016, US-017 |
| Screens | UI-08 import wizard, UI-07 settlement batches |
| Policy | `settlement-v1` |
| Tables | `settlement_report_definitions`, `settlement_category_mappings`, `settlement_batches`, `settlement_entries`, `settlement_entry_types` |
| Behavioural tests | BT-10 |
| Depends on | C-01, C-02 populated from real reports |

Three phases: stage, validate, **explicit operator commit**. Staged rows have zero financial effect.

**Traps:** uploading must commit nothing; a partial commit requires deliberate acceptance; unmapped categories go to suspense and are never guessed into a fee bucket; an unrecognised report version fails visibly rather than guessing columns; corrections insert a superseding entry rather than editing one.

---

## Slice 5 — Reconciliation and exceptions

| | |
|---|---|
| Stories | US-018 to US-021, US-031 |
| Screens | UI-09 reconciliation, UI-10 exceptions full, UI-16 audit log |
| Policy | `settlement-v1`, `refund-v1` |
| Tables | `order_reconciliations`, `order_reconciliation_decisions`, `reconciliation_exceptions`, `variance_classification_policies`, `refund_cost_recoveries` |

Settled profit becomes real here, which means `refund-v1` lands here too: recovery is entered manually and an unrecorded recovery **withholds** the settled figure rather than assuming zero.

The audit log viewer arrives now. Events have been written since slice 0; this is the point where someone actually needs to read them.

**Traps:** nothing auto-reconciles at any variance size; classification is advisory triage only; manual fallback creates no synthetic settlement entries; hard blockers are unwaivable by every role including Owner.

---

## Slice 6 — Month close

| | |
|---|---|
| Stories | US-022 to US-025 |
| Screens | UI-11 close list, UI-12 close workspace |
| Policy | `close-v1` |
| Tables | `month_checklist_definitions`, `month_checklist_results`, `month_actions`, `month_close_snapshots`, `approvals` |
| Behavioural tests | BT-02, BT-07 |

**Traps:**

- Protection reads `has_ever_been_closed`, never current status. BT-02 is the test that matters: close, reopen as Admin, then confirm the Admin is *still* denied a financial edit.
- Historical correction happens **in place** with a new snapshot version. It does not reopen the month.
- Snapshots are insert-only. Reclose adds a version; it never edits one.
- Readiness is invalidated by any financial change to the month.

---

## Slice 7 — Reports and exports

| | |
|---|---|
| Stories | US-026, US-027, US-028 |
| Screens | UI-13 reports |
| Policy | `period-v1`, `profit-v1` |
| Adds | reporting views |
| Behavioural tests | BT-06 |

**Traps:** `date_basis` is required with no default; currencies are grouped and never summed; incomplete orders are excluded from totals and reported as a separate count; views read figures already stamped `profit-v1` rather than reimplementing the formula in SQL.

The basis selector is the best UX opportunity in the app — present the four bases as the questions they answer, not as an enum.

---

## Slice 8 — Overview

| | |
|---|---|
| Stories | US-033 |
| Screens | UI-03 overview |

**Last, deliberately.** It aggregates orders, costs, estimates, reconciliation, months and exceptions. Built early it needs all six stubbed and teaches you nothing; built now it is mostly assembly.

Counts link to filtered views using the same scope and basis. When there is nothing to do, show an explicit all-clear — never a fabricated chart.

---

## Running throughout

**US-032** is not a slice. Every screen ships with loading, empty, partial, stale, conflict and permission-denied states, keyboard reachability, and the uncertainty vocabulary applied consistently. A screen that only renders the happy path is not done.

**The review ritual** — six questions per PR, from `guardrails/INVARIANTS.md`. Did it collapse a dimension, hardcode a policy value, check current status instead of closure history, coerce unknown to zero, duplicate a formula, or add a weak suppression?

## Dependency summary

```
walmart-probe ──────────┐
golden numbers ─────┐   │
                    │   │
0 skeleton          │   │
  └─ 1 access       │   │
       └─ 2 orders ─┼───┘
            └─ 3 costing ◄┘
                 └─ 4 settlement
                      └─ 5 reconciliation
                           └─ 6 close
                                └─ 7 reports
                                     └─ 8 overview
```

The chain is genuinely sequential. The two parallel tracks are the only work that can start before slice 0 finishes, and both are things only you or a live API can supply.
