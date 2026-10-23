# Invariant Register

**Version:** 1.0  
**Status:** Enforcement layer for the Walmart Store Manager rebuild.

## Why this exists

`walmart-user-stories.md` and its sibling documents state roughly thirty rules, and most of them are **prohibitions**: do not collapse the state dimensions, do not derive the posting period, do not hardcode the edit limit, do not sum across currencies.

Prose does not enforce anything. Worse, every one of these prohibitions makes the code *more awkward in the moment*, so there is constant local pressure to violate them — and an AI agent starting a cold session six weeks from now has no memory of why they exist.

This register converts each prohibition into something that **physically fails**: a database constraint, a catalog assertion, a static scan, or a behavioural test.

A rule that is only written down is a wish. A rule that breaks the build is a rule.

## How to read this

| Column | Meaning |
|---|---|
| Id | Stable identifier. Referenced by error messages and suppressions |
| Invariant | What must always be true |
| Source | The policy section it comes from |
| Enforced by | The mechanism that fails when it is violated |

Enforcement mechanisms:

- **`DDL`** — a constraint or trigger in `sql/001_invariants.sql`. Violation is impossible; the write fails.
- **`CATALOG`** — an assertion in `sql/002_selftest.sql` that inspects the schema itself. Violation fails CI after migration.
- **`SCAN`** — a pattern in `check_invariants.py`. Violation fails CI before merge.
- **`TEST`** — a behavioural test the team writes. Listed here so it cannot be forgotten.

---

## Database invariants

| Id | Invariant | Source | Enforced by |
|---|---|---|---|
| DB-01 | `orders.fulfillment_status_code` is one of exactly four values. `settlement_pending`, `reconciled`, and `refunded` are rejected | BR-10 interpretation | DDL |
| DB-02 | `accounting_months.has_ever_been_closed` may go false→true only, never back | BR-08, `close-v1` | DDL trigger |
| DB-03 | No monetary column uses a binary floating-point type | `profit-v1` | CATALOG |
| DB-04 | No currency column has a column default | BR-12 | CATALOG |
| DB-05 | `cost_edit_policies.max_edits` has no column default | BR-06, `cost-edit-v1` | CATALOG |
| DB-06 | `orders.accounting_month_id` is not a generated column and has no trigger deriving it from `order_date` | BR-13, `period-v1` | CATALOG |
| DB-07 | Forbidden tables do not exist: order line items, compensation, payroll, payment, FX rates | BR-02, BR-09, BR-12 | CATALOG |
| DB-08 | No column anywhere is named `order_status` or equivalent | BR-10 interpretation | CATALOG |
| DB-09 | Append-only tables reject `UPDATE` and `DELETE` | Multiple | DDL trigger |
| DB-10 | `month_close_snapshots` rows are immutable except for `superseded_by_id` | `close-v1` | DDL trigger |
| DB-11 | `currency_code` is a three-letter uppercase code | BR-12 | DDL |
| DB-12 | External identifiers are unique **per store**, not globally | US-005 AC-02 | CATALOG |
| DB-13 | `accounting_months.month` is between 1 and 12 | `period-v1` | DDL |
| DB-14 | A settlement entry's batch store matches its order's store | `settlement-v1` | DDL trigger |

## Static source invariants

| Id | Invariant | Source | Enforced by |
|---|---|---|---|
| SC-01 | The identifier `order_status` appears nowhere in source | BR-10 interpretation | SCAN |
| SC-02 | No numeric edit limit is written into code | BR-06, `cost-edit-v1` | SCAN |
| SC-03 | Period protection is checked via closure history, never current status | BR-08, `close-v1` | SCAN |
| SC-04 | Money never touches a binary float operation | `profit-v1` | SCAN |
| SC-05 | No currency code is defaulted or fallen back to | BR-12 | SCAN |
| SC-06 | Cost components are never zero-coalesced | `profit-v1` | SCAN |
| SC-07 | Profit is assigned only inside the calculation module | `profit-v1` | SCAN |
| SC-08 | No compensation, payroll, or payment identifier exists | BR-09 | SCAN |
| SC-09 | No order line-item identifier exists | BR-02 | SCAN |
| SC-10 | `date_basis` is never defaulted | `period-v1` | SCAN |
| SC-11 | No currency conversion or FX code exists | BR-12, `period-v1` | SCAN |
| SC-12 | Reconciliation statuses never appear in a fulfillment context | BR-10 interpretation | SCAN |
| SC-13 | Credentials and MFA secrets are never serialised | `access-v1` | SCAN |
| SC-14 | Variance classification never gates an automatic confirmation | `settlement-v1` | SCAN |

## Behavioural invariants

These cannot be caught statically. They are the tests that must exist before the relevant story is accepted.

| Id | Invariant | Story | Test shape |
|---|---|---|---|
| BT-01 | Order detail exposes four independent state dimensions and no combined status | US-007 | Assert the response has no `status` key and that a delivered order with refunds and open reconciliation reports all three truthfully |
| BT-02 | An Admin who reopens a month still cannot edit its financials | US-024 AC-02 | Close, reopen as Admin, attempt a cost edit as Admin, expect `HISTORICAL_PERIOD_PROTECTED` |
| BT-03 | The edit allowance comes from policy, not code | US-011 | Change `max_edits` in the policy table, assert the remaining count changes without a deploy |
| BT-04 | Two concurrent saves cannot both consume the last allowance | US-011 AC-03 | Fire parallel writes at remaining=1, assert exactly one succeeds |
| BT-05 | An unknown cost component yields an incomplete result, never zero | US-008 AC-02 | Omit one component, assert profit is withheld and the missing component is named |
| BT-06 | Cross-currency totals are refused | US-026 AC-04 | Two stores, two currencies, assert grouped output and a refusal to sum |
| BT-07 | A closed snapshot does not change when later edits occur | US-026 AC-03 | Close, correct as Owner, assert snapshot v1 is byte-identical and v2 exists |
| BT-08 | An unassigned store is indistinguishable from a non-existent one | US-002 AC-02 | Request both as a scoped user, assert identical status and body |
| BT-09 | A retried write does not double-count | US-014 AC-03 | Replay the same `operation_id`, assert one effect and one history row |
| BT-10 | Uploading a settlement file commits nothing | US-015 AC-03 | Upload, assert zero financial effect until the commit endpoint is called |
| BT-11 | A late order posts forward without touching the closed month | US-005 AC-06 | Close March, import a March order, assert it posts to the open month and March's snapshot is unchanged |
| BT-12 | An unrecorded refund recovery blocks a settled figure | `refund-v1` | Refund without recovery, assert `incomplete` with `refund_recovery_unknown` |

## Suppressions

Any `SCAN` finding can be suppressed inline when genuinely justified:

```python
legacy_value = row["order_status"]  # guardrail:allow SC-01 reading a third-party payload field
```

Rules:

- A suppression requires a reason. A bare `guardrail:allow SC-01` is rejected.
- Suppressions are greppable. Review them in every PR.
- A rising suppression count means the rule is wrong or the design drifted. Investigate rather than accumulate.
- `DDL` and `CATALOG` invariants cannot be suppressed. That is the point of putting them there.

## Review ritual

For every pull request, ask only these questions. The rest is detail.

1. Did it collapse a state dimension?
2. Did it hardcode a policy value?
3. Did it check current status where closure history was required?
4. Did it coerce an unknown to zero?
5. Did it add a second implementation of a formula that already exists?
6. Did it add a suppression, and is the reason good?
