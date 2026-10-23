# Lucrum — Walmart Store Manager

Internal multi-store Walmart order, cost, reconciliation and month-close app. A rebuild. The previous version is in `old_pro/`, untracked, kept only for reference — **never copy from it**, it contains the design faults this rebuild exists to remove.

## Stack

Python 3.12 · FastAPI · Jinja + htmx · PostgreSQL · raw SQL migrations · pytest  
App in `src/`. Specification in `docs/`. Tests in `tests/`.

## Authority

`docs/walmart-user-stories.md` is authoritative for every business rule. If the schema or API document disagrees with it, **stop and report the conflict** — do not reconcile it yourself.

All business decisions are closed and recorded as versioned policies:

| Policy | Governs |
|---|---|
| `access-v1` | Auth, store scope, capability matrix, exception classes |
| `period-v1` | Timezones, month boundaries, onboarding scope, posting, four reporting bases |
| `profit-v1` | Six cost components, profit, ROI, rounding, unknown-vs-zero |
| `cost-edit-v1` | Per-field edit allowance, overrides |
| `settlement-v1` | Stage/validate/commit, suspense, coverage, fallback |
| `close-v1` | Closure protection, correction, readiness, snapshots |
| `refund-v1` | Refunds, cost recovery, cancellations |

Ask the `spec-oracle` agent rather than reading all of `docs/`. If something is genuinely unanswered, it is a gap, a pending configuration item, or a contradiction — report which. **Never invent a rule.**

## Non-negotiables

Each of these is *more convenient to violate than to follow*. That is why they are listed, and why CI enforces them.

1. **Four independent state dimensions** — fulfillment, refund activity, reconciliation, period protection. Never merged. No component takes a `status` prop.
2. **Protection follows `has_ever_been_closed`**, never current status. `status == 'closed'` passes casual tests and silently lets an Admin who reopened a month edit its financials.
3. **Policy values come from tables** — edit limits, tolerances, timezones, currencies, horizons. Never constants.
4. **Unknown is not zero.** No `COALESCE(cost, 0)`, no `or 0`. Withhold the figure and name what is missing.
5. **Exact decimal money.** `Decimal` and `numeric` only. Never float, `parseFloat`, or `toFixed`. One half-up rounding, at the end.
6. **One implementation of `profit-v1`**, server-side, in the calculation service. UI previews are advisory and labelled.
7. **`order_date` ≠ `accounting_month_id`.** First is when the sale happened and follows the source. Second is which books it lands in and is a decision. Never derive one from the other.
8. **Pre-onboarding orders** are imported, read-only, excluded from everything financial, and never posted forward.
9. **Currencies are never summed.**

## Workflow

One story per session. One story per PR.

```
/story US-006
```

Runs `spec-oracle` → `test-author` → `story-implementer` → `invariant-auditor`. Never reorder. Tests are written **before** implementation, or they describe the code instead of constraining it.

The auditor cannot edit, deliberately. Do not resolve its findings by weakening a test or a guardrail.

## Before finishing any work

```
python docs/guardrails/check_invariants.py src
pytest
```

Both must be green. A finding may be suppressed only with a reason:
`# guardrail:allow SC-01 <why>`

`docs/guardrails/` and `docs/*.md` are **not editable** by implementation agents. If a guardrail or spec is wrong, that is an Owner decision, not a step in your task.

## Current state

See `PROGRESS.md` for the active slice, what is done, and open blockers. Update it at the end of each session.
