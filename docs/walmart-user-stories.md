# Walmart Store Manager — User Stories

**Version:** 1.0 — review draft  
**Date:** 23 October 2026  
**Deliverable:** Product backlog for an AI-assisted rebuild  
**Status:** All decision gates closed. Approved as the implementation contract, subject to the pending configuration items listed below.

## Purpose and scope

This document defines user outcomes and testable acceptance criteria for a seller-side Walmart store-management application. It covers store access, order costing, estimates, settlement evidence, reconciliation, monthly close, reporting, and audit.

It does not describe Walmart's internal physical-store management software. Listing management, inventory management, supplier purchasing, initiating marketplace refunds, manager compensation tracking, and payroll automation are outside this proposed release.

Recorded business requirements are distinguished from proposed implementation details. No existing source repository, executable database schema, or API contract was inspected for this backlog. Screen IDs refer to the proposed UI inventory reproduced below; they are not verified existing routes.

## Document set and precedence

Five documents describe this rebuild. They are not peers.

| Order | Document | Role |
|---:|---|---|
| 1 | `walmart-user-stories.md` (this file) | **Authoritative.** Business rules, scope, acceptance criteria, and decision gates. Wins every conflict. |
| 2 | `walmart-store-manager-database-schema.md` | Proposed persistence model. Must conform to this file. |
| 3 | `walmart-store-manager-api-service-design.md` | Proposed service and endpoint contracts. Must conform to this file. |
| 4 | `walmart-store-manager-system-architecture.md` | Layer boundaries and deployment shape. Non-binding on business rules. |
| 5 | `walmart-store-manager-design.md` | Product vision and UX direction only. **Contains no normative data model or permission matrix.** |
| 6 | `walmart-slice-plan.md` | Execution plan: delivery order, slice contents, per-slice traps. **States no business rules.** |

Rules for keeping the set honest:

- A business rule changes **here first**, then propagates to the schema and API documents in the same change.
- If a lower document states a rule this document does not, that rule is a proposal and is not approved.
- Do not restate the permission matrix, the table list, or the status vocabulary in more than one document. Reference them instead.
- `[cite:NNN]` and `[web:NNN]` markers refer to an external requirements-gathering transcript and research set that is **not included in this folder**. They are provenance hints, not verifiable citations. Do not add new ones.

## Established business requirements

| Rule | Requirement |
|---|---|
| BR-01 | Store assignments are many-to-many: several Managers can manage one store, and one Manager can manage several stores. No mandatory order-manager assignment or order-level compensation allocation. [cite:684] |
| BR-02 | An order carries exactly one SKU, with a quantity that may exceed one. There are no order line items. Quantity and selling price are imported. Buying cost is entered manually per unit for each order; no store/SKU buying-cost default. Total buying cost is quantity multiplied by unit buying cost. [cite:88] |
| BR-03 | Estimated Walmart fees and Walmart-label costs remain separate from settled/imported values. Manual third-party label and other costs are supported. [cite:88] |
| BR-04 | Warehouse cost is per order, with a store default and an optional order-specific override retained separately. [cite:88] |
| BR-05 | Estimated and settled values remain permanently distinguishable; an order can have multiple settlement entries over time. [cite:88] |
| BR-06 | Managers can correct saved manual cost fields within per-field edit limits; exceeding a limit requires Admin intervention. Numeric limits are not yet specified and must be stored as configurable policy, never hardcoded in application or schema defaults. [cite:88] |
| BR-07 | A Manager can mark a month ready. Closing requires completed settlement/reconciliation and an explicit Owner/Admin closing action. Owner/Admin can reopen. [cite:685] |
| BR-08 | Only the Owner can change financial records from past closed months; Admin cannot. [cite:88] |
| BR-09 | Orders are grouped by Walmart order month. Refunds also appear in the month they occur. Manager compensation is decided monthly by the Owner **outside this application**; the app performs no compensation calculation, allocation, or record-keeping. [cite:88][cite:684] |
| BR-14 | A store may be added long after it began trading. Each store has an explicit `managed_from` month; orders dated before it are imported read-only, excluded from all financial scope, and never posted to a period. See `period-v1`. |
| BR-13 | An order's true Walmart order month and the accounting period it is booked into are separate concepts. They differ only when an order arrives for a period that has already been closed, in which case it posts forward to the earliest open period and remains labeled with its true month. Refines BR-09; see "Period and timezone policy — `period-v1`". |
| BR-10 | Requested labels are Acknowledged, Shipped/In transit, Delivered, Cancelled, Refunded, Settlement pending, and Reconciled. Owner/Admin confirms reconciliation; manual fallback is allowed. [cite:686] |
| BR-11 | Store settings are Owner/Admin-only. [cite:685] |
| BR-12 | Each store has its own reporting currency. Amounts are stored and reported in the currency they occurred in. Cross-currency totals are not combined without an approved conversion policy. |

**Proposed interpretation of BR-08:** protection follows a month's closure history, not only its current status. Admin reopening must not grant permission to edit protected historical financial records. The Owner correction workflow and reclose behavior need explicit approval under D-06.

### Binding interpretation of BR-10 — four independent state dimensions

The seven BR-10 labels are **not** one status field. They decompose into four dimensions that must be independently readable at all times:

| Dimension | Values | Owner |
|---|---|---|
| Fulfillment | `acknowledged`, `shipped_in_transit`, `delivered`, `cancelled` | Order integration / Orders module |
| Refund activity | derived from settlement evidence; an order may be `delivered` **and** carry refunds | Settlements module |
| Reconciliation | `not_ready`, `pending_settlement`, `needs_review`, `reconciled`, `reconciled_with_exception` | Reconciliation module |
| Accounting protection | derived from the order's accounting month: `open`, `ready_for_review`, `closed`, `reopened`, plus `previously_closed` | Month close module |

Consequences that are binding on the schema and API documents:

- `settlement_pending` and `reconciled` are **reconciliation** values. They must not appear in any fulfillment status column or enum.
- `refunded` is **not** a fulfillment value. A refund is settlement evidence attached to an order that retains its own fulfillment state.
- No endpoint, column, view, or UI control may present a single combined "order status" that collapses these dimensions.
- The UI may render a derived summary badge, but the four underlying dimensions must remain visible on the order detail screen.

## Access and authorization policy — `access-v1`

**Status: approved.** This section closes decision gate D-01. It is normative.

### Authentication

Authentication is **email and password, implemented in-app**. No external identity provider.

This choice moves credential security into our own scope. The following are therefore requirements, not suggestions, and each needs a test:

| Requirement | Rule |
|---|---|
| Password storage | Argon2id, or bcrypt at cost 12 or higher. Never reversible, never logged |
| Password strength | Minimum 12 characters. No composition rules. Reject passwords found in a known-breach list |
| Rate limiting | Per-account and per-IP throttling on sign-in, with exponential backoff |
| Lockout | Temporary lockout after repeated failures, with an audited unlock path |
| MFA | TOTP. **Required for Owner and Admin.** Optional for Manager |
| Password reset | Single-use, time-limited token delivered by email. The response never reveals whether an account exists |
| Session | Server-side sessions, invalidated on sign-out. Idle timeout 30 minutes, absolute timeout 12 hours |
| Session hygiene | Rotate the session identifier on sign-in and on password change. Invalidate all sessions on password change |
| Leakage | No password, token, or session identifier in logs, URLs, exports, audit payloads, or error messages |

Timeout values are configuration, not constants.

### Store scope

| Role | Scope |
|---|---|
| Owner | All stores |
| Admin | **Only stores they are assigned to** |
| Manager | Only stores they are assigned to |

Scope is enforced on every server operation, not by hiding navigation. A direct API call or deep link to an unassigned store returns the same response as a non-existent one — it must not disclose that the store exists (US-002 AC-02).

Revoking an assignment takes effect on the next request, even if a stale screen is still open (US-004 AC-03).

### Exception classification

Managers may resolve data-quality exceptions but not financial ones. The split is by exception code, not by judgment:

| Class | Codes | Who may resolve |
|---|---|---|
| Data quality | `missing_cost_data`, `unmatched_sku`, `stale_order_data`, `order_detail_mismatch` | Owner, Admin, Manager |
| Financial | `settlement_variance`, `unallocated_settlement_entry`, `duplicate_settlement_conflict`, `coverage_incomplete`, `historical_impact`, `late_activity_closed_period`, `posting_period_protected`, `refund_recovery_unknown` | Owner, Admin |

Two overrides:

- Any exception with severity `critical` requires Owner or Admin regardless of class.
- A hard blocker is never waivable by any role. It is resolved by fixing the underlying data (US-021 AC-03).

### Capability resolution

Every protected operation resolves through `UserAccessService` against the capability matrix. No endpoint compares a role string directly. There is no `Viewer` role.

## Period and timezone policy — `period-v1`

**Status: approved.** This section closes decision gate D-02 and narrows D-09. It is normative.

### Timezone

- Each store has its own `timezone`, an IANA identifier such as `America/New_York`.
- It is required before financial reporting is activated for that store, and is never guessed or defaulted (US-003 AC-02).
- Every timestamp is stored in UTC. Conversion to local time happens at month assignment and at display, never in storage.
- DST is handled by the IANA database. Converting a UTC instant to a local date is always unambiguous, so month assignment has no DST edge case.
- Changing a store's timezone applies **prospectively only**. Existing orders keep their assigned dates. The change is audited and warns that future orders may land differently (US-030 AC-03).

### Transaction date and posting period are two different things

This is the core of `period-v1`. An order carries both:

| Concept | Field | Meaning | Can it move? |
|---|---|---|---|
| **Transaction date** | `order_date` | The true date the customer placed the order, in the store's timezone | Only to follow a source correction |
| **Posting period** | `accounting_month_id` | The accounting month the order is booked into | Only between open periods |

They are usually the same month. They diverge only when an order arrives for a period that is already closed.

Conflating them is what makes late orders unsolvable: either you corrupt a closed month or you misstate when the sale happened. Separating them avoids both.

### Order date

- `order_placed_at_utc` is the source instant, stored verbatim.
- `order_date` is that instant converted to the store's timezone and reduced to a calendar date.
- The customer's purchase timestamp defines it. Ship and delivery dates never affect it.
- `order_date` **always follows the source.** A re-import that corrects it updates it, audited as `order_date_changed`.

### Month boundary

A month is the calendar month in the store's timezone: the 1st at 00:00:00.000 local through the last day at 23:59:59.999 local.

Month membership is evaluated by comparing the local calendar `order_date` to the month's year and month. No UTC boundary arithmetic occurs at query time.

### Store onboarding scope

A Walmart store may have years of history before it is added here. Those orders are visible through the API, but the app does not manage them.

Each store has a **`managed_from` month**, set explicitly by the Owner and defaulting to the month the store is added. It may be set earlier or later deliberately, and changing it is an audited decision.

| Order date | `scope_status_code` | Treatment |
|---|---|---|
| Before `managed_from` | `pre_onboarding` | Imported, read-only, **excluded from everything financial** |
| On or after `managed_from` | `managed` | Normal |

A `pre_onboarding` order:

- is imported, so later settlement evidence has something to link to,
- **receives no posting period** and is never deferred forward,
- requires no cost entry and is never counted as missing cost data,
- is excluded from all profit, totals, month readiness, close, and snapshots,
- remains visible and filterable for reference.

**This rule is evaluated before the posting rules below and overrides them.** Without it, a store with three years of history would post every historical order forward into the current open month and corrupt it — the deferral rule is for orders that are late, not for orders that were never in scope.

Settlement evidence arriving for a `pre_onboarding` order is linked to that order, excluded from profit, and raises a `pre_onboarding_settlement` exception so the Owner knows real money moved outside the app's scope. It is not left unallocated, because these would accumulate forever.

### Posting rules at import

These apply only to `managed` orders. A `pre_onboarding` order stops at the scope check above.

1. Find the accounting month containing `order_date` for that store.
2. If it does not exist, retain the order, assign no posting period, and raise `PERIOD_NOT_PROVISIONED` (D-09).
3. If it exists and is open, post there. `posting_reason_code = natural`.
4. If it exists but has **ever been closed**, post to the **earliest currently open month** for that store. `posting_reason_code = deferred_closed_period`.
5. If no month is open at all, retain the order, assign no posting period, and raise `NO_OPEN_PERIOD`.

A deferred order is never hidden. It displays as "posted to May · relates to March" wherever it appears, and appears in a standing deferred-orders report for the Owner.

Posting forward never alters the closed month, never touches its snapshot, and never requires a reopen. This is the standard accounting treatment of a late transaction.

### Posting rules on re-import

When a source correction moves `order_date` into a different month:

- `order_date` updates unconditionally and is audited.
- If the current posting month **and** the new target month are both open, the order re-posts to the target month, audited as `order_reposted`.
- If either month has **ever been closed**, the posting period does not move. An exception is raised for Owner review, and only the Owner may re-post it (BR-08, US-005 AC-04).

So routine corrections self-heal during normal work, and anything touching a protected period stops and waits for a human.

### Four reporting bases

Every financial report declares exactly one basis and never mixes them.

| Basis | Groups by | Answers |
|---|---|---|
| **Order month** | `order_date` | "What did we sell in March?" |
| **Posting month** | `accounting_month_id` | "What is in our March books?" |
| **Settlement event month** | `settlement_entries.event_date` | "What money moved in March?" |
| **Snapshot** | `month_close_snapshots` | "What did we approve for March at close?" |

Order month and posting month agree for every order except deferred ones. The difference between the two is a reportable figure, not an error to hide.

### Cross-currency

No currency conversion exists in this release. There is no FX rate source, no rate table, and no converted total.

- Multi-store results are returned grouped by `currency_code`.
- Any attempt to combine differing currencies returns `CURRENCY_MISMATCH`.
- Introducing conversion later requires a new decision gate covering rate source, rate date, and rounding.

### Effect on D-09

`period-v1` settles what happens when a period is missing: the order is retained and an exception is raised. Import never creates a period.

D-09 is narrowed to one remaining question — whether month provisioning is a manual Owner/Admin action, a scheduled job, or both.

## Financial calculation policy — `profit-v1`

**Status: approved.** This section closes decision gate D-03 except where it names a remaining gate. It is normative. Every stored or displayed financial result records `calculation_policy_version = "profit-v1"`.

### Cost components

Six, and only six. All belong to the order.

| # | Component | Source |
|---:|---|---|
| 1 | `buying_cost_total` | `buying_cost_per_unit × quantity`, entered manually (BR-02) |
| 2 | `warehouse_cost_effective` | Order override if present, otherwise the store default snapshot. Applied **once per order**, never per unit (BR-04) |
| 3 | `walmart_fee` | Estimated or settled |
| 4 | `walmart_label_cost` | Estimated or settled |
| 5 | `third_party_label_cost` | Manual |
| 6 | `other_cost` | Manual |

```
total_cost = buying_cost_total
           + warehouse_cost_effective
           + walmart_fee
           + walmart_label_cost
           + third_party_label_cost
           + other_cost
```

### Estimated profit

```
estimated_profit = selling_amount - total_cost(using estimated walmart values)
```

`selling_amount` is the imported Walmart order amount.

### Settled profit

Same shape, with settled values replacing the estimated Walmart ones. Because an order may carry many settlement entries over time (BR-05), the Walmart-side terms are **sums over committed, allocated entries**:

```
settled_revenue      = SUM(settled_revenue_amount)
settled_walmart_fee  = SUM(settled_walmart_fee)
settled_walmart_label= SUM(settled_walmart_label_cost)
settled_other        = SUM(settled_other_amount)

settled_profit = settled_revenue
               - buying_cost_total
               - warehouse_cost_effective
               - settled_walmart_fee
               - settled_walmart_label
               - settled_other
               - third_party_label_cost
               - other_cost
```

Staged, superseded, and unallocated entries are excluded (US-017 AC-03).

```
variance = settled_profit - estimated_profit
```

Variance uses the order's **current** estimate version, and the version used is recorded with the result (US-013 AC-04).

### Sales tax

The imported `selling_amount` **excludes** buyer sales tax. Walmart collects and remits it as marketplace facilitator. There is no tax term in any formula and no tax column in the schema.

If a settlement report is later found to include tax inside the selling amount, that is a new decision gate, not a silent adjustment.

### Label costs are additive

`walmart_label_cost` and `third_party_label_cost` **both apply** when both are present, and are summed. An order carrying both is normal — for example an outbound label and a return label — and raises no exception.

Each is independently subject to the unknown-versus-zero rule below. An order with no third-party label records an explicit zero, not a blank.

### ROI

```
roi = profit / total_cost
```

Expressed as a percentage, using the same basis as the profit figure it accompanies — estimated ROI from estimated profit and estimated total cost, settled ROI from settled.

`roi` is **Unavailable**, never zero and never infinity, when `total_cost` is zero or any component is unknown (US-013 AC-03).

### Rounding and precision

- Monetary inputs are stored at 2 decimal places.
- All intermediate arithmetic is performed in exact decimal at full precision. **No intermediate rounding.**
- A single half-up rounding to 2 decimal places is applied once, to the final result.
- ROI is computed from unrounded profit and unrounded total cost, then rounded half-up to 2 decimal places of percent.
- Binary floating point is prohibited for monetary arithmetic anywhere in the system.

### Unknown versus zero

- A component is **known** when it holds a value, including an explicitly saved zero.
- A component is **unknown** when it is blank. Unknown is never coerced to zero for calculation or display.
- All six cost components must be known for a profit figure to be produced.
- If any component is unknown, the result is `Incomplete`. The system reports which components are missing and produces no number.
- An `Incomplete` result is never labeled final, never exported as a total, and never contributes to a month close snapshot.

### Currency

All components of a single calculation must share one currency. A mismatch is an error, not a conversion. No formula in this policy converts between currencies (BR-12).

### Coverage

A settled profit figure requires declared settlement coverage.

- Complete coverage produces `settled_profit`.
- Partial coverage produces `settled_to_date`, explicitly labeled, never presented as final.
- No coverage produces `Not settled` — not zero (US-007 AC-04).

### Remaining gates

Two refinements were made after this section was first written:

- **Refund treatment** is resolved in `refund-v1`. Refunds flow into `settled_revenue` as negative entries, and buying cost is taken **net of a manually recorded recovery**. Until a recovery is recorded the settled figure is `incomplete`, never computed as if recovery were zero.
- **Settlement category mapping** is resolved in `settlement-v1` as configuration rather than a formula decision. The mapping table ships empty and is populated from real Walmart reports; unmapped categories go to suspense and are never guessed. Tracked as pending configuration C-01.

## Manual cost edit policy — `cost-edit-v1`

**Status: approved.** This section closes decision gate D-04. It is normative and configurable: the values below are the approved initial configuration, stored in `cost_edit_policies`, not constants in code.

### Scope

The allowance is counted per `(order, field)`. Exhausting one field never blocks another (US-011 AC-02).

Fields under policy: `buying_cost_per_unit`, `warehouse_cost_override`, `third_party_label_cost`, `other_cost`, `estimated_walmart_fee`, `estimated_walmart_label_cost`.

### Approved configuration

| Setting | Value | Meaning |
|---|---|---|
| `max_edits` | **2** | Two corrections per field |
| `counts_first_save` | **false** | The initial entry into an empty field is data entry, not a correction |
| `counts_no_op_save` | **false** | Resubmitting an unchanged value consumes nothing |

So a field permits one initial entry plus two corrections — three successful writes — before approval is required.

### Counting rules

- **Initial save** into a blank field: free. No allowance consumed, but an edit history row is still written.
- **Unchanged value**: the server compares incoming to stored values field by field. Unchanged fields are skipped entirely — no write, no history row, no allowance consumed.
- **Clearing a value to blank**: consumes an edit. Blanking is a change, and treating it as free would make blank-then-retype an unlimited reset loop.
- **Rejected submission** (validation failure, stale revision, permission denial): consumes nothing. Nothing was written.
- **Counting and enforcement happen inside the write transaction** at serializable isolation, so two concurrent saves cannot both consume the last allowance (US-011 AC-03).
- A repeated `operation_id` returns the original result and consumes nothing.

### Remaining allowance is computed live

```
remaining = max(0, current_policy.max_edits - consumed_count)
```

Consequences:

- Raising the limit from 2 to 3 **unlocks** fields that had consumed 2. This is intended.
- Lowering the limit locks fields that have already exceeded the new value.
- Edit history is never rewritten by a policy change. Only the derived remaining count moves (US-030 AC-03).
- A policy change never recalculates historical financial figures.

### Override workflow

When `remaining` reaches zero the field is locked and the server returns `EDIT_OVERRIDE_REQUIRED`.

1. The Manager submits a request naming the field, the current value, the proposed value, and a reason (US-012 AC-01).
2. An **Admin** approves it when the order's month has never been closed. When the month has ever been closed, approval requires the **Owner**, regardless of the month's current status (BR-08, US-012 AC-03).
3. Approval grants **exactly one** additional edit. It is single-use and bound to that order, that field, and that requester.
4. The Manager then makes the change themselves. The approval id is recorded on the resulting edit row.
5. The grant remains valid until it is used, rejected, or the order's month is closed. It has no silent expiry timer.

Rules that make the control real:

- An override **does not reset** `consumed_count`. The counter keeps incrementing, so a further change needs a further override.
- An override authorizes one field. It never unlocks the order.
- Approving an override is an audited approval action, not a settings change.
- Edit history is never deleted or reset by an intervention (US-012 AC-04).

### Why this is still policy data

The numbers above are approved, not permanent. They live in `cost_edit_policies` with an effective date and a version so the business can tune them without a migration. Application code reads the policy; it does not embed `2`.

If no policy row is configured for a field, the server returns `EDIT_POLICY_UNCONFIGURED` and refuses the edit. It does not fall back to the values in this table.

## Settlement ingestion policy — `settlement-v1`

**Status: approved.** This section closes decision gate D-05 and the design half of D-10. It is normative.

### Three phases, and nothing is effective until the third

1. **Stage.** The file is parsed and normalized into `settlement_entries` with `commit_state_code = staged`. Staged rows have **zero financial effect** and appear in no total (US-015 AC-03).
2. **Validate.** Every row is checked. Each issue is counted and traceable to its source row. Nothing commits automatically, regardless of how clean the file is.
3. **Commit.** An explicit operator action. Only Owner or Admin may commit.

### Partial commits are allowed but never automatic

When some rows fail validation, the operator sees the split and chooses:

- fix the source and re-upload, or
- explicitly commit the valid subset, accepting that coverage is partial.

A partial commit is recorded as such. The batch carries its accepted, rejected, and staged counts so no total can appear fully committed when it is not (US-016 AC-03).

### Duplicates and conflicts

- Re-importing an already-committed source reference with **identical values** is a no-op. It creates no second entry and remains traceable to the original.
- The same reference with **different values** raises a `duplicate_settlement_conflict` exception. It never overwrites committed evidence (US-016 AC-02).
- Idempotency is keyed on the batch `source_checksum` and each row's `operation_id`.

### Unmatched and unknown data goes to suspense

- An entry that cannot be confidently linked to an order is committed with `allocation_state_code = unallocated`. It is **excluded from order profit** and surfaced for review. It is never auto-allocated on a guess (US-017 AC-03).
- An entry whose category is not in the configured mapping lands in `settled_other_amount` with a suspense flag and an exception. It is never guessed into a fee or label bucket.
- An unrecognized report version fails the import visibly. It does not fall back to column-position guessing (US-015 AC-04).

### Category mapping is configuration, not code

Which source columns populate `settled_revenue_amount`, `settled_walmart_fee`, `settled_walmart_label_cost`, and `settled_other_amount` — including sign conventions — is held in `settlement_category_mappings`, keyed by report type and version.

This table ships **empty**. It is populated once real Walmart reports are in hand. Until a mapping exists for a report type, that report cannot be committed.

This is the design resolution of D-10. What remains is data entry, tracked as pending configuration C-01, not an open design decision.

### Coverage is declared, not inferred

The system cannot know that Walmart has finished paying for an order. Coverage is therefore a **human declaration** recorded at reconciliation, alongside the evidence revision it was based on (US-019 AC-01).

| Coverage | Meaning | Effect |
|---|---|---|
| `none` | No committed evidence | Shows "Not settled", never zero |
| `partial` | Some evidence, more expected | Produces a `settled_to_date` figure, never final |
| `complete` | Reviewer declares settlement finished | Produces `settled_profit` |

### Variance classification never auto-confirms

A configurable threshold per store classifies each variance to help reviewers triage:

| Classification | Meaning |
|---|---|
| `minor` | Within the configured threshold |
| `material` | Outside it |
| `unclassified` | No threshold configured |

The threshold supports an absolute amount, a percentage, or both. **It ships unconfigured**, which yields `unclassified` and changes nothing operationally.

Classification is advisory only. **No variance of any size ever reconciles itself.** Every reconciliation is a recorded human decision (US-018 AC-04).

### Manual fallback

Owner or Admin may mark an order reconciled without complete evidence, provided:

- a reason is mandatory,
- the decision is recorded as `manual_fallback` and is permanently distinguishable from an evidence-based one,
- it creates **no synthetic settlement entries** (US-019 AC-04),
- no hard blocker is outstanding.

Hard blockers, which no role may waive:

- an unallocated settlement entry exists on the order,
- an unresolved duplicate conflict exists,
- a currency mismatch exists,
- the order has no posting period.

## Period protection and close policy — `close-v1`

**Status: approved.** This section closes decision gate D-06. It is normative.

### What "protected" actually restricts

Protection follows `has_ever_been_closed`, never current status.

**Protected — Owner only:**

- manual cost field changes
- estimate version creation
- reconciliation decisions and reversals
- settlement entry allocation, linking, and unlinking
- refund cost recovery entries
- moving an order's posting period into or out of the period

**Not protected — normal roles may continue:**

- fulfillment status updates
- order and store manager assignments
- notes and comments
- raising exceptions, and commenting on them
- viewing, reporting, and export

The distinction is whether the action can change a financial figure. Operational housekeeping continues after close.

### Historical correction

The Owner corrects a protected month **in place**. The month stays closed throughout.

1. Owner submits the correction with a mandatory reason and the affected periods.
2. The change is applied and audited as `historical_financial_correction_applied`.
3. A **new snapshot version** is created for the affected month.
4. The prior snapshot is marked superseded and remains fully readable and reportable.

There is no reopen in this path. Reopen exists for substantial rework, is available to Owner and Admin, and still grants no historical edit authority (US-024 AC-02).

Both paths share one invariant: an approved snapshot is never edited or deleted. Correction and reclose add versions (US-023 AC-04, US-024 AC-04).

### Late evidence

Settlement evidence whose event date falls in a closed month is **committed normally**. It is flagged `late_activity_closed_period` and raises an exception.

It does not reopen the month, does not alter the snapshot, and does not require a reopen to be recorded. The Owner decides whether it warrants a correction.

### Readiness invalidation

Readiness is a statement about evidence the reviewer actually saw. It is invalidated by **any change to that month's financial inputs or settlement evidence**:

- a manual cost change on any order in the month
- a new estimate version
- a newly committed settlement entry affecting the month
- a reconciliation decision or reversal
- a new or reopened exception
- an order posted into or out of the month

On invalidation the month returns to `open`, emits `accounting_month_readiness_invalidated`, and records which change caused it so reviewers can see why (US-022 AC-04, US-020 AC-03).

### Snapshot contents and retention

A snapshot records, **per currency**:

- order counts: total, complete, incomplete, deferred
- `selling_amount_total`
- `total_cost`
- `estimated_profit_total`
- `settled_profit_total` and `settled_to_date_total`
- `variance_total`
- settlement coverage counts by `coverage_code`
- unresolved exception count at close
- the included entity revisions and a `content_hash`

Incomplete orders are counted and listed, never silently summed into totals.

Snapshots are retained **indefinitely**. There is no deletion path, no archival purge, and no edit path.

## Refund and cancellation policy — `refund-v1`

**Status: approved.** This section closes decision gate D-07. It is normative.

### Refunds are settlement evidence

A refund is a settlement entry with a negative revenue amount. It reduces `settled_revenue` naturally. There is no refund flag on the order and no refund fulfillment state.

Several partial refunds may exist for one order, each with its own reference, amount, and date (US-027 AC-02).

### Product cost recovery is entered manually

When goods come back, some, all, or none of the buying cost may be recovered. The system cannot know which.

- Recovery is recorded per refund entry as an explicit amount.
- Until it is recorded, recovery is **unknown**, and the order's settled profit is `incomplete` with reason `refund_recovery_unknown`.
- A recovery of zero is recorded explicitly and is distinguishable from unrecorded.
- Recovery never exceeds the original buying cost for the refunded quantity.

The settled profit formula uses buying cost **net of recovery**:

```
settled_profit = settled_revenue
               - (buying_cost_total - total_recovered_cost)
               - warehouse_cost_effective
               - settled_walmart_fee
               - settled_walmart_label
               - settled_other
               - third_party_label_cost
               - other_cost
```

This replaces the `refund_treatment_pending` placeholder introduced with `profit-v1`.

### Cancellations

An order cancelled after costs were incurred keeps those costs and shows a loss.

- Revenue for a cancelled order is **zero**. The imported selling amount does not contribute, because Walmart never pays it.
- All entered costs remain. A cost that was never actually incurred is entered as an explicit zero by the Manager.
- The resulting negative profit appears in the month's figures. A cancellation that cost real money is a real loss.

### Double counting is prevented by basis separation, not by allocation

A refund appears exactly once in each reporting basis:

- in **order month**, attributed to the original order's month,
- in **settlement event month**, attributed to the refund's own date.

The two bases are never summed together. That is what prevents the same refund being deducted twice (US-027 AC-03), and it is why `date_basis` is a required reporting parameter rather than a default.

### Tax

No tax effects exist in any refund calculation. `profit-v1` already established that the selling amount excludes buyer sales tax, which Walmart collects and remits.

## Roles and prioritization

| Role | Established authority | Proposed defaults requiring review |
|---|---|---|
| Owner | Oversees store business, controls historical financial edits | Creates stores; manages access; imports financial reports; views all authorized business stores |
| Admin | Changes store settings, intervenes on edit limits, confirms reconciliation, closes **and reopens** months | Store-scoped access; settlement imports; open-period cost corrections; no historical financial editing, including after reopening a month |
| Manager | Works in assigned stores, enters/corrects manual costs, marks months ready | Reads settlement evidence; raises exceptions; no reconciliation confirmation, month close, or settings changes |

Established authority derives from BR-01 through BR-12. Additional permissions in this backlog are proposals, not implied approvals. [cite:88][cite:684][cite:685][cite:686]

### Capability matrix

This is the single normative permission matrix for the project. The schema, API, architecture, and design documents must reference it rather than restate it. Every "Yes" must be enforced server-side; UI visibility is not an authorization control.

All capabilities are store-scoped. Owner has all stores; Admin and Manager have only their assigned stores (`access-v1`).

| Capability | Owner | Admin | Manager | Source |
|---|:--:|:--:|:--:|---|
| View stores in scope | Yes | Yes | Yes | BR-01 |
| Create store | Yes | No | No | `access-v1` |
| Administer users | Yes | No | No | `access-v1` |
| Assign managers to stores | Yes | No | No | `access-v1` |
| Change store settings | Yes | Yes | No | BR-11 |
| Change cost edit policy | Yes | Yes | No | `cost-edit-v1` |
| Provision accounting months | Yes | Yes | No | `period-v1` |
| Import orders | Yes | Yes | Yes | `access-v1` |
| Import settlement reports | Yes | Yes | No | `access-v1` |
| Commit a partial settlement batch | Yes | Yes | No | `settlement-v1` |
| Enter and correct manual costs in an open period | Yes | Yes | Yes | BR-02, BR-06 |
| Exceed a per-field edit limit | Yes | Yes | Request only | BR-06 |
| Approve an edit override (open period) | Yes | Yes | No | `cost-edit-v1` |
| Approve an edit override (protected period) | **Yes** | **No** | No | BR-08 |
| Create estimate versions | Yes | Yes | Yes | BR-03 |
| Record refund cost recovery | Yes | Yes | Yes | `refund-v1` |
| Raise an exception | Yes | Yes | Yes | US-021 |
| Resolve a data-quality exception | Yes | Yes | Yes | `access-v1` |
| Resolve a financial exception | Yes | Yes | No | `access-v1` |
| Allocate a settlement entry to an order | Yes | Yes | No | `settlement-v1` |
| Confirm reconciliation | Yes | Yes | No | BR-10 |
| Manual-fallback reconciliation | Yes | Yes | No | `settlement-v1` |
| Mark a month ready | Yes | Yes | Yes | BR-07 |
| Close a month | Yes | Yes | No | BR-07 |
| Reopen a closed month | Yes | Yes | No | BR-07 |
| Edit financial records in a previously closed month | **Yes** | **No** | **No** | BR-08 |
| Re-post an order into or out of a protected period | **Yes** | **No** | **No** | BR-08, `period-v1` |
| View audit log | Yes | Yes | No | `access-v1` |
| Export records in scope | Yes | Yes | Yes | `access-v1` |

A `Viewer` or `Accountant` read-only role is **not** approved for this release. Do not create one.

One rule overrides everything in this table. **BR-08 is absolute.** No role except Owner may mutate financial records belonging to a month that has ever been closed, regardless of the month's current status. Reopening changes workflow availability, not historical edit authority (US-024 AC-02).

- **P0:** required for a controlled operational/financial MVP; may be delivered in several increments.
- **P1:** useful after the core workflow is proven.
- **Ready:** business rules are decided and the acceptance criteria are testable. Implementation still needs agreed contracts and tests.
- Every story is now Ready. No story is blocked on an unmade business decision.
- Dependencies describe delivery ordering, not mandatory runtime coupling between modules.

## Screen traceability

| Screen | Purpose |
|---|---|
| UI-01 | Sign in and access recovery |
| UI-02 | Store selection and creation |
| UI-03 | Overview and work queue |
| UI-04 | Orders list |
| UI-05 | Order detail |
| UI-06 | Manual cost editing |
| UI-07 | Settlement batch list |
| UI-08 | Import wizard and batch detail |
| UI-09 | Reconciliation workspace |
| UI-10 | Exceptions queue |
| UI-11 | Monthly close list |
| UI-12 | Monthly close workspace |
| UI-13 | Reports |
| UI-14 | Team and store assignments |
| UI-15 | Store settings |
| UI-16 | Audit log |

UI-17 is retired. See "Withdrawn scope and reserved identifiers".

## Epic A — Access and stores

### US-001 — Sign in securely

**Story:** As an authorized user, I want to sign in and understand session expiry so that business data is available only within an authorized session.

**Priority:** P0 · **Module:** Access · **Screens:** UI-01 · **Dependencies:** None · **Status:** Ready

**Acceptance criteria**
- **AC-01:** Given invalid or expired authentication, when protected data is requested, then no protected record is returned.
- **AC-02:** Given a session expires during editing, when a save is attempted, then the user is prompted to authenticate and no financial action is silently replayed.
- **AC-03:** Given successful authentication, when returning to a saved destination, then its store and record authorization are checked again.
- **AC-04:** Given sign-out, when another user signs in on the same client, then cached business data from the previous session is not displayed.

### US-002 — Select an authorized store

**Story:** As a Manager, I want to select among assigned stores so that my work stays in the correct store context.

**Priority:** P0 · **Module:** Stores · **Screens:** UI-02, application shell · **Dependencies:** US-001 · **Status:** Ready

**Acceptance criteria**
- **AC-01:** Given several assigned stores, when opening the selector, then only authorized stores are offered.
- **AC-02:** Given an unassigned store identifier, when requesting its records directly through an API or deep link, then no store data is disclosed.
- **AC-03:** Given unsaved input, when switching stores, then explicit discard/cancel is required before leaving.
- **AC-04:** Given a store switch, when the new screen loads, then selected rows and incompatible filters from the previous store are cleared.

### US-003 — Create and configure a store

**Story:** As the Owner, I want to register a store and its reporting context so that its orders and financial records remain isolated from other stores.

**Priority:** P0 · **Module:** Stores · **Screens:** UI-02, UI-15 · **Dependencies:** US-001 · **Status:** Ready

**Acceptance criteria**
- **AC-01:** Given valid approved store configuration, when creating a store, then it receives a stable internal identifier distinct from its display name.
- **AC-02:** Given missing reporting currency, reporting timezone, or required integration configuration, when activating financial reporting, then the missing configuration is identified rather than guessed.
- **AC-03:** Given a Manager, when attempting store creation or settings changes, then the server rejects the operation.
- **AC-04:** Given a store rename, when viewing historical records, then their store identity and links remain intact.

### US-004 — Manage store assignments

**Story:** As an authorized access administrator, I want to assign Managers to stores so that multiple Managers can collaborate without assigning every order to an individual.

**Priority:** P0 · **Module:** Team · **Screens:** UI-14 · **Dependencies:** US-003 · **Status:** Ready

**Acceptance criteria**
- **AC-01:** Given two Managers and one store, when both receive assignments, then both can access permitted store records independently.
- **AC-02:** Given one Manager and two stores, when assigned to both, then access is available to both without creating duplicate user identities.
- **AC-03:** Given assignment removal, when the Manager makes a subsequent protected request, then revoked access is denied even if an old screen remains open.
- **AC-04:** Given assignment changes, when saving, then the actor, affected user/store, and change are audited; historical orders are not reassigned or repriced.

## Epic B — Orders and operational context

### US-005 — Import and refresh orders

**Story:** As an operations user, I want Walmart orders imported with their source identifiers so that quantities and selling amounts do not need manual re-entry.

**Priority:** P0 · **Module:** Order integration · **Screens:** UI-03, UI-04 · **Dependencies:** US-003 · **Status:** Ready

**Acceptance criteria**
- **AC-01:** Given a supported order payload, when processing it, then store identity, source order identifier, SKU, order date, quantity, selling amount, currency, and source fulfillment state are preserved.
- **AC-02:** Given the same source order is processed again, when refreshing, then existing order identity is retained without duplicate orders or loss of manual costs. Source order identifiers are unique within a store, not globally.
- **AC-03:** Given a failed refresh, when viewing the list, then the last successful update and failure are visible; stale data is not labeled current.
- **AC-04:** Given an imported change affects protected historical reporting, when processing it, then source evidence is retained but the closed snapshot is not silently rewritten; an exception is raised.
- **AC-05:** Given an order whose date falls outside any provisioned accounting month for its store, when importing, then the order is retained and an exception is raised; the importer does not create, guess, or widen an accounting period (`period-v1`, D-09).
- **AC-06:** Given an order whose true month has already been closed, when importing, then it posts to the earliest currently open month, is marked `deferred_closed_period`, retains its true order date, and the closed month and its snapshot are unchanged (`period-v1`).
- **AC-07:** Given a re-import that corrects an order's date, when processing it, then the order date always updates and is audited; the posting period moves only when both the current and target months are open, and otherwise stays put and raises an exception for Owner review (`period-v1`, BR-08).
- **AC-08:** Given a deferred order, when it is displayed anywhere, then both its posting period and its true order month are visible; neither is shown alone.

### US-006 — Find orders needing work

**Story:** As a Manager, I want to filter orders by cost completeness and review state so that I can prioritize unfinished work.

**Priority:** P0 · **Module:** Orders · **Screens:** UI-04 · **Dependencies:** US-005 · **Status:** Ready

**Acceptance criteria**
- **AC-01:** Given imported orders, when filtering, then order identifier/SKU, order-date range, fulfillment, cost completeness, reconciliation, and month state can be applied independently.
- **AC-02:** Given a date filter, when it is displayed, then its basis is labeled Walmart order date and its reporting timezone is clear.
- **AC-03:** Given no matching orders, when viewing results, then the screen distinguishes filter mismatch from an empty store and offers Clear filters.
- **AC-04:** Given navigation to detail and back, when returning, then filters, stable sorting, pagination, and list position are restored.

### US-007 — Inspect an order without mixing states

**Story:** As a store user, I want operational facts, costs, settlement evidence, and accounting protection shown separately so that one status does not hide another.

**Priority:** P0 · **Module:** Orders · **Screens:** UI-05 · **Dependencies:** US-006 · **Status:** Ready

**Acceptance criteria**
- **AC-01:** Given an order detail, when displayed, then source identifiers, order date, items, quantity, currency, and imported selling amount are read-only and labeled by source.
- **AC-02:** Given a delivered order with refund activity and unresolved reconciliation, when displayed, then all three states remain independently visible.
- **AC-03:** Given partial fulfillment of an order's quantity, when viewing the summary, then the fulfilled and unfulfilled quantity evidence remains accessible instead of being replaced by a misleading single final state.
- **AC-04:** Given no settlement evidence, when displaying financial values, then the screen shows Not settled or Unavailable rather than a known zero payout.

## Epic C — Costs and estimates

### US-008 — Enter buying cost per unit

**Story:** As a Manager, I want to enter the unit buying cost for an order so that changing purchase prices are reflected accurately.

**Priority:** P0 · **Module:** Costing · **Screens:** UI-05, UI-06 · **Dependencies:** US-007 · **Status:** Ready

**Acceptance criteria**
- **AC-01:** Given an order, when entering buying cost, then the input is explicitly per unit and total buying cost is calculated from imported quantity (BR-02; one SKU per order).
- **AC-02:** Given an order with no entered cost, when calculating completeness, then the amount remains unknown; no SKU/store buying-cost default is substituted.
- **AC-03:** Given a known zero cost, when explicitly saved as zero, then it is distinguishable from a blank input.
- **AC-04:** Given a successful change to one order's unit cost, when recalculating, then other orders' costs do not change, including other orders carrying the same SKU.

### US-009 — Enter manual order costs

**Story:** As a Manager, I want to enter estimated Walmart deductions and manual operating costs so that profit can be estimated before settlement.

**Priority:** P0 · **Module:** Costing · **Screens:** UI-06 · **Dependencies:** US-008 · **Status:** Ready

**Acceptance criteria**
- **AC-01:** Given an open editable order, when entering costs, then estimated Walmart fees, estimated Walmart label, third-party label, and other costs are separate fields/categories.
- **AC-02:** Given an unavailable Walmart label estimate, when entering a manual value, then it is identified as manual and does not masquerade as a settled source fact.
- **AC-03:** Given an invalid amount or unsupported currency, when saving, then the field-specific error is shown, the draft is preserved, and no partial financial write occurs.
- **AC-04:** Given third-party and Walmart label inputs, when calculating profit, then the approved combination/deduplication policy is applied; ambiguous cases are flagged rather than silently double-counted.

### US-010 — Apply warehouse defaults and overrides

**Story:** As a Manager, I want to override a store's warehouse charge for an individual order so that exceptional order costs do not alter the default for everyone.

**Priority:** P0 · **Module:** Costing and store policy · **Screens:** UI-05, UI-06, UI-15 · **Dependencies:** US-003, US-008 · **Status:** Ready

**Acceptance criteria**
- **AC-01:** Given a configured store warehouse default, when establishing an order's cost basis, then the applicable default and its policy version are retained as a snapshot.
- **AC-02:** Given a permitted override, when saving it, then both the snapshot default and order override remain visible and only the override determines effective warehouse cost.
- **AC-03:** Given an order with quantity greater than one, when calculating warehouse cost, then the charge is applied once per order, not once per unit.
- **AC-04:** Given an Owner/Admin changes a default, when viewing previously costed orders, then their saved defaults are unchanged; future application follows the approved effective-date policy.

### US-011 — Correct costs within field limits

**Story:** As a Manager, I want to correct mistakes within each field's edit allowance so that ordinary corrections do not require unrestricted financial access.

**Priority:** P0 · **Module:** Costing policy · **Screens:** UI-06 · **Dependencies:** US-008, US-009, US-010 · **Status:** Ready

**Acceptance criteria**
- **AC-01:** Given a saved manual field, when opening editing, then its server-calculated remaining allowance and any lock reason are shown. The allowance is read from configured policy; no numeric limit may be hardcoded in application code or schema defaults (`cost-edit-v1`).
- **AC-02:** Given one field's allowance is exhausted, when editing, then that field is protected without blocking unrelated allowed fields.
- **AC-03:** Given simultaneous saves against the last allowance, when processed, then the approved counter policy and record revision prevent exceeding the limit.
- **AC-04:** Given an unchanged or rejected submission, when updating usage, then nothing is consumed and no edit history row is written, per `cost-edit-v1`. Given an initial entry into a blank field, nothing is consumed. Given a clear-to-blank, one edit is consumed.

### US-012 — Request edit-limit intervention

**Story:** As a Manager, I want to request correction of a locked field so that mistakes can be fixed with accountable approval.

**Priority:** P0 · **Module:** Exceptions and costing policy · **Screens:** UI-06, UI-10 · **Dependencies:** US-011 · **Status:** Ready

**Acceptance criteria**
- **AC-01:** Given a locked field, when requesting help, then the request identifies the field, current/proposed values, store, order, and explanation.
- **AC-02:** Given an Admin handles an open-period request, when correcting or granting approved scoped access, then only the approved field/action is affected and the intervention is audited.
- **AC-03:** Given the record belongs to a protected historical month, when an Admin handles the request, then historical-edit authority is not granted; the request requires Owner resolution.
- **AC-04:** Given intervention, when inspecting history, then previous changes and edit counts are preserved rather than erased through an undocumented reset.

### US-013 — Compare estimate versions

**Story:** As an Owner or Manager, I want to compare estimate versions with settled-to-date evidence so that later fee changes do not erase the original expectation.

**Priority:** P0 · **Module:** Estimates · **Screens:** UI-05, UI-09, UI-13 · **Dependencies:** US-008, US-009, US-010 · **Status:** Ready

**Acceptance criteria**
- **AC-01:** Given a successful cost change, when recomputing the estimate, then the new version records its inputs and calculation-policy version and retains previous versions.
- **AC-02:** Given settlement data arrives later, when comparing, then the selected estimate remains visible and is not overwritten by actual deductions.
- **AC-03:** Given missing required inputs, when displaying profit/ROI, then completeness is explicit and the result is not labeled final; zero-denominator ROI is Unavailable, not infinity.
- **AC-04:** Given a comparison, when displaying amounts, then estimate version, settlement coverage, manual-cost revision, and currency are identified; payout is not mislabeled profit.

### US-014 — Save safely during concurrent work

**Story:** As a Manager, I want conflicts and uncertain saves handled explicitly so that another user's work is not silently overwritten.

**Priority:** P0 · **Module:** Shared mutation handling · **Screens:** UI-06 and all financial dialogs · **Dependencies:** US-008, US-001 · **Status:** Ready

**Acceptance criteria**
- **AC-01:** Given a stale record revision, when saving, then the server rejects the stale update and the UI preserves the draft for comparison.
- **AC-02:** Given a month closes after editing begins, when saving, then current financial protection is checked and the write is rejected without consuming a successful edit.
- **AC-03:** Given a timeout with unknown outcome, when retrying, then an operation lookup or retry-safe identifier prevents duplicate changes and counter increments.
- **AC-04:** Given a financial write is pending, when displaying feedback, then success is shown only after the authoritative result is confirmed.

## Epic D — Settlement evidence

### US-015 — Validate a settlement import

**Story:** As an authorized financial operator, I want to preview and validate a settlement report so that malformed or ambiguous data does not silently enter financial totals.

**Priority:** P0 · **Module:** Settlements integration · **Screens:** UI-07, UI-08 · **Dependencies:** US-005 · **Status:** Ready

**Acceptance criteria**
- **AC-01:** Given a supported report, when staging it, then its source identifier/checksum, reporting interval, parser/mapping version, and normalized row preview are retained.
- **AC-02:** Given invalid amounts, currencies, dates, categories, duplicate conflicts, unmatched references, or historical impacts, when validating, then each issue is counted and traceable to its row.
- **AC-03:** Given preview mode, when viewing results, then a Not committed state is clear and preview rows have no effective financial impact.
- **AC-04:** Given an unsupported report version, when processing it, then import fails visibly rather than guessing column mappings.

### US-016 — Commit imports without duplication

**Story:** As an authorized financial operator, I want safe import commits and retries so that the same payout evidence cannot double revenue or fees.

**Priority:** P0 · **Module:** Settlements · **Screens:** UI-08 · **Dependencies:** US-015 · **Status:** Ready

**Acceptance criteria**
- **AC-01:** Given an already committed report/transaction, when importing it again, then no second effective entry is created and duplicate results are traceable.
- **AC-02:** Given the same reference with different financial values, when validating/committing, then an explicit conflict is raised instead of overwriting evidence.
- **AC-03:** Given a batch operation fails, when inspecting it, then accepted, rejected, staged, and effective states are unambiguous and totals cannot appear fully committed prematurely.
- **AC-04:** Given new source evidence affecting a closed month, when retaining it, then the closed snapshot remains unchanged and an Owner-review historical-impact exception is created.

### US-017 — Inspect multiple entries per order

**Story:** As a financial reviewer, I want every settlement entry and later adjustment visible so that one payout cell cannot hide changing financial evidence.

**Priority:** P0 · **Module:** Settlements · **Screens:** UI-05, UI-07, UI-08 · **Dependencies:** US-016 · **Status:** Ready

**Acceptance criteria**
- **AC-01:** Given several entries for one order, when opening its ledger view, then all included entries remain accessible with source reference, category, signed amount, currency, date, and batch.
- **AC-02:** Given payout-summary data and transaction-level data, when calculating order amounts, then the payout summary is not added again as order revenue.
- **AC-03:** Given an unmatched transaction, when displaying totals, then it remains explicitly unallocated and excluded from order profit until authorized allocation.
- **AC-04:** Given a source correction, when representing it, then the approved adjustment/supersession workflow preserves original evidence rather than offering unrestricted ledger edits.

## Epic E — Reconciliation and exceptions

### US-018 — Review reconciliation differences

**Story:** As an Owner or Admin, I want to compare like-for-like estimated Walmart net with settlement evidence so that unexplained differences can be investigated.

**Priority:** P0 · **Module:** Reconciliation · **Screens:** UI-09 · **Dependencies:** US-013, US-017 · **Status:** Ready

**Acceptance criteria**
- **AC-01:** Given an order, when opening review, then the estimate version, settlement revision, category mapping, variance, coverage, and missing-cost state are visible.
- **AC-02:** Given manual operating costs, when comparing expected versus received Walmart net, then these are identified separately and payout is not compared directly with profit.
- **AC-03:** Given missing coverage or unallocated evidence, when computing eligibility, then approved hard blockers are visible rather than hidden by a zero variance.
- **AC-04:** Given no approved tolerance/completeness policy, when evaluating automatic readiness, then the feature stays gated rather than inventing a threshold.

### US-019 — Confirm reconciliation or manual fallback

**Story:** As an Owner or Admin, I want to record an explicit reconciliation decision so that settlement completion has accountable evidence.

**Priority:** P0 · **Module:** Reconciliation · **Screens:** UI-09 · **Dependencies:** US-018 · **Status:** Ready

**Acceptance criteria**
- **AC-01:** Given eligible current evidence, when confirming, then the reviewer, timestamp, evidence revision, estimate/cost basis, and declared coverage are recorded.
- **AC-02:** Given a Manager calls the confirmation operation directly, when authorization runs, then it is rejected regardless of hidden UI controls.
- **AC-03:** Given a manual fallback is permitted, when submitting it, then reason, evidence reference, coverage, and manual-decision labeling are mandatory.
- **AC-04:** Given a fallback decision, when stored, then it does not create fake settled transactions or bypass unwaivable hard blockers.

### US-020 — Reevaluate after new financial activity

**Story:** As a reviewer, I want later settlement activity to trigger renewed review so that an old confirmation does not falsely certify new evidence.

**Priority:** P0 · **Module:** Reconciliation · **Screens:** UI-05, UI-09, UI-10 · **Dependencies:** US-019 · **Status:** Ready

**Acceptance criteria**
- **AC-01:** Given a confirmed order and a material later entry, when ingesting it, then the live reconciliation becomes Review required according to the approved policy.
- **AC-02:** Given a previous confirmation, when reviewing later activity, then it remains in history with its original coverage and evidence revision.
- **AC-03:** Given a month marked ready, when its approved readiness basis changes, then readiness is invalidated and affected reviewers can identify why.
- **AC-04:** Given a closed month, when later activity arrives, then it raises a late-activity exception without automatically reopening or replacing the approved snapshot.

### US-021 — Resolve exceptions with evidence

**Story:** As an authorized reviewer, I want an exception work queue so that missing data, import conflicts, and financial blockers have explicit owners and resolutions.

**Priority:** P0 · **Module:** Exceptions · **Screens:** UI-10 · **Dependencies:** US-012, US-015, US-018 · **Status:** Ready

**Acceptance criteria**
- **AC-01:** Given an exception, when displayed, then type, severity, related records, assignment, age, and blocking effect are visible.
- **AC-02:** Given a user resolves it, when submitting, then authorized disposition, explanation, and underlying evidence/correction are required.
- **AC-03:** Given a hard blocker remains unresolved in the underlying data, when acknowledging a note, then the blocker is not silently waived.
- **AC-04:** Given changed evidence invalidates a prior resolution, when reevaluating, then the issue reopens or links to a successor while preserving the previous decision.

## Epic F — Monthly close

### US-022 — Review month readiness

**Story:** As a Manager, I want to review a month and mark it ready so that the Owner/Admin can make the final close decision.

**Priority:** P0 · **Module:** Month close · **Screens:** UI-11, UI-12 · **Dependencies:** US-019, US-021 · **Status:** Ready

**Acceptance criteria**
- **AC-01:** Given a store/month, when opening readiness, then missing costs, settlement coverage, reconciliation, unallocated evidence, and blocking exceptions link to supporting records.
- **AC-02:** Given failed required checks, when marking ready, then the server rejects it and lists actionable blockers.
- **AC-03:** Given successful readiness marking, when viewing the month, then it is Ready for review, not Closed, with actor, timestamp, and reviewed revision.
- **AC-04:** Given material data changes before closing, when readiness is reevaluated, then the old ready decision cannot be used silently against new evidence.

### US-023 — Close a reviewed month

**Story:** As an Owner or Admin, I want to explicitly close an eligible month so that an approved version of its financial results is protected.

**Priority:** P0 · **Module:** Month close · **Screens:** UI-12 · **Dependencies:** US-022 · **Status:** Ready

**Acceptance criteria**
- **AC-01:** Given settlement/reconciliation is incomplete, when close is requested, then it fails even if a readiness button was previously used.
- **AC-02:** Given eligibility is satisfied, when confirming close, then store, month, reporting basis, warnings, and current evidence revision are shown before submission.
- **AC-03:** Given evidence changes or a conflicting financial write races with close, when committing, then server-side serialization/revision controls prevent a snapshot based on stale checks.
- **AC-04:** Given successful close, when reading history, then the actor, time, policy version, included revisions, and immutable/versioned report snapshot are available; duplicate retries create no duplicate close.

### US-024 — Reopen without erasing protection

**Story:** As an Owner or Admin, I want to reopen a month for review without deleting its previous close or granting unauthorized historical edits.

**Priority:** P0 · **Module:** Month close · **Screens:** UI-12 · **Dependencies:** US-023 · **Status:** Ready

**Acceptance criteria**
- **AC-01:** Given a closed month, when an Owner/Admin reopens it, then a reason and reopen event are recorded and previous snapshots remain available.
- **AC-02:** Given an Admin reopened a month, when Admin or Manager attempts a protected historical financial edit, then the operation is still denied.
- **AC-03:** Given a reopened month, when displayed, then Open/Reopened and Previously closed are visible together.
- **AC-04:** Given reclose eligibility, when closing again, then a new snapshot/version is created without replacing earlier approved versions.

### US-025 — Correct protected historical finances

**Story:** As the Owner, I want a controlled historical-correction workflow so that errors can be corrected without destroying previously approved results.

**Priority:** P0 · **Module:** Historical corrections · **Screens:** UI-06, UI-10, UI-12 · **Dependencies:** US-024 · **Status:** Ready

**Acceptance criteria**
- **AC-01:** Given an authorized historical correction, when submitting, then a reason, affected fields, expected revisions, and affected periods are explicitly reviewed.
- **AC-02:** Given an Admin/Manager requests the same action directly, when authorized, then historical financial modification is rejected.
- **AC-03:** Given correction completes, when inspecting reports, then the old snapshot remains reproducible and any proposed revised close has a distinct version.
- **AC-04:** Given unresolved policy on correction/reopen/restatement, when implementation reaches this workflow, then it remains blocked instead of choosing an accounting treatment automatically.

## Epic G — Reports and exports

### US-026 — Report by explicit financial basis

**Story:** As the Owner, I want separate order-month, posting-month, settlement-activity, and snapshot views so that financial totals answer a clear question.

**Priority:** P0 · **Module:** Reporting · **Screens:** UI-13 · **Dependencies:** US-013, US-017, US-023 · **Status:** Ready

**Acceptance criteria**
- **AC-01:** Given order-month reporting, when grouping orders, then membership follows `order_date` resolved in the store's timezone, including orders that were posted forward to a later period.
- **AC-02:** Given a report, when displaying it, then store, currency, date basis, estimate/settled/snapshot basis, completeness, and calculation version are visible. The date basis names exactly one of order month, posting month, settlement event month, or snapshot.
- **AC-03:** Given a closed snapshot, when viewing it later, then live subsequent edits or evidence do not silently alter its approved values.
- **AC-04:** Given amounts in several currencies, when summarizing, then totals are returned grouped by currency and never combined; no conversion exists in this release.
- **AC-05:** Given orders posted forward from a closed period, when viewing posting-month and order-month reports side by side, then the difference between the two bases is reportable and attributable to specific orders.

### US-027 — See refunds in the event month

**Story:** As the Owner, I want refunds visible in the month they occurred and linked to their original orders so that later refunds are not missed or counted twice.

**Priority:** P0 · **Module:** Reporting · **Screens:** UI-05, UI-12, UI-13 · **Dependencies:** US-017, US-026 · **Status:** Ready

**Acceptance criteria**
- **AC-01:** Given a refund after its order month, when viewing refund-event reporting, then it appears under its refund-event date/month and links to the original order/month.
- **AC-02:** Given several partial refunds, when displaying activity, then their separate references, amounts, and remaining source context remain traceable.
- **AC-03:** Given order-cohort and refund-activity views overlap, when calculating a combined result, then an approved allocation policy prevents the same refund being deducted twice.
- **AC-04:** Given tax, return recovery, cancellation cost, or refund timing treatment is unapproved, when reporting, then affected results carry an unresolved-policy indication rather than an invented final amount.

### US-028 — Export authorized results

**Story:** As an authorized user, I want to export the records I am permitted to view so that external checks retain the same reporting meaning.

**Priority:** P1 · **Module:** Reporting/export · **Screens:** UI-04, UI-08, UI-13, UI-16 · **Dependencies:** US-026, US-031 · **Status:** Ready

**Acceptance criteria**
- **AC-01:** Given an export request, when submitting it, then selected rows versus all filtered rows is explicit and store, filters, date basis, currency, and snapshot/version travel with the result.
- **AC-02:** Given a background export/download, when processing and retrieving it, then current user/store authorization is enforced at both stages.
- **AC-03:** Given sensitive fields are not authorized, when exporting, then they remain excluded; download links do not grant indefinite public access.
- **AC-04:** Given text fields containing spreadsheet formula prefixes, when exporting CSV, then the approved export policy prevents unintended formula execution.

## Epic H — Audit, settings, and usability

### US-030 — Change store policies prospectively

**Story:** As an Owner or Admin, I want versioned store settings so that changing future behavior does not unexpectedly rewrite historical results.

**Priority:** P0 · **Module:** Settings · **Screens:** UI-15 · **Dependencies:** US-003, US-031 · **Status:** Ready

**Acceptance criteria**
- **AC-01:** Given a Manager attempts a settings write, when authorized, then it is rejected.
- **AC-02:** Given a policy change, when reviewing it, then effective date, affected future behavior, and historical impact are explicit.
- **AC-03:** Given a changed warehouse default, calculation rule, or edit allowance, when applying it, then historical snapshots are not recalculated silently.
- **AC-04:** Given integration settings, when reading them, then existing secret values are not returned; replacement is explicit and secret values are excluded from audit payloads.

### US-031 — Inspect an audit trail

**Story:** As an authorized reviewer, I want a traceable history of financial and permission actions so that decisions and corrections can be explained.

**Priority:** P0 · **Module:** Audit · **Screens:** UI-05, UI-12, UI-16 · **Dependencies:** US-001 · **Status:** Ready

**Acceptance criteria**
- **AC-01:** Given a cost correction, access change, import commit, reconciliation, close/reopen, policy change, or historical correction, when it succeeds, then actor/service identity, time, store, target, action, operation ID, and relevant revision/change are recorded.
- **AC-02:** Given a workflow requires a reason, when auditing it, then the explanation and decision context are retained without credentials or unnecessary customer PII.
- **AC-03:** Given application users view audit events, when requesting mutation, then no standard edit/delete operation is available to rewrite history.
- **AC-04:** Given an audit screen or export, when accessing it, then sensitive field visibility and store scope are authorized independently of ordinary order access.

### US-032 — Complete work on keyboard and mobile

**Story:** As a user on a phone or using a keyboard or assistive technology, I want accessible controls and clear failures so that essential workflows do not depend on a mouse or large monitor.

**Priority:** P0 · **Module:** Shared UI · **Screens:** All · **Dependencies:** None; apply during every story · **Status:** Ready as proposed quality targets

**Acceptance criteria**
- **AC-01:** Given an essential workflow, when using the keyboard, then controls are reachable with visible focus; dialogs manage focus, provide dismissal, and return focus appropriately.
- **AC-02:** Given loading, empty, partial, stale, offline, validation, and permission states, when they occur, then the state is explained without false success or loss of entered data.
- **AC-03:** Given a narrow viewport or 200% zoom, when using the application, then essential labels/actions remain available; a comparison does not hide its currency or estimate/settlement basis.
- **AC-04:** Given status changes or validation errors, when communicated, then text and accessible announcements supplement color; reduced-motion preferences are respected.

### US-033 — See actionable overview work

**Story:** As a Manager or Owner, I want a store overview of unfinished work so that I can navigate directly to the next meaningful action.

**Priority:** P1 · **Module:** Overview · **Screens:** UI-03 · **Dependencies:** US-006, US-021, US-022 · **Status:** Ready

**Acceptance criteria**
- **AC-01:** Given an authorized store, when opening overview, then missing costs, unresolved exceptions, reconciliation work, and month readiness are available as links to filtered views.
- **AC-02:** Given summary counts, when selecting one, then the destination uses the same store/scope/basis and explains differences caused by refreshed data.
- **AC-03:** Given financial summaries, when displaying them, then currency, estimate/settled/snapshot basis, completeness, and freshness are explicit.
- **AC-04:** Given no actionable work, when displaying overview, then an all-clear state is shown without fabricated transactions, charts, or example amounts.

## Decision gates

**All decision gates are closed.** Every business rule required to build this system has been decided and recorded in a versioned policy section above.

What remains is listed under "Pending configuration" below. That is data entry awaiting real Walmart files, not an unmade decision.

An AI builder encountering a question not answered by this document must still report the blocker rather than choose an answer. New questions become new gates; they are not resolved by inference.

### Closed gates

| Gate | Was | Resolution |
|---|---|---|
| D-01 | Authentication provider; store scope; who creates stores, administers users, imports, exports, views audit | **Closed.** `access-v1`. In-app email and password with mandatory MFA for Owner/Admin; Admin and Manager scoped to assigned stores; Owner alone creates stores and administers users; Managers import and export within scope; audit is Owner/Admin; exception resolution split by class. |
| D-02 | Timezone, month boundary, source timestamp, late and corrected dates, cross-currency | **Closed.** `period-v1`. Per-store IANA timezone, calendar months in local time, customer purchase timestamp, transaction date separated from posting period, late orders post forward, no currency conversion. Recorded as BR-13. |
| D-03 | Profit formulas, tax basis, label combination, ROI basis, rounding, unknown-versus-zero | **Closed.** `profit-v1`. Six cost components, additive labels, no tax term, exact decimal with one half-up rounding, ROI over total cost, unknown never coerced to zero. |
| D-04 | Edit limits, first-save and no-op treatment, clearing, intervention mechanism, policy-change effects | **Closed.** `cost-edit-v1`. Two corrections per field, first save free, no-op free, clearing counted, override grants one single-use edit, allowance computed live. |
| D-05 | Supported reports, import atomicity, suspense treatment, coverage, tolerance, fallback eligibility | **Closed.** `settlement-v1`. Stage, validate, then explicit operator commit; partial commits allowed but never automatic; unmatched and unknown data goes to suspense; coverage is declared by a human; variance classification never auto-confirms; manual fallback is Owner/Admin with a mandatory reason and unwaivable hard blockers. |
| D-06 | Closure-history protection, correction procedure, late evidence, readiness invalidation, snapshot contents | **Closed.** `close-v1`. Protection follows `has_ever_been_closed`; financial operations are enumerated and Owner-only, operational ones continue; the Owner corrects in place and a new snapshot version is created; late evidence commits and flags without reopening; any financial change invalidates readiness; snapshots are retained indefinitely. |
| D-07 | Partial refunds, returns, recovered product cost, cancelled-order expenses, tax, cohort-versus-event allocation | **Closed.** `refund-v1`. Refunds are negative settlement entries; product cost recovery is entered manually and is unknown until recorded; cancelled orders keep incurred costs and show a loss; double counting is prevented by never summing across reporting bases. |
| D-09 | Accounting-month provisioning | **Closed.** `period-v1`. A scheduled job maintains provisioned months ahead per store; Owner and Admin may also provision manually for backfill. Import never creates a period. |
| D-10 | Settlement category mapping | **Closed as a design decision.** `settlement-v1`. Mapping is configuration held in `settlement_category_mappings`, keyed by report type and version, shipping empty. Unknown categories go to suspense and are never guessed. Populating it is tracked as C-01. |
| D-08 | Manager payment visibility and accounting treatment | **Withdrawn** with the manager payment notes scope. See "Withdrawn scope and reserved identifiers". |

### Earlier resolutions

| Was | Resolution |
|---|---|
| Does an order contain multiple SKUs or line items? | No. One SKU per order, quantity may exceed one. Recorded as BR-02. No order line-item entity exists. |
| Who may reopen a closed month? | Owner **and** Admin, per BR-07. Admin reopening never confers historical financial edit authority, per BR-08 and US-024 AC-02. |
| Store currency model | Per-store reporting currency. Recorded as BR-12. No conversion exists. |
| Is manager compensation in scope? | No. Removed from this release. Recorded as BR-09. |
| Which document wins on conflict? | This one. See "Document set and precedence". |

## Pending configuration

These are not decisions. They are values that can only come from real external data, and they block **production use** of specific features rather than their implementation.

| Id | Item | Blocks | Until populated |
|---|---|---|---|
| C-01 | Settlement category mappings per Walmart report type and version: source column, target field, sign convention | Committing settlement imports for that report type | Import of an unmapped report type is refused with a clear error. The feature is built and testable against fixtures |
| C-02 | The catalogue of supported Walmart report types and versions | Same as C-01 | An unrecognized report version fails visibly rather than guessing columns |
| C-03 | Per-store variance classification thresholds | Nothing | Variances are returned `unclassified`, which changes no behaviour |
| C-04 | Per-store reporting timezone and currency | Financial reporting for that store | Store activation is refused until both are set; never guessed |
| C-05 | Month provisioning horizon per store | Nothing | The scheduled job uses its configured horizon; absent configuration, provisioning is manual |

C-01 and C-02 should be populated from real Walmart files before settlement import goes live. Build against recorded fixtures in the meantime.
## Withdrawn scope and reserved identifiers

Identifiers are never reused or renumbered. Withdrawn items are recorded here so that stale references can be diagnosed.

| Identifier | Was | Status |
|---|---|---|
| US-029 | Record monthly manager payment decisions | **Withdrawn.** Manager compensation is handled outside the application (BR-09). Do not implement. |
| UI-17 | Manager payment notes screen | **Withdrawn** with US-029. |
| D-08 | Manager payment visibility and accounting treatment | **Withdrawn** with US-029. |

No table, endpoint, module, or screen for manager compensation may be created. If compensation returns to scope, open a new US identifier.

## Proposed delivery sequence

| Increment | Stories | Demonstrable result |
|---|---|---|
| 1 — Foundation | US-001–004, US-031; US-032 throughout | Store-scoped authentication, access management, audit foundation |
| 2 — Working order-cost slice | US-005–014, US-030 | Import orders, enter/correct costs, compare estimates, enforce limits and concurrency |
| 3 — Settlement evidence | US-015–017 | Preview and commit supported reports without duplicates |
| 4 — Financial review | US-018–021 | Evidence-backed reconciliation and exception resolution |
| 5 — Close and reports | US-022–027 | Protected month snapshots, controlled historical review, explicit financial reports |
| 6 — Convenience | US-028, US-033 | Exports and an actionable overview |

Module dependencies can be implemented using public contracts and test doubles during development. Do not require a finished overview to deliver order costing. US-030's policy capabilities should be delivered alongside the features that need them rather than delayed until all settings are implemented.

## Definition of ready

Before implementation begins on a story:
- The policy sections it depends on are read: `access-v1`, `period-v1`, `profit-v1`, `cost-edit-v1`, `settlement-v1`, `close-v1`, `refund-v1`.
- Any pending configuration it relies on is either populated or stubbed with recorded fixtures.
- Acceptance criteria and required failure cases are agreed.
- Role, store scope, record state, and historical protection are defined.
- Input/output API contracts and financial source/date/currency semantics are agreed.
- Database changes and compatibility/migration expectations are reviewed.
- Required UI states and test cases are identified.

A story is not ready merely because a screen mockup or database table exists.

## Definition of done

Before a story is accepted:
- Every acceptance criterion has a passing automated or documented manual test with the story/criterion ID.
- Authorization is tested on server operations, including direct requests outside permitted store scope.
- Financial writes validate current permissions, versions, edit limits, and historical protection within a safe transaction boundary.
- Applicable retries/imports cannot double effective records or counters.
- Historical financial facts and snapshots are preserved according to the approved policy.
- Loading, empty, validation, conflict, permission, stale-data, and uncertain-outcome states are tested where applicable.
- No secrets or unnecessary personal data appear in logs, URLs, exports, or client-visible errors.
- Financial totals and completeness are validated against approved business test cases, not only UI snapshots.
- Schema migrations, rollback/recovery implications, and API compatibility are documented where changed.
- Keyboard, narrow-viewport, and zoom checks pass for changed screens.
- No approved policy was reimplemented a second time; `profit-v1` and `cost-edit-v1` each have exactly one implementation.
- No pending-configuration value was invented as a default.

## Modular implementation guardrails

These are proposed engineering requirements for the rebuild, not additional confirmed business rules.

- Keep Access, Stores, Orders, Costing, Estimates, Settlements, Reconciliation, Exceptions, Month Close, Reports, Team, Settings, and Audit as explicit module boundaries.
- Share domain identifiers and public contracts; do not let a feature import another feature's private persistence internals.
- Do not bind a screen directly to a database table or allow browser-to-database bypasses of workflow policy.
- Keep fulfillment, refund, reconciliation, and accounting protection separate as four independent dimensions; no universal order-status switch controlling everything. See "Binding interpretation of BR-10".
- Store every monetary amount with its currency and never aggregate across currencies. No conversion exists in this release (`period-v1`).
- Keep an order's transaction date and its posting period as separate fields, and never derive one from the other in the database (`period-v1`).
- Use explicit business commands for saving costs, committing imports, confirming reconciliation, closing, reopening, and historical correction; do not expose unrestricted financial CRUD.
- Keep authoritative financial calculations on the server and record calculation/policy versions. UI previews are advisory.
- Preserve source evidence, versioned estimates, and close snapshots as distinct records rather than mutable columns overwriting one another.
- Plan additive/compatible schema and API changes; include backfill and retirement steps for breaking changes.
- Treat story dependencies as delivery planning, not an instruction to build distributed microservices.

## AI implementation handoff

Use the following as a reusable implementation instruction after approving a story:

```text
Implement only the selected story and its approved acceptance criteria.
Read its linked business rules, policy sections, API contracts, and schema first.
walmart-user-stories.md is authoritative; if the schema or API document disagrees with it, stop and report the conflict.
If required policy or a contract is missing, report the blocker instead of inventing it.
Never hardcode a policy value such as an edit limit, tolerance, timezone, currency, or profit formula.
Never collapse the four order state dimensions, derive the posting period, or check current month status where closure history is required.
List the modules, tables, endpoints, and tests expected to change before editing.
Do not modify another module's internals or introduce unrestricted financial CRUD.
Write tests for the acceptance criteria, permissions, and applicable failure cases.
Preserve estimate history, settlement evidence, and closed-month protection.
Use revision checks and safe retry handling for applicable financial writes.
Document migrations and compatibility impact.
Finish with changed files, test results, remaining blockers, and criterion coverage.
Do not claim the entire app is complete because this one story passes.
```

Select a story by its US identifier in the implementation task. Keep identifiers stable as wording evolves; split oversized stories into traceable child stories rather than renumbering the entire backlog.
