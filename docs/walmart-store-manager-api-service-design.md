# Walmart Store Manager Web App API and Service Design

**Version:** 1.0  
**Date:** 23 October 2026  
**Status:** Proposed service and endpoint contracts. Subordinate to `walmart-user-stories.md`.

## Authority

`walmart-user-stories.md` is authoritative. This document implements it and must not contradict it.

Deliberate omissions, so that no rule lives in two places:

| Not stated here | Read it in |
|---|---|
| Permission matrix | `walmart-user-stories.md` → "Capability matrix" |
| Order state vocabulary | `walmart-user-stories.md` → "Binding interpretation of BR-10" |
| Numeric edit limits | `walmart-user-stories.md` → "Manual cost edit policy — `cost-edit-v1`". Read at runtime from `cost_edit_policies`; never a constant here or in code |
| Profit formulas | `walmart-user-stories.md` → "Financial calculation policy — `profit-v1`". Implemented once, in the domain layer |
| Timezones, month boundaries, posting rules | `walmart-user-stories.md` → "Period and timezone policy — `period-v1`" |
| Table and column definitions | `walmart-store-manager-database-schema.md` |

Four constraints apply to every endpoint in this document:

1. **Authorization is server-side.** Hidden UI controls are not a control.
2. **Four state dimensions stay separate.** No endpoint accepts or returns a combined `order_status`.
3. **Every monetary value travels with its currency.** No endpoint returns a bare amount, and no endpoint sums across currencies.
4. **Gated values are never defaulted.** If a policy required to answer a request is unconfigured, return a blocker error, not an assumption.

## Purpose

This document defines the API and service-layer design for the Walmart Store Manager web application. The goal is to protect the modular database design from being flattened into weak CRUD endpoints and to ensure that business workflows such as reconciliation, month close, reopen, cost edit overrides, and settlement ingestion remain explicit and controlled.[cite:685][cite:88][cite:747]

The application should expose business-oriented actions rather than generic record mutation wherever the process has financial, audit, or workflow meaning. This is especially important because the app must preserve estimated versus settled values, support multiple settlement entries over time, apply per-field edit controls, and enforce close-period permissions.[cite:88][cite:685][cite:718]

## Design goals

The API and service layer should achieve the following:

- Keep business rules centralized in service methods.
- Avoid hidden workflow transitions inside generic update endpoints.
- Make permission checks explicit and testable.
- Support audit logging as a side effect of sensitive actions.
- Keep imported source data, manual enrichment, and financial review distinct.
- Allow future change without rewriting every endpoint.[cite:88][cite:685][cite:718]

## Architectural style

A modular REST-style API is appropriate here because the system has clearly defined resources and operational workflows. However, the service layer should be action-driven even when the external API remains REST-shaped. In practice, this means some endpoints are normal resource endpoints and others are workflow endpoints such as `close`, `reopen`, `reconcile`, or `approve-override`.[cite:685][cite:703]

Recommended backend layers:

- Controller layer: request parsing, response formatting, auth context.
- Policy layer: authorization rules.
- Service layer: business workflows and transaction boundaries.
- Repository/data-access layer: persistence only.
- Audit/event layer: append-only logging and approvals.[cite:718][cite:212]

## Service boundaries

Define services by business responsibility, not by table.

| Service | Responsibility |
|---|---|
| AuthService | login, MFA, sessions, password lifecycle, lockout |
| ExceptionService | exception lifecycle, class-based resolution authority, hard-blocker enforcement |
| UserAccessService | users, roles, permission checks |
| StoreService | stores, settings, store manager assignments |
| AccountingMonthService | create month, mark ready, close, reopen |
| ChecklistService | checklist definitions and month checklist results |
| OrderService | order retrieval, fulfillment state updates, assignment |
| PeriodService | timezone resolution, month boundaries, posting assignment and re-posting under `period-v1` |
| OrderCostService | cost entry, edit limit enforcement, overrides |
| EstimateService | estimate snapshot creation and current estimate selection |
| SettlementImportService | settlement batch ingestion, parsing, linking |
| ReconciliationService | variance review, exception handling, reconcile status |
| ImportService | import batches, errors, staging records |
| ReportingService | query/report views and export generation |
| CalculationService | the single implementation of `profit-v1`: profit, total cost, ROI, variance, completeness |
| AuditService | audit events, approvals, compliance traces |

This separation prevents the `OrderService` from becoming an unmaintainable god service.

## API conventions

### General rules

- Use UUIDs as resource identifiers.
- Use ISO dates and timestamps.
- Return stable codes for statuses and error types.
- Use optimistic concurrency or version fields where high-risk updates occur.
- Prefer explicit action endpoints for business transitions.
- For list endpoints, support filtering, sorting, pagination, and export mode.

### Response envelope

Use a predictable JSON shape for consistency.

```json
{
  "data": {},
  "meta": {},
  "errors": []
}
```

For simple mutation actions, return updated summary objects plus audit-relevant metadata where useful.

### Error format

```json
{
  "errors": [
    {
      "code": "MONTH_CLOSED",
      "message": "Financial edits are not allowed because the accounting month is closed.",
      "field": null
    }
  ]
}
```

Stable machine-readable error codes are important for AI-assisted clients and frontend workflows.

## Authorization model

Permissions must be enforced in the backend, not just in the UI. This is necessary because the app includes privileged actions such as reopening months, editing after field edit limits, and protecting past closed financial records.[cite:685][cite:88][cite:212]

### Core permissions

The normative matrix lives in `walmart-user-stories.md` → "Capability matrix". It is not reproduced here, because two copies of a permission matrix is how the previous version drifted.

Implementation requirements:

- Every protected operation resolves its decision through `UserAccessService`, which reads the matrix. No endpoint hardcodes a role comparison.
- All capabilities are store-scoped. Owner has every store; Admin and Manager have only their assignments (`access-v1`).
- A capability absent from the matrix resolves to **deny**. It is never granted by analogy to a neighbouring role.
- There is no `Viewer` role in this release. Do not implement one.

### Closed-period rule

Financial mutation endpoints must reject the request when the order's accounting month has **ever** been closed, unless the actor is the Owner and the operation is an explicitly allowed historical correction.

The check reads `accounting_months.has_ever_been_closed`, **not** `status_code`. An Admin who reopens a month gains workflow access, not historical financial edit authority (BR-08, US-024 AC-02). An implementation that checks `status_code != 'closed'` is wrong even though it will pass naive testing.

Error code for this case: `HISTORICAL_PERIOD_PROTECTED`. The older `MONTH_CLOSED` code covers only the current-status case.

## Resource groups and endpoints

## Auth and users

Authentication is in-app email and password per `access-v1`. The security obligations listed there are requirements on these endpoints.

### `POST /auth/login`

Authenticates a user. Returns an MFA challenge rather than a session when MFA is enabled.

Requirements:
- per-account and per-IP rate limiting with exponential backoff,
- temporary lockout after repeated failures,
- identical response timing and wording for unknown account and wrong password,
- session identifier rotated on success.

### `POST /auth/mfa/verify`

Completes a TOTP challenge and issues the session. **Owner and Admin cannot hold a session without it.**

### `POST /auth/logout`

Ends a session by revoking it server-side. A revoked token is rejected immediately, not merely dropped by the client (US-001 AC-04).

### `POST /auth/password-reset/request`

Always returns the same response whether or not the account exists.

### `POST /auth/password-reset/confirm`

Consumes a single-use, time-limited token. On success, **revokes every existing session for that user**.

### `POST /auth/password/change`

Requires the current password. Revokes all other sessions.

### `GET /me`

Returns current user profile and permissions.

### `GET /users`

List users.

Filters:
- role
- active
- store_id optional

### `POST /users`

Create user.

### `PATCH /users/{userId}`

Update profile and active state.

### `GET /roles`

List role definitions.

## Stores

### `GET /stores`

List stores.

Filters:
- active
- manager_user_id

### `POST /stores`

Create store.

### `GET /stores/{storeId}`

Get store detail.

### `PATCH /stores/{storeId}`

Update store identity fields.

### `GET /stores/{storeId}/settings`

Get store settings.

### `PATCH /stores/{storeId}/settings`

Update store settings such as default warehouse cost. This should create audit events because historical financial behavior depends on store defaults even when snapshots preserve applied values.[cite:88]

### `GET /stores/{storeId}/managers`

List store manager assignments.

### `POST /stores/{storeId}/managers`

Assign manager to store.

### `PATCH /stores/{storeId}/managers/{assignmentId}`

Deactivate or modify assignment.

## Accounting months

### `GET /months`

List accounting months.

Filters:
- store_id
- year
- month
- status_code

### `POST /months/provision`

Provision accounting month records for a store and date range. Renamed from `/months/generate` to emphasise that this is an explicit administrative action.

This is the **only** way an accounting month comes into existence. Order import, settlement import, and background jobs must never create one. An order dated outside any provisioned month is retained and raises `PERIOD_NOT_PROVISIONED` plus an exception for review (D-09, US-005 AC-05).

Validation:
- the store has a reporting timezone and currency configured,
- the range does not overlap existing months.

### `GET /months/{monthId}`

Get month detail, including checklist summary, exception counts, readiness, and close state.

### `POST /months/{monthId}/mark-ready`

Marks month as ready for review.

Validation:
- actor must be Owner, Admin, or Manager with store access,
- month must not already be closed.

Side effects:
- update readiness fields,
- create `month_actions` record,
- write audit event.[cite:685]

### `POST /months/{monthId}/close`

Closes the month.

Validation:
- actor must be Owner or Admin,[cite:685]
- month must not already be closed,
- required checklist items must be complete or waived,
- unresolved exceptions count should be accepted explicitly when non-zero,
- close note should be required.

Side effects:
- set month status to closed,
- set `has_ever_been_closed = true` permanently and increment `close_count`,
- insert a `month_close_snapshots` row at `snapshot_version = close_count`,
- write approval if approval model is used,
- create `month_actions` record,
- write audit event,
- activate period lock behavior for financial edits.[cite:685][cite:703][cite:712]

Idempotency: a retried close with the same `operation_id` returns the existing snapshot and creates no second close (US-023 AC-04).

Snapshot contents are governed by D-06. Until it closes, persist the raw included revisions and coverage rather than an approved totals shape.

### `POST /months/{monthId}/reopen`

Reopens a closed month.

Validation:
- actor must be Owner or Admin (BR-07),
- reopen note required,
- month must currently be closed.

Side effects:
- set month status to `reopened`,
- write `month_actions`,
- write audit event,
- possibly record approval action.

What reopening explicitly does **not** do:
- it does not reset `has_ever_been_closed`,
- it does not delete, alter, or hide any `month_close_snapshots` row,
- it does not grant Admin or Manager authority to edit historical financial records (US-024 AC-02).

A reclose creates `snapshot_version + 1`. Earlier versions remain readable and are marked `superseded_by_id`, never replaced (US-024 AC-04).

### `POST /months/{monthId}/historical-correction`

Owner only. Corrects a financial record in a month that has ever been closed, **without reopening it** (`close-v1`).

Request requires the affected records, the new values, the expected revisions, and a mandatory reason.

Validation:
- actor is Owner, otherwise `HISTORICAL_PERIOD_PROTECTED`,
- the affected periods are explicitly acknowledged in the request (US-025 AC-01).

Side effects, in one transaction:
- apply the correction,
- create `snapshot_version + 1` for each affected month,
- mark the prior snapshot superseded while leaving it fully readable,
- emit `historical_financial_correction_applied`.

The month's status does not change. Reopen is a separate workflow for substantial rework and still grants no historical edit authority.

### `GET /months/{monthId}/snapshots`

Lists every close snapshot for the month, newest first, including superseded versions.

### `GET /months/{monthId}/snapshots/{snapshotVersion}`

Returns one approved snapshot exactly as it was recorded. This endpoint is read-only with no mutation counterpart. Later live edits must never change its response (US-026 AC-03).

### `GET /months/{monthId}/checklist`

Get checklist results for the month.

### `PUT /months/{monthId}/checklist/{itemCode}`

Set checklist item status.

Validation:
- item exists,
- actor has access,
- if status is waived and item is required, note should be required.

### `GET /months/{monthId}/actions`

Returns month workflow history.

## Orders

### `GET /orders`

Primary order list endpoint.

Filters:
- store_id
- accounting_month_id
- status_code
- reconciliation_status
- assigned_user_id
- has_exception
- settlement_state
- order_date_from
- order_date_to
- search by walmart_order_id

This endpoint should support table views, export mode, and compact summary projections.

### `GET /orders/{orderId}`

Returns full order detail.

Required sections in the response:
- order summary, including the four state dimensions as four separate fields,
- assignments,
- current cost profile with per-field remaining edit allowance,
- estimate version history,
- settlement entry ledger,
- reconciliation summary and decision history,
- exceptions,
- audit history summary.

The response must **not** contain a field named `status`, `order_status`, or any equivalent that merges dimensions. The four dimensions are returned as:

```json
{
  "fulfillment": { "status_code": "delivered", "fulfilled_quantity": 2 },
  "refund_activity": { "entry_count": 1, "has_refunds": true },
  "reconciliation": { "status_code": "needs_review", "coverage_code": "partial" },
  "period_protection": { "accounting_month_id": "...", "status_code": "reopened", "has_ever_been_closed": true }
}
```

A delivered order with a refund and unresolved reconciliation must render all three truthfully and simultaneously (US-007 AC-02).

### `PATCH /orders/{orderId}/fulfillment`

Updates the **fulfillment dimension only**. Renamed from `PATCH /orders/{orderId}/status` to make the scope unmistakable.

Accepted `fulfillment_status_code` values are exactly `acknowledged`, `shipped_in_transit`, `delivered`, `cancelled`.

Validation:
- transition allowed,
- `settlement_pending`, `reconciled`, and `refunded` are **rejected** with `INVALID_DIMENSION_VALUE`; they belong to the reconciliation and settlement modules,
- period protection does not block a non-financial fulfillment update unless D-06 says otherwise.

Side effects:
- append `order_fulfillment_history`,
- update `orders.fulfillment_status_code`,
- write audit event.

There is no endpoint for setting a refund state. Refunds arrive as settlement evidence.

### `GET /orders/{orderId}/assignments`

List optional manager assignments.

### `POST /orders/{orderId}/assignments`

Assign manager to order.

### `PATCH /orders/{orderId}/assignments/{assignmentId}`

End assignment.

Because orders do not require assignment, these endpoints must never be assumed mandatory in business flows.[cite:684]

## Order costs

### `GET /orders/{orderId}/cost-profile`

Get current effective cost profile.

### `PUT /orders/{orderId}/cost-profile`

Create or replace current cost profile values.

Validation:
- financial edit allowed for actor and month,
- required manual fields validated,
- per-field edit limit logic invoked where values change.[cite:88]

Important note: although the endpoint is shaped as a replace/update, the implementation must compare incoming values field-by-field and route each changed field through edit-limit enforcement.

### `POST /orders/{orderId}/cost-fields/{fieldCode}/change`

Preferred explicit workflow endpoint for high-control systems.

Request body example:

```json
{
  "new_value": 12.50,
  "currency_code": "USD",
  "expected_record_revision": 7,
  "operation_id": "b1f0...",
  "reason": "Corrected supplier cost"
}
```

Validation, in order:
- field is editable,
- `expected_record_revision` matches, otherwise `STALE_REVISION`,
- the order's month has never been closed, or the actor is the Owner performing an authorized historical correction, otherwise `HISTORICAL_PERIOD_PROTECTED`,
- an edit allowance is **configured** for this field, otherwise `EDIT_POLICY_UNCONFIGURED`,
- remaining allowance is greater than zero, or an approved override applies, otherwise `EDIT_OVERRIDE_REQUIRED`.

Side effects:
- update `order_cost_profiles` and increment `record_revision`,
- append `order_cost_field_edits` with the `operation_id` and the policy version applied,
- supersede the current estimate version if the change affects estimate inputs,
- write audit event.

Allowance rules, implementing `cost-edit-v1`:

- The numeric limit is read from `cost_edit_policies` at request time. **It is not defined in this document or in application code.** Resolution order is store-specific row, then global row.
- If no policy row resolves, return `EDIT_POLICY_UNCONFIGURED` naming the field. Do not substitute a value, including the seeded one.
- `remaining = max(0, policy.max_edits - consumed_count)`, computed live. A policy change moves the remaining count immediately; it never rewrites history.
- The initial entry into a blank field does not consume. A correction or a clear-to-blank consumes one.
- An unchanged value is skipped entirely: no write, no history row, no consumption. Compare per field, not per payload.
- A rejected request consumes nothing.
- Counting and enforcement occur inside one serializable transaction, so two concurrent requests cannot both consume the last allowance (US-011 AC-03).
- A repeated `operation_id` returns the original result without consuming a second allowance (US-014 AC-03).
- Exhausting one field's allowance must not block edits to other fields (US-011 AC-02).

### `GET /orders/{orderId}/cost-fields/{fieldCode}/allowance`

Returns the server's view of the allowance so the UI never computes it (US-011 AC-01).

```json
{
  "field_code": "buying_cost_per_unit",
  "max_edits": 2,
  "consumed_count": 2,
  "remaining": 0,
  "locked": true,
  "lock_reason": "EDIT_OVERRIDE_REQUIRED",
  "policy_version": 1,
  "active_override_request_id": null
}
```

### `GET /orders/{orderId}/cost-field-history/{fieldCode}`

Returns edit history for one field.

## Edit overrides

### `POST /orders/{orderId}/edit-overrides`

Create override request for a field after edit limit is reached.

### `GET /edit-overrides`

List override requests.

Filters:
- status
- store_id
- requested_by

### `POST /edit-overrides/{overrideId}/approve`

Approve override.

Validation:
- actor must be Admin or Owner,
- request must be pending,
- if `requires_owner` is true because the order's month has ever been closed, an Admin approval is **rejected** with `OWNER_APPROVAL_REQUIRED` (BR-08, US-012 AC-03).

Side effects:
- approval log,
- audit event,
- override state change.[cite:88]

An approved override authorizes **one** change to **one** named field, by the requesting user, on the requested order. Specifically:

- It is single-use. It is consumed by the next successful edit of that field and the approval id is recorded on the resulting edit row.
- It **does not reset** `consumed_count`. The edit it authorizes still increments the counter, so a further change requires a further override.
- It does not unlock other fields on the same order.
- It remains valid until used, rejected, or the order's month is closed. There is no silent expiry timer.
- It never deletes or rewrites edit history (US-012 AC-04).

### `POST /edit-overrides/{overrideId}/reject`

Reject override.

## Estimates

### `GET /orders/{orderId}/estimates`

Returns estimate history.

### `POST /orders/{orderId}/estimates`

Creates a new estimate version instead of mutating old ones.

Validation:
- month financial edit allowed,
- monetary shape valid,
- source code valid.

Side effects:
- mark prior current estimate as not current,
- insert new version,
- recompute reconciliation summary if needed,
- write audit event.[cite:88][cite:723]

### `POST /orders/{orderId}/estimates/{estimateId}/set-current`

Optional recovery/admin endpoint if version control gets out of sync.

## Settlement imports and settlement entries

Settlement import is three phases per `settlement-v1`. Nothing is financially effective until commit.

### `POST /imports/settlements`

**Phase 1 and 2 — stage and validate.** Never commits.

Validation:
- the report type and version exist in `settlement_report_definitions`, otherwise `UNSUPPORTED_REPORT_VERSION`,
- a mapping exists for the report, otherwise `CATEGORY_MAPPING_MISSING`.

Side effects:
- create `import_batches`,
- create `raw_import_records`,
- normalize rows into `settlement_entries` with `commit_state_code = staged`,
- record `source_checksum` and `parser_version`,
- create `import_errors` for bad rows, each traceable to its source row,
- attempt deterministic order linkage, leaving uncertain rows unallocated.

Staged entries have **zero financial effect** and appear in no total.

### `POST /imports/settlements/{batchId}/commit`

**Phase 3 — commit.** An explicit operator action, Owner or Admin only.

Request:
```json
{ "accept_partial": true, "operation_id": "..." }
```

Validation:
- the batch is staged and not already committed,
- if any row failed validation, `accept_partial` must be explicitly true, otherwise `PARTIAL_COMMIT_NOT_ACCEPTED`.

Side effects:
- flip accepted entries to `committed`,
- record accepted, rejected, and staged counts on the batch,
- raise `late_activity_closed_period` exceptions for entries whose event date falls in a previously closed month, **without reopening it**,
- raise `duplicate_settlement_conflict` where a known reference arrives with different values,
- leave unmapped categories in `settled_other_amount` with a suspense flag and an exception,
- invalidate readiness for any affected month,
- write audit events.

A repeated `operation_id` or an identical `source_checksum` returns the original result and creates no second entry.

Nothing in this endpoint auto-allocates an uncertain entry or auto-confirms a reconciliation.

### `GET /imports/settlements/{batchId}`

Returns import summary.

### `GET /settlement-batches`

List settlement batches.

### `GET /settlement-batches/{batchId}`

Returns batch plus linked entries.

### `GET /settlement-entries`

Search settlement entries.

Filters:
- store_id
- order_id
- settlement_batch_id
- event_month
- entry_type
- unmatched_only

### `POST /settlement-entries/{entryId}/link-order`

Manually links a settlement entry to an order.

Validation:
- actor has reconciliation permissions,
- linkage is valid and not conflicting.

Because one order may have multiple settlement entries over time, linking should be many-to-one capable rather than exclusive.[cite:88][web:738]

### `POST /settlement-entries/{entryId}/unlink-order`

Manual unlink action with reason and audit log.

## Reconciliation

### `GET /reconciliation/orders`

Returns reconciliation queue.

Filters:
- store_id
- status_code
- has_exception
- month_id
- manager_id optional

### `GET /orders/{orderId}/reconciliation`

Returns current reconciliation summary, estimate reference, settlement totals, and exceptions.

### `POST /orders/{orderId}/reconciliation/review`

Runs or records human review.

Request fields may include:
- target status,
- note,
- accepted variance,
- exception decisions.

Validation:
- actor authorized,
- estimates and settlement context exist according to target status.

Side effects:
- update reconciliation summary,
- create or resolve exceptions,
- write audit event.[cite:708][cite:712]

### `POST /orders/{orderId}/reconciliation/mark-reconciled`

Explicit finalize action. Owner or Admin only.[cite:686]

Request requires a declared `coverage_code`, because coverage is a human declaration and is never inferred (`settlement-v1`).

Validation:
- no hard blocker is outstanding — unallocated entries, unresolved duplicate conflicts, currency mismatch, or an unpostable order all refuse the request with `HARD_BLOCKER_OUTSTANDING`,
- when `decision_basis_code = manual_fallback`, a reason is mandatory.

A manual fallback is recorded as a judgment. It creates **no synthetic settlement entries** and does not alter coverage facts (US-019 AC-04).

No variance classification, of any size, permits this endpoint to be called automatically.

### `POST /orders/{orderId}/refund-recoveries`

Records how much buying cost was recovered against a refund entry, per `refund-v1`.

Request:
```json
{
  "settlement_entry_id": "...",
  "recovered_amount": "14.20",
  "is_known_zero": false,
  "currency_code": "USD",
  "recovered_quantity": 1,
  "reason": "Returned saleable"
}
```

Validation:
- the referenced entry is a committed refund on this order,
- recovery does not exceed the buying cost for the refunded quantity,
- currency matches the order,
- this is a **financial write**: in a previously closed month it is Owner-only (BR-08).

Until a recovery exists, the order's settled profit is `incomplete` with reason `refund_recovery_unknown`. It is never computed as though recovery were zero.

### `POST /orders/{orderId}/reconciliation/exceptions`

Create exception.

### `PATCH /reconciliation/exceptions/{exceptionId}`

Update exception state.

## Imports for orders

### `POST /imports/orders`

Starts order import.

Side effects:
- create import batch,
- validate rows,
- upsert orders by `(store_id, walmart_order_id)`,
- resolve `order_date` from `order_placed_at_utc` through the store timezone,
- assign or re-evaluate the posting period under `period-v1`,
- append `order_posting_history`,
- create import errors and exceptions if needed.

#### Posting resolution, per `period-v1`

On **first import** of an order:

| Situation | Result |
|---|---|
| Month containing `order_date` exists and is open | Post there, `posting_reason_code = natural` |
| Month exists but has ever been closed | Post to the **earliest currently open month**, `posting_reason_code = deferred_closed_period`, raise a deferral exception |
| Month does not exist | Retain order, `posting_status_code = unpostable_no_period`, return `PERIOD_NOT_PROVISIONED` |
| No month is open at all | Retain order, `posting_status_code = unpostable_no_open_period`, return `NO_OPEN_PERIOD` |

On **re-import** where the source corrects the date:

- `order_date` updates **unconditionally** and emits `order_date_changed`.
- If the current posting month and the target month are both open, re-post and emit `order_reposted`.
- If either has ever been closed, **the posting period does not move**. Raise an exception for Owner review and emit no re-post.

Posting forward never writes to the closed month, never alters its snapshot, and never triggers a reopen.

### `POST /orders/{orderId}/repost`

Owner-only. Moves an order's posting period when automatic rules declined to.

Validation:
- actor must be Owner when either the current or target month has ever been closed (BR-08),
- target month exists and is open,
- note required.

Side effects:
- update `orders.accounting_month_id`, set `posting_reason_code = owner_reposted`,
- append `order_posting_history`,
- write audit event.

This is the only endpoint that may move an order into or out of a protected period.

### `GET /imports/orders/{batchId}`

Returns import result summary.

## Reporting

Every reporting response must declare `store_id`, `currency_code`, `date_basis`, `value_basis`, `completeness`, and `calculation_policy_version`. A report that returns bare numbers is non-conforming (US-026 AC-02).

Profit reporting is unblocked under `profit-v1`. Two rules apply to aggregates:

- Orders with `incomplete` figures are **excluded from totals** and reported as a separate excluded count. An unknown component never enters a sum as zero.
- Results are grouped by `currency_code` and never summed across currencies.

A report answers one basis. A request that attempts to combine order-cohort and settlement-event figures into a single total returns `BASIS_COMBINATION_REFUSED`, because that is how refund double counting is prevented (`refund-v1`).

### `GET /reports/store-month`

Query parameters:
- store_id
- year
- month
- `date_basis`: `order_month`, `posting_month`, or `snapshot` — **required, no default**
- `value_basis`: `estimated` or `settled`
- `snapshot_version` (required when `date_basis=snapshot`)

`order_month` groups by `orders.order_date` and includes orders that were posted forward. `posting_month` groups by `orders.accounting_month_id` and is what a close snapshot was built from. They differ only by deferred orders.

Omitting `date_basis` is an error, not a default. The two bases answer different questions and the caller must say which one they want.

`snapshot` reads `month_close_snapshots` and is unaffected by later live edits.

### `GET /reports/deferred-orders`

Orders whose posting period differs from their true order month, with both months, the reason code, and the amount involved. This is the reconciliation between the order-month and posting-month bases (US-026 AC-05).

### `GET /reports/unpostable-orders`

Orders retained but assigned to no period, with the blocking reason. These are excluded from every monthly total until resolved.

### `GET /reports/refund-month-effects`

Groups by settlement event month, never by original order month. Each row links back to its originating order and order month (US-027 AC-01).

Combined order-cohort and refund-activity totals return `BASIS_COMBINATION_REFUSED`. A refund is reportable under either basis, never under their sum.

### `GET /reports/reconciliation-exceptions`

### `GET /reports/orders-missing-costs`

### `GET /reports/closed-month-changes`

This report should be backed by audit events and explicit closed-period change detection.

### `GET /exports/orders.csv`

### `GET /exports/reconciliation.csv`

### `GET /exports/month-close-package`

## Service design details

## AuthService

Responsibilities:
- user identity resolution,
- login, MFA challenge and verification, logout,
- password hashing, change, and reset,
- rate limiting, lockout, and session lifecycle,
- permission context materialization.

Should not contain business-domain authorization rules beyond identity basics. Must never return `password_hash` or `mfa_secret` to any caller, and must exclude them at the repository layer rather than stripping them per response.

## UserAccessService

Responsibilities:
- role resolution,
- store access rules,
- permission predicates.

Recommended methods:
- `canManageStore(user, storeId)`
- `canEditProtectedHistoricalFinancials(user, accountingMonth)` — Owner only, evaluated against `has_ever_been_closed`
- `canCloseMonth(user, storeId)`
- `canReopenMonth(user, storeId)` — Owner and Admin, per BR-07
- `canApproveOverride(user, overrideRequest)` — denies Admin when `requires_owner` is true
- `canConfirmReconciliation(user, storeId)`
- `canResolveException(user, exception)` — class-aware, denies Managers on financial codes
- `storeIdsInScope(user)` — Owner gets all; Admin and Manager get assignments only

This service is the only place role comparisons appear. Every method resolves against the capability matrix in `walmart-user-stories.md`; a capability marked `Gated D-01` returns false.

## StoreService

Responsibilities:
- create/update stores,
- update settings,
- assign store managers.

Critical rule:
- settings changes affecting financial defaults must be audited.[cite:88]

## AccountingMonthService

Responsibilities:
- generate or load month,
- mark ready,
- close,
- reopen,
- compute readiness summary.

Recommended methods:
- `markReady(monthId, actor, note)`
- `closeMonth(monthId, actor, closeRequest)`
- `reopenMonth(monthId, actor, note)`
- `validateMonthClose(monthId)`

`closeMonth` should run in one transaction because checklist validation, action logging, approval logging, and state locking must remain consistent.[cite:703][cite:712]

## ChecklistService

Responsibilities:
- list definitions,
- update checklist item result,
- compute required-item completion state.

Do not hardcode checklist items in controller code. Use definitions from the database so the business process can evolve.[cite:704][cite:711]

## OrderService

Responsibilities:
- order queries,
- status transition validation,
- optional assignment handling,
- order detail aggregation.

Should not own cost edits, estimates, settlement ingestion, or reconciliation rules.

## OrderCostService

Responsibilities:
- current cost profile updates,
- field-level edit counting,
- override requirement checks,
- cost change audit events.

Recommended methods:
- `changeField(orderId, fieldCode, newValue, actor, reason, operationId)`
- `getAllowance(orderId, fieldCode)` — returns max, consumed, remaining, lock reason
- `requiresOverride(orderId, fieldCode)`
- `applyApprovedOverride(...)`

This service is critical because the edit limit is per field, not per order.[cite:88] It is also the only place `cost_edit_policies` is read; no other service or UI component may compute an allowance.

## CalculationService

Responsibilities:
- the single implementation of `profit-v1`,
- estimated and settled profit, total cost, ROI, and variance,
- completeness evaluation and missing-component reporting,
- decimal precision and the final half-up rounding.

Recommended methods:
- `calculateEstimated(orderId)`
- `calculateSettled(orderId)`
- `calculateVariance(orderId)`
- `evaluateCompleteness(costProfile, estimateVersion)`

No other service, view, trigger, or frontend component may implement a profit calculation. The frontend may show an advisory preview, clearly labeled, but the server's figure is authoritative. Two implementations will diverge, and the `calculation_policy_version` stamp would then be false.

## EstimateService

Responsibilities:
- create estimate versions,
- set current version,
- compute estimate-derived profit snapshots.

Recommended methods:
- `createEstimateVersion(orderId, payload, actor)`
- `getCurrentEstimate(orderId)`
- `recalculateEstimateProfit(orderId)`

Old estimate versions must remain queryable because estimated and settled values must remain visible forever.[cite:88]

## SettlementImportService

Responsibilities:
- normalize imported settlement data,
- create settlement batches,
- create settlement entries,
- try deterministic linking to orders,
- flag unmatched rows.

Recommended methods:
- `importSettlementBatch(input, actor)`
- `normalizeSettlementRow(rawRow)`
- `linkSettlementEntries(batchId)`

Because one order may have many settlement entries, the importer must never assume a one-row final settlement model.[cite:88][web:738]

## ReconciliationService

Responsibilities:
- aggregate estimate and settlement context,
- calculate variance,
- manage reconciliation summary,
- create and resolve exceptions,
- finalize reconciled state when allowed.

Recommended methods:
- `buildOrderReconciliation(orderId)`
- `reviewOrder(orderId, actor, payload)`
- `markReconciled(orderId, actor, note)`
- `openException(orderId, payload)`
- `resolveException(exceptionId, actor, resolution)`

This service should own variance math and reconciliation statuses so the UI does not invent them independently.[cite:708][cite:712]

## ImportService

Responsibilities:
- generic import batch lifecycle,
- error recording,
- raw staging persistence.

Useful if order imports and settlement imports share infrastructure.

## ReportingService

Responsibilities:
- reporting queries,
- export generation,
- projection-specific aggregation.

Important rule:
- reports grouped by order month and reports grouped by refund/financial event month must stay distinct.[cite:88]

## AuditService

Responsibilities:
- write audit events,
- write approval records,
- fetch trace history.

Audit write methods should be reusable from all business services rather than having every service construct audit payloads differently.[cite:718][cite:567]

## Validation rules by business area

## Order costs

- `buying_cost_per_unit` required for an order to be cost-complete; absent means unknown, never zero,
- an explicit zero is accepted and stored distinguishably from blank,
- quantity must be positive,
- warehouse cost applies once per order regardless of quantity,
- warehouse override must be non-negative unless an explicit negative adjustment policy exists,
- a change beyond the configured per-field allowance requires an approved override. **The allowance value is read from policy, never written into code or this document.**

## Estimates

- estimate version payload must preserve monetary precision,
- `currency_code` required,
- source code must be known,
- current version uniqueness must be protected,
- profit and ROI are computed under `profit-v1` and **omitted with a completeness reason** whenever any of the six cost components is unknown. They are never returned as zero.

## Financial calculation

Every service producing a financial figure implements `profit-v1` exactly once, in the domain layer, and observes:

- six cost components, with the Walmart label and third-party label costs **added together**, not treated as alternatives,
- no sales tax term; the imported selling amount excludes buyer tax,
- exact decimal arithmetic, with a single half-up rounding to 2 decimal places applied only to the final result,
- ROI derived from unrounded profit over unrounded total cost, and returned as `"Unavailable"` when total cost is zero,
- unknown inputs produce an `incomplete` result naming the missing components, never a substituted zero,
- settled figures aggregate only committed, allocated settlement entries,
- `calculation_policy_version` returned alongside every figure.

Responses carry the basis explicitly:

```json
{
  "estimated": { "profit": "41.18", "roi": "0.2246", "completeness": "complete" },
  "settled":   { "profit": null, "completeness": "incomplete",
                 "coverage_code": "partial",
                 "label": "settled_to_date",
                 "incomplete_reason_codes": ["refund_recovery_unknown"] },
  "variance":  null,
  "variance_classification": "unclassified",
  "currency_code": "USD",
  "calculation_policy_version": "profit-v1"
}
```

`incomplete_reason_codes` names exactly what is missing, so the UI can tell a user which input to supply rather than showing a blank. `refund_recovery_unknown` means a refund exists whose product-cost recovery has not been recorded; per `refund-v1` the figure is withheld rather than computed as if recovery were zero.

## Settlements

- batch id uniqueness is per store, not global,
- entries may be negative for refunds or adjustments,
- `currency_code` is required per entry and never defaulted,
- a linked entry's batch store must match the order's store,
- entries without order linkage are accepted and held unallocated for later review; unallocated entries are excluded from order profit,[web:736][web:740]
- a source correction inserts a superseding entry rather than editing the original.

## Month close

- closed month requires a manual actor action,
- required checklist items complete or waived,
- close note required,
- reopen note required,
- a snapshot is written in the same transaction as the close,
- historical-edit protection follows `has_ever_been_closed`, not current status.[cite:685][cite:704][cite:712]

## Currency

- every monetary request and response field carries a `currency_code`,
- an amount whose currency cannot be determined is rejected, not assumed,
- no endpoint returns a total that sums across currencies; multi-currency results are returned grouped by currency (BR-12, US-026 AC-04),
- no endpoint accepts or returns an FX rate or a converted amount. Conversion does not exist in this release.

## Dates and periods

- every date field in a response states whether it is a local calendar date or a UTC instant,
- `order_date` is a local calendar date in the store's timezone; `order_placed_at_utc` is the source instant,
- an order response always includes both its posting period and its true order month when they differ,
- a store without a configured timezone cannot resolve dates and returns `STORE_TIMEZONE_UNCONFIGURED`,
- changing a store timezone is prospective; no endpoint recomputes historical `order_date` values.

## Error codes

Stable codes are part of the contract.

| Code | Meaning |
|---|---|
| `MONTH_CLOSED` | The month is currently closed; workflow action unavailable |
| `STORE_OUT_OF_SCOPE` | The store is not assigned to the actor. Must be indistinguishable from a non-existent store |
| `HISTORICAL_PERIOD_PROTECTED` | The month has ever been closed; financial edit is Owner-only |
| `OWNER_APPROVAL_REQUIRED` | Admin cannot approve this override because the period is protected |
| `EDIT_OVERRIDE_REQUIRED` | Per-field allowance exhausted |
| `EDIT_POLICY_UNCONFIGURED` | No `cost_edit_policies` row resolves for this field. A configuration error, not a fallback trigger |
| `CALCULATION_INCOMPLETE` | One or more cost components unknown; the response names them |
| `CONFIGURATION_MISSING` | A required configuration value is unpopulated; the item id, such as `C-01`, is in the detail |
| `BASIS_COMBINATION_REFUSED` | A request tried to sum figures from two different reporting bases |
| `STALE_REVISION` | Optimistic concurrency conflict |
| `INVALID_DIMENSION_VALUE` | A value from one state dimension was submitted to another |
| `CURRENCY_REQUIRED` | Monetary value supplied without a currency |
| `CURRENCY_MISMATCH` | Operation would combine differing currencies |
| `PERIOD_NOT_PROVISIONED` | Order date falls outside any provisioned accounting month |
| `NO_OPEN_PERIOD` | The order's month is closed and no open month exists to post it forward into |
| `POSTING_PERIOD_PROTECTED` | A re-post would move an order into or out of a previously closed period; Owner action required |
| `STORE_TIMEZONE_UNCONFIGURED` | The store has no IANA timezone, so no date can be resolved |
| `UNSUPPORTED_REPORT_VERSION` | No `settlement_report_definitions` row matches; columns are never guessed |
| `CATEGORY_MAPPING_MISSING` | No mapping configured for this report type (C-01) |
| `PARTIAL_COMMIT_NOT_ACCEPTED` | Rows failed validation and the operator did not explicitly accept a partial commit |
| `HARD_BLOCKER_OUTSTANDING` | An unwaivable blocker prevents this decision |
| `MFA_REQUIRED` | Owner and Admin cannot hold a session without TOTP |
| `EXCEPTION_CLASS_FORBIDDEN` | A Manager attempted to resolve a financial exception |
| `CROSS_STORE_REFERENCE` | Attempted link between records in different stores |

All business decisions are now closed, so no endpoint should ever be blocked by an unmade decision. `CONFIGURATION_MISSING` covers the remaining case: a value that exists in the design but has not been populated, such as a settlement category mapping or a store's timezone.

Returning a plausible default instead of one of these errors is the single most damaging thing an implementer can do to this project. An unconfigured system must refuse, not guess.

## Audit events to emit

Sensitive actions should emit audit events consistently.

| Action | Event type |
|---|---|
| Store setting changed | `store_settings_updated` |
| Cost edit policy changed | `cost_edit_policy_updated` |
| Month provisioned | `accounting_month_provisioned` |
| Order date corrected by source | `order_date_changed` |
| Order posted to a period | `order_posted` |
| Order re-posted between periods | `order_reposted` |
| Order posted forward from a closed period | `order_posting_deferred` |
| Store timezone changed | `store_timezone_changed` |
| Sign-in succeeded or failed | `auth_sign_in_succeeded`, `auth_sign_in_failed` |
| Account locked or unlocked | `auth_account_locked`, `auth_account_unlocked` |
| Password changed or reset | `auth_password_changed` |
| MFA enrolled or reset | `auth_mfa_changed` |
| Store assignment granted or revoked | `store_assignment_changed` |
| Settlement batch committed | `settlement_batch_committed` |
| Refund cost recovery recorded | `refund_cost_recovery_recorded` |
| Category mapping configured | `settlement_category_mapping_changed` |
| Month marked ready | `accounting_month_marked_ready` |
| Month readiness invalidated | `accounting_month_readiness_invalidated` |
| Month closed | `accounting_month_closed` |
| Close snapshot created | `month_close_snapshot_created` |
| Month reopened | `accounting_month_reopened` |
| Historical financial correction | `historical_financial_correction_applied` |
| Order fulfillment changed | `order_fulfillment_changed` |
| Cost field changed | `order_cost_field_changed` |
| Override requested | `edit_override_requested` |
| Override approved | `edit_override_approved` |
| Estimate version created | `order_estimate_version_created` |
| Settlement batch imported | `settlement_batch_imported` |
| Settlement entry linked | `settlement_entry_linked` |
| Reconciliation reviewed | `order_reconciliation_reviewed` |
| Exception opened | `reconciliation_exception_opened` |
| Exception resolved | `reconciliation_exception_resolved` |

## Transaction boundaries

The following actions should run in database transactions:

- close month, including snapshot creation and the `has_ever_been_closed` flag,
- reopen month,
- historical correction, including the new snapshot version and supersession,
- settlement batch commit, including exception generation and readiness invalidation,
- cost field change, including allowance counting and override consumption, at serializable isolation,
- estimate version creation plus current version swap,
- settlement import batch commit,
- reconciliation review when updating summary, decision history, and exceptions together.

This reduces the chance of broken mid-state records in sensitive financial workflows.[cite:718][cite:212]

Every one of these actions accepts an `operation_id` and is idempotent on retry. A timeout with an unknown outcome must be resolvable by replaying the same `operation_id` without duplicating a record or a counter (US-014 AC-03, US-016 AC-01, US-023 AC-04).

## Anti-patterns to avoid

Do not let AI generate these patterns:

- One generic `PATCH /orders/{id}` that edits status, costs, estimates, reconciliation, and settlement flags all at once.
- A single `status` field in any request or response that merges fulfillment, refund, reconciliation, or period state.
- One `walmart_fee` column that gets overwritten from estimate to settled state.
- UI-only permission checks with no backend enforcement.
- A closed-period check written against current status instead of `has_ever_been_closed`.
- Month close implemented as a simple boolean flag change with no checklist, note, snapshot, or audit event.
- Settlement import that assumes one final settlement row per order.
- Refund reporting that only follows original order month instead of refund event month.[cite:88][cite:685][web:738]
- A hardcoded edit limit, tolerance, rounding rule, or profit formula standing in for an unresolved decision gate.
- A monetary field without a currency, or a total summed across currencies.
- Auto-creating an accounting month during import to avoid an inconvenient error.
- Deriving `accounting_month_id` from `order_date` in a trigger or generated column, which silently destroys deferred postings.
- A report endpoint that defaults `date_basis` instead of requiring it.
- Reopening a closed month to accommodate a late order, when posting it forward is the designed answer.
- Reopening a closed month to perform a historical correction, when in-place correction with a new snapshot version is the designed answer.
- Auto-reconciling anything because the variance looked small.
- Committing a settlement batch as a side effect of uploading it.
- Treating an unmapped settlement category as a fee, a label, or zero.
- Computing settled profit as though an unrecorded refund recovery were zero.
- Returning `STORE_OUT_OF_SCOPE` in a way that confirms the store exists.
- Any endpoint, service, or table for manager compensation (BR-09).

## Suggested implementation order

1. Auth and access policies
2. Store and settings endpoints
3. Accounting month endpoints
4. Order list and detail endpoints
5. Order cost workflows
6. Estimate workflows
7. Order import and settlement import endpoints
8. Reconciliation workflows
9. Reporting and exports
10. Audit query endpoints

This order lets the frontend and service tests grow on stable business flows rather than trying to integrate everything at once.[cite:747][cite:88]

## Final recommendation

The API should be treated as a **workflow API**, not just a CRUD API. That is the main safeguard against repeating the original design mistake. If business actions stay explicit at the service layer, the modular schema will remain usable, updates will be safer, and AI-generated code will have much less room to blur operational data, financial history, and accounting controls into one messy object model.[cite:88][cite:685][cite:718]
