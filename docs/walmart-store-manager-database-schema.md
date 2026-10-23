# Walmart Store Manager Web App Database Schema

**Version:** 1.0  
**Date:** 23 October 2026  
**Status:** Proposed persistence model. Subordinate to `walmart-user-stories.md`.

## Authority

`walmart-user-stories.md` is authoritative for business rules, scope, the capability matrix, and state vocabulary. Where this document and that one disagree, that one wins and this document is the defect.

This schema deliberately does **not** restate the permission matrix or the state vocabulary. It implements them.

Binding inputs from the authoritative document:

| Rule | Effect on this schema |
|---|---|
| BR-02 — one SKU per order, no line items | `orders` carries `sku`, `quantity`, and a single selling amount. **No `order_items` table exists or may be added.** |
| BR-06 + `cost-edit-v1` — edit limits are configurable, now configured | No numeric limit appears in any column default or check constraint. Limits live in `cost_edit_policies`, seeded once with the approved configuration. |
| `profit-v1` — approved calculation policy | Six cost components, additive labels, no tax term, half-up rounding at the final step only. Profit columns are populated and stamped with `calculation_policy_version`. |
| BR-07 — Owner and Admin may close and reopen | `month_actions` accepts both roles for `close` and `reopen`; enforcement is in the service layer. |
| BR-08 — only Owner edits previously closed months | `accounting_months.has_ever_been_closed` makes protection a durable fact, not a current-status lookup. |
| BR-09 — compensation out of scope | No compensation, payment, or payroll table exists or may be added. |
| BR-10 interpretation — four independent state dimensions | Fulfillment, refund activity, reconciliation, and period protection are stored in four separate places. No combined status column exists. |
| BR-12 — per-store reporting currency | Every monetary amount is stored alongside a currency code. No FX rate table exists; no conversion is implemented. |
| BR-13 + `period-v1` — transaction date and posting period are separate | `orders.order_date` and `orders.accounting_month_id` are independent columns. `accounting_month_id` is **never** a derived or generated column. |

## Goal

This schema is designed to be **modular, changeable, and version-friendly**. The main principle is to separate business domains so that future changes in reconciliation, reporting, permissions, imports, or UI do not force destructive rewrites across the whole database.[cite:88][cite:685][cite:747]

The schema intentionally avoids one giant `orders` table with many overloaded columns. Instead, it uses bounded modules for identity, stores, orders, operational costs, financial estimates, settlement entries, reconciliation, accounting periods, and audit history. That separation matches the confirmed business rules and also fits good close-control and audit-trail practice, where ownership, evidence, exception handling, and period locks must stay explicit.[cite:685][cite:88][cite:703][cite:718]

## Design principles

### Modular structure

The database should be organized so each major concern can evolve mostly independently:

- identity and access,
- store configuration,
- imported order facts,
- manual operational enrichment,
- estimated financial values,
- settlement events,
- reconciliation review,
- accounting month control,
- audit and approvals.[cite:88][cite:685]

### Append-over-overwrite

Sensitive financial history should prefer new records or versioned records instead of destructive overwrites. This matters because estimated and settled values must remain visible forever, and later refunds or fee corrections may arrive after the first settlement cycle.[cite:88][web:738][web:740]

### Explicit workflow tables

Close, reopen, override, and reconciliation actions should be represented explicitly rather than hidden in generic update timestamps. Close-control guidance emphasizes named owners, evidence, exceptions, sign-off, and period locking instead of implied process state.[cite:704][cite:711][cite:712]

### Extensible enumerations

Statuses and types should be implemented with either stable code tables or well-scoped enums so business wording can change without requiring dangerous schema surgery.

## Recommended module layout

| Module | Tables | Purpose |
|---|---|---|
| Identity | `users`, `roles`, `user_role_assignments` | Authentication and authorization |
| Stores | `stores`, `store_settings`, `store_manager_assignments`, `cost_edit_policies` | Store identity, defaults, and editable policy |
| Accounting periods | `accounting_months`, `month_checklist_definitions`, `month_checklist_results`, `month_actions`, `month_close_snapshots` | Month readiness, close, reopen, protected results |
| Orders | `orders`, `order_fulfillment_history`, `order_assignments` | Imported Walmart order facts and fulfillment state |
| Operational costs | `order_cost_profiles`, `order_cost_field_edits`, `edit_override_requests` | Manual cost data and edit control |
| Financial estimates | `order_estimate_versions` | Pre-settlement values and profit snapshots |
| Settlements | `settlement_batches`, `settlement_entries`, `settlement_entry_types` | Imported payout, refund, and adjustment events |
| Reconciliation | `order_reconciliations`, `reconciliation_exceptions` | Human review and exception handling |
| Audit and approvals | `approvals`, `audit_events` | Traceability and control evidence |
| Imports | `import_batches`, `import_errors`, `raw_import_records` optional | Import traceability and recovery |

### Tables that must never exist

| Forbidden | Reason |
|---|---|
| `order_items`, `order_lines` | BR-02: one SKU per order |
| any compensation / manager payment / payroll table | BR-09: out of scope |
| any column named `order_status` or equivalent combined status | BR-10 interpretation: four independent dimensions |
| any column holding both an estimated and a settled value over its lifetime | BR-03, BR-05 |

## Schema strategy

A relational database such as PostgreSQL is a strong fit because the domain includes clear entities, cross-table constraints, period controls, auditability, and reporting joins. Audit-trail and reconciliation use cases generally benefit from explicit transactional consistency and relational linkage.[cite:718][cite:212]

The recommended strategy is:

- core business facts in normalized tables,
- selective snapshot values where historical preservation matters,
- append-only audit/event logs for sensitive changes,
- reporting views or materialized views for dashboards.

## Core schema

## Identity module

### `users`

Stores application users.

| Column | Type | Notes |
|---|---|---|
| id | uuid pk | Primary key |
| email | citext unique | Login identity |
| full_name | text | Display name |
| password_hash | text | Argon2id, or bcrypt cost 12+. Never returned by any query used for display |
| password_updated_at | timestamptz | Drives session invalidation on change |
| mfa_secret | text nullable | TOTP secret, encrypted at rest |
| mfa_enabled | boolean default false | **Required true for Owner and Admin** (`access-v1`) |
| failed_sign_in_count | integer default 0 | Lockout backoff |
| locked_until | timestamptz nullable | Temporary lockout |
| last_sign_in_at | timestamptz nullable | Audit |
| is_active | boolean | Soft-disable support |
| created_at | timestamptz | Audit |
| updated_at | timestamptz | Audit |

`password_hash` and `mfa_secret` must never appear in an audit payload, an export, a log line, or an API response. Exclude them at the repository layer, not by remembering to strip them at the controller.

### `user_sessions`

Server-side sessions, because `access-v1` requires sign-out and password change to invalidate access immediately.

| Column | Type | Notes |
|---|---|---|
| id | uuid pk | Primary key |
| user_id | uuid fk -> users.id | Owner of the session |
| token_hash | text unique | Hash of the session token; never the token itself |
| issued_at | timestamptz | Audit |
| last_seen_at | timestamptz | Drives the 30 minute idle timeout |
| expires_at | timestamptz | Absolute 12 hour timeout |
| revoked_at | timestamptz nullable | Set on sign-out and on password change |
| ip_address | text nullable | Audit |
| user_agent | text nullable | Audit |

Timeout values are configuration, not constants in this table.

### `password_reset_tokens`

| Column | Type | Notes |
|---|---|---|
| id | uuid pk | Primary key |
| user_id | uuid fk -> users.id | Target |
| token_hash | text unique | Hash only |
| expires_at | timestamptz | Short-lived |
| used_at | timestamptz nullable | Single use |
| created_at | timestamptz | Audit |

Reset responses must not reveal whether an account exists.

### `roles`

Keeps roles data-driven and extensible.

| Column | Type | Notes |
|---|---|---|
| id | uuid pk | Primary key |
| code | text unique | Exactly `owner`, `admin`, `manager`. No `viewer` role is approved for this release |
| label | text | Human-readable |
| is_system | boolean | Prevent accidental deletion |

### `user_role_assignments`

Supports future multi-role expansion without changing `users` directly.

| Column | Type | Notes |
|---|---|---|
| id | uuid pk | Primary key |
| user_id | uuid fk -> users.id | User |
| role_id | uuid fk -> roles.id | Role |
| assigned_at | timestamptz | Audit |
| assigned_by | uuid fk -> users.id nullable | Actor |
| revoked_at | timestamptz nullable | Optional |

This is more modular than a single role column because permission models often evolve after v1.

## Stores module

### `stores`

Represents business stores/accounts.

| Column | Type | Notes |
|---|---|---|
| id | uuid pk | Primary key |
| name | text | Store name |
| walmart_store_key | text unique | External store identifier |
| timezone | text | IANA identifier such as `America/New_York`. Required before financial reporting; never defaulted. Changes apply prospectively only (`period-v1`) |
| reporting_currency_code | char(3) | ISO 4217. Required before financial reporting is activated (BR-12, US-003 AC-02) |
| managed_from_year | integer | Scope boundary. Orders before it are out of scope (BR-14) |
| managed_from_month | integer | 1-12. Defaults to the month the store was added; Owner may change it, audited |
| is_active | boolean | Active flag |
| created_at | timestamptz | Audit |
| updated_at | timestamptz | Audit |

`reporting_currency_code` is nullable only at creation time. Activating financial reporting for a store requires it to be set, and US-003 AC-02 forbids guessing it.

### `store_settings`

Separates changing operational settings from store identity. Settings are **versioned and effective-dated** because US-030 AC-02/AC-03 require policy changes to apply prospectively without recalculating history.

| Column | Type | Notes |
|---|---|---|
| id | uuid pk | Primary key |
| store_id | uuid fk -> stores.id | Store |
| default_warehouse_cost | numeric(12,2) | Store-level default |
| default_warehouse_cost_currency | char(3) | Currency of the default |
| settings_version | integer | Monotonic per store |
| effective_from | timestamptz | When this version begins applying |
| effective_to | timestamptz nullable | Null for the current version |
| updated_by | uuid fk -> users.id | Actor |
| created_at | timestamptz | Audit |

Recommended constraints:
- unique `(store_id, settings_version)`
- one current row per store, enforced by partial unique index where `effective_to is null`

Rows are **inserted, never updated**. Superseding a version sets `effective_to` on the old row and inserts a new one. This is what makes `warehouse_cost_default_snapshot` on an order auditable back to a specific policy version.

This table stays separate from `stores` so future settings growth does not bloat store identity.

### `cost_edit_policies`

Holds the per-field manual cost edit allowance. Implements `cost-edit-v1` from the authoritative document.

| Column | Type | Notes |
|---|---|---|
| id | uuid pk | Primary key |
| store_id | uuid fk -> stores.id nullable | Null means global default policy |
| field_code | text | `buying_cost_per_unit`, `warehouse_cost_override`, `third_party_label_cost`, `other_cost`, `estimated_walmart_fee`, `estimated_walmart_label_cost` |
| max_edits | integer | Corrections permitted per field. **No column default** |
| counts_first_save | boolean | Whether the initial save consumes an allowance |
| counts_no_op_save | boolean | Whether an unchanged submission consumes an allowance |
| policy_version | integer | Monotonic |
| effective_from | timestamptz | Prospective application |
| effective_to | timestamptz nullable | Null for current |
| updated_by | uuid fk -> users.id | Actor |
| created_at | timestamptz | Audit |

**Approved initial seed — `cost-edit-v1`.** One global row per field code, `store_id = null`, `policy_version = 1`:

| field_code | max_edits | counts_first_save | counts_no_op_save |
|---|---:|:--:|:--:|
| `buying_cost_per_unit` | 2 | false | false |
| `warehouse_cost_override` | 2 | false | false |
| `third_party_label_cost` | 2 | false | false |
| `other_cost` | 2 | false | false |
| `estimated_walmart_fee` | 2 | false | false |
| `estimated_walmart_label_cost` | 2 | false | false |

Hard rules:

- The value `2` appears **in this seed only**. It must not become a column default, a check constraint, a constant, or a fallback in application code.
- Resolution order is store-specific row, then global row. A missing row for a field is a configuration error returning `EDIT_POLICY_UNCONFIGURED`, never a fallback to the seed values.
- Policy rows are **insert-only**. Superseding sets `effective_to` and inserts a new `policy_version`.
- Remaining allowance is computed live as `max(0, current_policy.max_edits - consumed_count)`. Raising `max_edits` unlocks previously locked fields; lowering it locks fields already over the new value.
- Changing a policy version never rewrites `order_cost_field_edits` and never recalculates historical financial figures (US-030 AC-03).

### `store_manager_assignments`

Supports multiple managers per store while keeping orders optionally unassigned.[cite:684]

| Column | Type | Notes |
|---|---|---|
| id | uuid pk | Primary key |
| store_id | uuid fk -> stores.id | Store |
| user_id | uuid fk -> users.id | Manager **or Admin** |
| assignment_kind | text | Optional categorization |
| active_from | date | Start |
| active_to | date nullable | End |
| created_by | uuid fk -> users.id | Owner only (`access-v1`) |
| created_at | timestamptz | Audit |

Under `access-v1` this table scopes **both** Managers and Admins. Only the Owner has implicit access to every store; an Admin without an active row here has no access to that store. Store scope is enforced on every server operation, and an unassigned store must be indistinguishable from a non-existent one (US-002 AC-02).

## Accounting periods module

### `accounting_months`

Represents a store-specific month. Monthly profit is calculated per store, and orders belong to the month of Walmart order date.[cite:88]

| Column | Type | Notes |
|---|---|---|
| id | uuid pk | Primary key |
| store_id | uuid fk -> stores.id | Store |
| year | integer | Period year |
| month | integer | 1-12 |
| period_start | date | First local calendar day of the month |
| period_end | date | Last local calendar day of the month |
| status_code | text | `open`, `ready_for_review`, `closing_review`, `closed`, `reopened` |
| has_ever_been_closed | boolean default false | **Never reset to false.** Set true on first close and permanently thereafter |
| close_count | integer default 0 | Number of completed closes; supports reclose versioning |
| ready_marked_by | uuid fk -> users.id nullable | Manager/Admin/Owner |
| ready_marked_at | timestamptz nullable | Audit |
| ready_basis_revision | text nullable | Evidence revision the readiness decision was made against (US-022 AC-04) |
| closed_by | uuid fk -> users.id nullable | Owner/Admin (BR-07) |
| closed_at | timestamptz nullable | Audit |
| reopened_by | uuid fk -> users.id nullable | Owner/Admin (BR-07) |
| reopened_at | timestamptz nullable | Audit |
| close_note | text nullable | Required at close |
| unresolved_exception_count | integer default 0 | Denormalized summary; not a source of truth |
| created_at | timestamptz | Audit |
| updated_at | timestamptz | Audit |

Recommended unique constraint:
- unique `(store_id, year, month)`

### Why `has_ever_been_closed` exists

BR-08 protects financial records in months that **have been** closed, and US-024 AC-02 requires that protection to survive a reopen. If protection were derived from `status_code`, an Admin reopening a month would silently grant themselves historical edit authority — the exact bug BR-08 forbids.

Therefore:

- `status_code` governs **workflow availability** (can it be marked ready, closed, reopened).
- `has_ever_been_closed` governs **financial edit authority** (Owner-only once true).

These two must never be collapsed into one column, and no code path may set `has_ever_been_closed` back to false.

Months are **provisioned explicitly**, by a scheduled job that maintains a configured horizon of future months per store, or manually by Owner or Admin for backfill (`period-v1`). The provisioning job is idempotent and never closes, reopens, or otherwise modifies an existing month.

Importers must not create them. An order whose date falls outside any provisioned month for its store is retained with `posting_status_code = unpostable_no_period` and raises an exception (US-005 AC-05).

Period boundaries are local calendar dates in the store's timezone. Month membership is evaluated by comparing `orders.order_date` to `year` and `month`; no UTC boundary arithmetic occurs at query time.

### `month_close_snapshots`

Every close produces an immutable, reproducible record of the approved result. US-023 AC-04 requires it, US-024 AC-04 requires reclose to add a version rather than replace one, US-026 AC-03 requires later edits not to alter it, and D-06 governs its contents.

| Column | Type | Notes |
|---|---|---|
| id | uuid pk | Primary key |
| accounting_month_id | uuid fk -> accounting_months.id | Month |
| snapshot_version | integer | 1 for the first close, incremented on each reclose |
| closed_by | uuid fk -> users.id | Actor |
| closed_at | timestamptz | When |
| close_note | text | Required |
| calculation_policy_version | text | Which calculation rules produced these figures |
| currency_code | char(3) | Store reporting currency at close time |
| included_order_count | integer | Coverage |
| totals | jsonb | Approved aggregate figures; shape fixed by `close-v1`, keyed by currency |
| included_revisions | jsonb | Per-entity revision/version references included in this snapshot |
| content_hash | text | Integrity check for reproducibility |
| superseded_by_id | uuid fk -> month_close_snapshots.id nullable | Set when a later reclose supersedes this version |
| created_at | timestamptz | Audit |

Hard rules:

- Rows are **insert-only**. No `UPDATE` path except setting `superseded_by_id`.
- Reclose inserts `snapshot_version + 1`. It never edits or deletes an earlier version.
- Superseded snapshots remain readable and reportable forever. There is no deletion or archival purge path.
- Recommended unique constraint: `(accounting_month_id, snapshot_version)`.
- A historical correction by the Owner creates `snapshot_version + 1` **without reopening the month** (`close-v1`).

`totals` is keyed by currency code, each holding:

```json
{
  "USD": {
    "order_count": 412,
    "complete_order_count": 400,
    "incomplete_order_count": 12,
    "deferred_order_count": 3,
    "selling_amount_total": "18402.55",
    "total_cost": "15118.20",
    "estimated_profit_total": "3284.35",
    "settled_profit_total": "2910.08",
    "settled_to_date_total": "188.40",
    "variance_total": "-374.27",
    "coverage_counts": { "none": 6, "partial": 14, "complete": 392 },
    "unresolved_exception_count": 2
  }
}
```

Incomplete orders are counted and listed, never summed into totals as zero. Currencies are never combined.

### `month_checklist_definitions`

Checklist item catalog, so month-close logic can evolve without code rewrites.[cite:704][cite:711]

| Column | Type | Notes |
|---|---|---|
| id | uuid pk | Primary key |
| code | text unique | Stable machine code |
| label | text | UI label |
| description | text nullable | Guidance |
| is_required | boolean | Required vs optional |
| sort_order | integer | UI ordering |
| is_active | boolean | Soft-retire support |

### `month_checklist_results`

Checklist completion state per accounting month.

| Column | Type | Notes |
|---|---|---|
| id | uuid pk | Primary key |
| accounting_month_id | uuid fk -> accounting_months.id | Month |
| checklist_definition_id | uuid fk -> month_checklist_definitions.id | Item |
| status_code | text | `pending`, `complete`, `waived` |
| note | text nullable | Explanation |
| acted_by | uuid fk -> users.id nullable | Actor |
| acted_at | timestamptz nullable | Audit |

Recommended unique constraint:
- unique `(accounting_month_id, checklist_definition_id)`

### `month_actions`

Explicit action/event table for month workflow. This is more future-proof than relying only on columns in `accounting_months`.

| Column | Type | Notes |
|---|---|---|
| id | uuid pk | Primary key |
| accounting_month_id | uuid fk -> accounting_months.id | Month |
| action_code | text | `mark_ready`, `close`, `reopen`, `comment` |
| actor_id | uuid fk -> users.id | Actor |
| note | text nullable | Explanation |
| created_at | timestamptz | Audit |

## Orders module

### `orders`

Imported Walmart order header. One SKU per order (BR-02); there is no line-item table.

| Column | Type | Notes |
|---|---|---|
| id | uuid pk | Primary key |
| store_id | uuid fk -> stores.id | Store |
| walmart_order_id | text | External identifier, unique **within a store** |
| walmart_purchase_order_id | text nullable | Optional external id |
| sku | text | The order's single SKU (BR-02) |
| item_description | text nullable | Imported display text |
| order_placed_at_utc | timestamptz | Source purchase instant, stored verbatim |
| order_date | date | **Transaction date.** `order_placed_at_utc` converted to the store timezone, reduced to a calendar date. Always follows the source |
| accounting_month_id | uuid fk -> accounting_months.id nullable | **Posting period.** Null only while unpostable |
| scope_status_code | text | `managed` or `pre_onboarding` (BR-14). Evaluated **before** posting |
| posting_reason_code | text nullable | `natural`, `deferred_closed_period`, `owner_reposted`. Null when out of scope |
| posting_status_code | text | `posted`, `out_of_scope`, `unpostable_no_period`, `unpostable_no_open_period` |
| posted_at | timestamptz nullable | When the posting period was assigned |
| quantity | integer | Imported |
| selling_amount | numeric(12,2) | Imported total for the order |
| currency_code | char(3) | Currency of `selling_amount` (BR-12) |
| fulfillment_status_code | text | **Fulfillment dimension only** |
| fulfilled_quantity | integer nullable | Supports partial fulfillment (US-007 AC-03) |
| acknowledged_at | timestamptz nullable | Optional |
| shipped_at | timestamptz nullable | Optional |
| delivered_at | timestamptz nullable | Optional |
| cancelled_at | timestamptz nullable | Optional |
| source_revision | text nullable | Source payload revision for idempotent refresh |
| last_imported_at | timestamptz | Freshness (US-005 AC-03) |
| created_at | timestamptz | Audit |
| updated_at | timestamptz | Audit |

**`fulfillment_status_code` permitted values — exhaustive:**

- `acknowledged`
- `shipped_in_transit`
- `delivered`
- `cancelled`

That is the complete set. Per the binding interpretation of BR-10:

- `settlement_pending` and `reconciled` are **reconciliation** values and live only in `order_reconciliations.status_code`.
- `refunded` is **not** a fulfillment value. Refunds are settlement evidence in `settlement_entries`; a refunded order keeps its own fulfillment state, which is usually `delivered`. There is no `refunded_at` column, because an order can have several partial refunds at different dates (US-027 AC-02).
- Period protection is read from `accounting_months`, never stored on the order.

A reader needing "the state of this order" assembles four values from four sources. That is intentional.

### Transaction date versus posting period

`order_date` and `accounting_month_id` answer different questions and must never be collapsed, per `period-v1` and BR-13.

- `order_date` is **when the sale happened**. It follows the source unconditionally and drives order-month cohort reporting.
- `accounting_month_id` is **which period the order is booked into**. It drives posting-month reporting and month close.

They name the same month for every order except one case: an order whose true month had already been closed when it arrived. That order posts forward to the earliest open month and is marked `deferred_closed_period`.

Rules:

- `scope_status_code` is evaluated **first**. A `pre_onboarding` order gets `accounting_month_id = null`, `posting_status_code = out_of_scope`, and is excluded from every financial aggregate, readiness check, and close snapshot (BR-14). It is never deferred forward — deferral is for late orders, not for orders that were never in scope.
- `accounting_month_id` is **not** a derived column. Do not add a trigger or generated expression computing it from `order_date`; that would destroy the deferral.
- `posting_reason_code = natural` means the two agree. Any other value means they diverge and the UI must show both.
- An order with `posting_status_code != 'posted'` is retained, visible, and excluded from every monthly total until an Owner posts it.
- The true month is always derivable from `order_date`; it is not duplicated into its own column.

### `order_posting_history`

Append-only record of posting-period assignments and moves. Required because `period-v1` permits re-posting only between open periods and makes Owner re-posting an audited act.

| Column | Type | Notes |
|---|---|---|
| id | uuid pk | Primary key |
| order_id | uuid fk -> orders.id | Order |
| sequence | integer | 1, 2, 3... |
| from_accounting_month_id | uuid fk -> accounting_months.id nullable | Null on initial posting |
| to_accounting_month_id | uuid fk -> accounting_months.id nullable | Null when becoming unpostable |
| from_order_date | date nullable | Prior transaction date |
| to_order_date | date | Transaction date at this point |
| reason_code | text | `initial`, `source_date_corrected`, `owner_reposted`, `deferred_closed_period` |
| actor_id | uuid fk -> users.id nullable | Null for system |
| note | text nullable | Required for `owner_reposted` |
| created_at | timestamptz | Audit |

Recommended unique constraint: `(order_id, sequence)`. Insert-only.

### `order_fulfillment_history`

Full history of the fulfillment dimension, instead of only current state. Renamed from `order_status_history` to prevent it from accumulating reconciliation or refund states.

| Column | Type | Notes |
|---|---|---|
| id | uuid pk | Primary key |
| order_id | uuid fk -> orders.id | Order |
| fulfillment_status_code | text | Same four-value set as `orders.fulfillment_status_code` |
| fulfilled_quantity | integer nullable | Quantity at this point |
| changed_at | timestamptz | When it happened |
| changed_by | uuid fk -> users.id nullable | Actor or system |
| source | text | `import`, `manual` |
| note | text nullable | Context |

Reconciliation history belongs in the reconciliation module. Refund history belongs in `settlement_entries`. Do not write either into this table.

### `order_assignments`

Optional link from orders to managers. Orders do not need a manager at all.[cite:684]

| Column | Type | Notes |
|---|---|---|
| id | uuid pk | Primary key |
| order_id | uuid fk -> orders.id | Order |
| user_id | uuid fk -> users.id | Manager |
| assigned_at | timestamptz | Audit |
| unassigned_at | timestamptz nullable | End |
| assigned_by | uuid fk -> users.id nullable | Actor |

Because assignment is optional, no foreign key should force every order to have a manager.[cite:684]

## Operational costs module

### `order_cost_profiles`

Stores the current effective manual and store-derived operational cost values for an order.

| Column | Type | Notes |
|---|---|---|
| id | uuid pk | Primary key |
| order_id | uuid fk -> orders.id unique | One active cost profile per order |
| currency_code | char(3) | Currency of every amount in this row (BR-12) |
| buying_cost_per_unit | numeric(12,2) nullable | Manual. Null means **unknown**, not zero |
| buying_cost_per_unit_is_known_zero | boolean default false | Distinguishes an explicit zero from a blank (US-008 AC-03) |
| quantity_snapshot | integer | Quantity used for the total |
| buying_cost_total | numeric(12,2) nullable | `buying_cost_per_unit * quantity_snapshot` |
| store_settings_version_applied | integer | Which `store_settings` version supplied the default |
| warehouse_cost_default_snapshot | numeric(12,2) | Store default captured at time of use |
| warehouse_cost_override | numeric(12,2) nullable | Optional per-order override |
| warehouse_cost_effective | numeric(12,2) nullable | Override when present, otherwise the snapshot |
| third_party_label_cost | numeric(12,2) nullable | Manual |
| other_cost | numeric(12,2) nullable | Manual |
| cost_completeness_code | text | `incomplete`, `complete`; derived, never user-set |
| record_revision | integer | Optimistic concurrency token (US-014 AC-01) |
| notes | text nullable | Context |
| created_by | uuid fk -> users.id | Actor |
| updated_by | uuid fk -> users.id | Actor |
| created_at | timestamptz | Audit |
| updated_at | timestamptz | Audit |

Rules:

- The store default snapshot and the override are separate columns because historical order economics must not change when store settings change later (US-010 AC-04). `store_settings_version_applied` makes the snapshot traceable to the exact policy row that produced it.
- Warehouse cost is **per order**, applied once regardless of quantity (US-010 AC-03).
- Null means unknown. Nothing in this table may be coalesced to zero for display or calculation (US-008 AC-02, US-007 AC-04).
- No profit column appears here. Profit is computed under `profit-v1` and stored on `order_estimate_versions` and `order_reconciliations`, where it can be versioned and stamped. A cost profile holds inputs, never results.

### `order_cost_field_edits`

Append-only record of every manual cost change. The allowance is **per field**, so this table is the authoritative edit counter.

| Column | Type | Notes |
|---|---|---|
| id | uuid pk | Primary key |
| order_id | uuid fk -> orders.id | Order |
| field_code | text | Matches `cost_edit_policies.field_code` |
| previous_value | numeric(12,2) nullable | Prior value |
| new_value | numeric(12,2) nullable | New value |
| currency_code | char(3) | Currency of both values |
| edit_sequence | integer | 1, 2, 3... per `(order_id, field_code)` |
| change_kind | text | `initial_entry`, `correction`, `clear_to_blank` |
| consumed_allowance | boolean | True for `correction` and `clear_to_blank`, false for `initial_entry` under `cost-edit-v1` |
| policy_version_applied | integer | `cost_edit_policies.policy_version` in force at the time |
| edited_by | uuid fk -> users.id | Actor |
| edited_at | timestamptz | Audit |
| reason | text nullable | Explanation |
| override_request_id | uuid fk -> edit_override_requests.id nullable | Set when the edit was authorized by an approved override |
| operation_id | text | Idempotency key preventing a retry from double-counting (US-014 AC-03) |

Rules:

- Edits are counted per `(order_id, field_code)`, never per order.
- Rows are **insert-only**. An intervention never deletes or resets history (US-012 AC-04).
- Recommended unique constraints: `(order_id, field_code, edit_sequence)` and `(operation_id)`.
- The allowance is read from `cost_edit_policies` at request time. No numeric limit appears in this table's definition.
- Under `cost-edit-v1`: `initial_entry` does not consume; `correction` and `clear_to_blank` consume one each.
- An unchanged submission produces **no row at all**. The server diffs per field and skips unchanged ones, so a resaved form leaves no trace and consumes nothing.
- A rejected write — validation failure, stale revision, permission denial — produces no row and consumes nothing.
- `consumed_count` for a field is `COUNT(*) WHERE consumed_allowance = true`. This is the authoritative counter; nothing caches it on `order_cost_profiles`.
- An edit authorized by an approved override still sets `consumed_allowance = true` and sets `override_request_id`. **Overrides never reset the counter**, so each subsequent change needs a fresh override.
- Counting and limit enforcement must occur inside the same serializable transaction as the write, so two concurrent saves cannot both consume the last allowance (US-011 AC-03).

### `edit_override_requests`

Captures post-limit edit authorization.

| Column | Type | Notes |
|---|---|---|
| id | uuid pk | Primary key |
| order_id | uuid fk -> orders.id | Order |
| field_code | text | Field requiring override |
| requested_by | uuid fk -> users.id | Requester |
| approved_by | uuid fk -> users.id nullable | Admin/Owner |
| status_code | text | `pending`, `approved`, `rejected`, `expired`, `escalated_to_owner` |
| current_value | numeric(12,2) nullable | Value at time of request (US-012 AC-01) |
| proposed_value | numeric(12,2) nullable | Requested value |
| currency_code | char(3) | Currency of both |
| requires_owner | boolean default false | True when the order's month has ever been closed (BR-08) |
| reason | text | Why the edit is needed |
| created_at | timestamptz | Audit |
| resolved_at | timestamptz nullable | Audit |

`requires_owner` is set from `accounting_months.has_ever_been_closed`, not from the month's current status. An Admin may resolve open-period requests only; a request against a previously closed month must escalate to the Owner even if the month is currently reopened (US-012 AC-03, BR-08).

## Financial estimates module

### `order_estimate_versions`

Stores immutable estimate snapshots. This is better than a single mutable estimate row because estimates may be revised before or during reconciliation and still need historical visibility.[cite:88][cite:716][cite:723]

| Column | Type | Notes |
|---|---|---|
| id | uuid pk | Primary key |
| order_id | uuid fk -> orders.id | Order |
| version_no | integer | 1, 2, 3... |
| currency_code | char(3) | Currency of all amounts in this row |
| estimated_walmart_fee | numeric(12,2) nullable | Estimate; null means unknown |
| estimated_walmart_label_cost | numeric(12,2) nullable | Estimate; null means unknown |
| estimated_other_marketplace_cost | numeric(12,2) nullable | Optional |
| estimated_total_cost | numeric(12,2) nullable | Sum of the six cost components; null when inputs are incomplete |
| estimated_net_profit | numeric(12,2) nullable | `selling_amount - estimated_total_cost`; null when inputs are incomplete |
| estimated_roi | numeric(9,4) nullable | `estimated_net_profit / estimated_total_cost`; null when total cost is zero or inputs are incomplete |
| cost_profile_revision | integer | `order_cost_profiles.record_revision` used as input |
| calculation_policy_version | text | `profit-v1` |
| input_completeness_code | text | `complete`, `incomplete`; incomplete estimates are never labeled final (US-013 AC-03) |
| missing_component_codes | text[] nullable | Which cost components were unknown, when incomplete |
| source_code | text | `manager_manual`, `imported`, `system_calc` |
| created_by | uuid fk -> users.id nullable | Actor |
| created_at | timestamptz | Audit |
| is_current | boolean | Convenience flag |

Recommended constraints:
- unique `(order_id, version_no)`
- exactly one current version per order, via partial unique index where `is_current = true`

Rules:

- Rows are **insert-only**. Creating a new version clears `is_current` on the prior row; it never edits the prior row's amounts (US-013 AC-01).
- Settlement data never writes to this table. Settled values live in `settlement_entries` (BR-03, BR-05).
- Profit and ROI are computed under `profit-v1` and are **null whenever any cost component is unknown**. Null here means "cannot be stated", never zero.
- `missing_component_codes` is what lets the UI tell a Manager exactly which input to supply instead of showing a blank.

### Calculation policy versioning

`profit-v1` is implemented in the domain layer, not in generated columns, triggers, or view expressions. The version string is stamped on every stored result so a figure remains explicable after the formula changes.

Precision rules, binding on every implementation:

- Stored monetary values are `numeric(12,2)`.
- Intermediate arithmetic uses exact decimal at full precision — PostgreSQL `numeric` without a scale, or an equivalent decimal type. **Binary floating point is prohibited.**
- Exactly one half-up rounding to 2 decimal places is applied, to the final stored result.
- ROI is derived from unrounded profit and unrounded total cost, stored at `numeric(9,4)`.

Changing the formula means issuing `profit-v2` and recomputing forward. It never rewrites rows stamped `profit-v1`, and it never alters a `month_close_snapshots` row.

## Settlements module

The schema must allow one order to have multiple settlement-related entries over time, including later refunds, fee adjustments, and corrections.[cite:88][web:734][web:738]

### `settlement_entry_types`

Optional code table for extensibility.

| Column | Type | Notes |
|---|---|---|
| id | uuid pk | Primary key |
| code | text unique | Example: `sale`, `refund`, `fee_adjustment`, `label_adjustment`, `other_adjustment` |
| label | text | Human-readable |
| affects_profit_direction | smallint | 1, -1, or 0 optional |
| is_active | boolean | Soft-retire |

### `settlement_report_definitions`

The catalogue of Walmart report types and versions the importer accepts. Pending configuration C-02.

| Column | Type | Notes |
|---|---|---|
| id | uuid pk | Primary key |
| report_type_code | text | Walmart report family |
| report_version | text | Source format version |
| parser_version | text | Our normalizer version |
| is_active | boolean | Soft-retire |
| created_at | timestamptz | Audit |

Recommended unique constraint: `(report_type_code, report_version)`.

Ships **empty**. An unrecognized report version fails the import visibly rather than guessing column positions (US-015 AC-04).

### `settlement_category_mappings`

Maps a source category or column to one of our settled fields. Pending configuration C-01, and the design resolution of D-10.

| Column | Type | Notes |
|---|---|---|
| id | uuid pk | Primary key |
| report_definition_id | uuid fk -> settlement_report_definitions.id | Report type and version |
| source_category_code | text | Value or column name as it appears in the source |
| target_field | text | `settled_revenue_amount`, `settled_walmart_fee`, `settled_walmart_label_cost`, `settled_other_amount` |
| sign_convention | smallint | `1` to take as-is, `-1` to invert |
| is_refund | boolean default false | Marks categories that represent refunds |
| notes | text nullable | Why this mapping exists |
| is_active | boolean | Soft-retire |
| created_by | uuid fk -> users.id | Actor |
| created_at | timestamptz | Audit |

Recommended unique constraint: `(report_definition_id, source_category_code)` where `is_active = true`.

Ships **empty**. A source category with no active mapping is committed into `settled_other_amount`, flagged into suspense, and raises an exception. It is **never** guessed into a fee or label bucket (`settlement-v1`).

### `variance_classification_policies`

Thresholds that classify a variance as minor or material. Advisory only — classification never confirms a reconciliation.

| Column | Type | Notes |
|---|---|---|
| id | uuid pk | Primary key |
| store_id | uuid fk -> stores.id nullable | Null means global |
| absolute_threshold | numeric(12,2) nullable | Amount in store currency |
| percentage_threshold | numeric(5,2) nullable | Percent of estimated profit |
| currency_code | char(3) nullable | Required when an absolute threshold is set |
| policy_version | integer | Monotonic |
| effective_from | timestamptz | Prospective |
| effective_to | timestamptz nullable | Null for current |
| created_by | uuid fk -> users.id | Actor |
| created_at | timestamptz | Audit |

Ships **unconfigured** (C-03). With no policy, every variance is `unclassified` and behaves exactly as it would with no tolerance concept at all. Nothing auto-reconciles under any configuration.

### `settlement_batches`

Represents an imported Walmart payout/settlement batch.

| Column | Type | Notes |
|---|---|---|
| id | uuid pk | Primary key |
| store_id | uuid fk -> stores.id | Store |
| external_batch_id | text | External settlement id, unique **within a store** |
| source_checksum | text nullable | Duplicate detection (US-015 AC-01, US-016 AC-01) |
| parser_version | text | Mapping version used to normalize this batch |
| payout_date | date nullable | Bank/payout date |
| settlement_period_start | date nullable | Optional |
| settlement_period_end | date nullable | Optional |
| currency_code | char(3) | Currency of batch-level amounts |
| gross_amount | numeric(14,2) nullable | Optional import |
| net_amount | numeric(14,2) nullable | Optional import |
| commit_state_code | text | `staged`, `committed`, `rejected` — staged rows have no financial effect (US-015 AC-03) |
| import_batch_id | uuid fk -> import_batches.id nullable | Traceability |
| created_at | timestamptz | Audit |

Batch-level `gross_amount` / `net_amount` are payout summaries. They must never be added to order revenue alongside transaction-level entries (US-017 AC-02).

### `settlement_entries`

Represents individual financial events inside a payout or adjustment report.

| Column | Type | Notes |
|---|---|---|
| id | uuid pk | Primary key |
| settlement_batch_id | uuid fk -> settlement_batches.id | Batch |
| order_id | uuid fk -> orders.id nullable | Optional linked order |
| settlement_entry_type_id | uuid fk -> settlement_entry_types.id nullable | Type |
| external_entry_id | text nullable | External row id if available |
| event_date | date nullable | Financial event date |
| reporting_month_year | integer nullable | Denormalized optional |
| reporting_month_number | integer nullable | Denormalized optional |
| settled_walmart_fee | numeric(12,2) nullable | Settled value |
| settled_walmart_label_cost | numeric(12,2) nullable | Settled value |
| settled_revenue_amount | numeric(12,2) nullable | Revenue or reversal |
| settled_other_amount | numeric(12,2) nullable | Misc adjustment |
| settled_net_effect | numeric(12,2) nullable | Final line effect |
| currency_code | char(3) | Currency of this entry (BR-12). **No default value** |
| allocation_state_code | text | `allocated`, `unallocated`, `suspense` — unallocated entries are excluded from order profit (US-017 AC-03) |
| commit_state_code | text | `staged`, `committed`, `superseded` |
| superseded_by_id | uuid fk -> settlement_entries.id nullable | Source corrections supersede rather than overwrite (US-017 AC-04) |
| operation_id | text | Idempotency key preventing duplicate commits (US-016 AC-01) |
| raw_payload | jsonb nullable | Source fidelity |
| created_at | timestamptz | Audit |

This table is the main future-proofing mechanism. One order can relate to zero, one, or many settlement entries over time.[web:733][web:736][web:738]

Rules:

- Rows are **insert-only**. A correction inserts a new entry and sets `superseded_by_id` on the old one; it never edits amounts in place.
- Refunds are entries here with a `refund` type and a signed amount. There is no refund flag on `orders`.
- An entry's `event_date` drives refund-event-month reporting; the order's `order_date` drives order-month reporting. The two are never interchanged (US-027 AC-01).
- A `settlement_entry` linked to an order must belong to a batch whose `store_id` matches that order's `store_id`. Enforce this with a composite foreign key or a trigger; a cross-store link is a data-integrity failure, not a reconciliation exception.
- `currency_code` has no default. An entry whose source currency cannot be determined is rejected into `import_errors` rather than assumed (US-015 AC-02).

### `refund_cost_recoveries`

Manual record of how much buying cost was recovered on a refund. Implements `refund-v1`.

| Column | Type | Notes |
|---|---|---|
| id | uuid pk | Primary key |
| settlement_entry_id | uuid fk -> settlement_entries.id | The refund entry this recovery relates to |
| order_id | uuid fk -> orders.id | Denormalized for querying |
| recovered_amount | numeric(12,2) nullable | Null means **unknown**, not zero |
| is_known_zero | boolean default false | Explicit "nothing was recovered" |
| currency_code | char(3) | Must match the order currency |
| recovered_quantity | integer nullable | Units actually recovered |
| reason | text nullable | Context, such as damaged or not returned |
| recorded_by | uuid fk -> users.id | Actor |
| recorded_at | timestamptz | Audit |

Rules:

- One recovery record per refund entry. Recommended unique constraint on `settlement_entry_id`.
- Until a recovery is recorded, the order's settled profit is `incomplete` with reason `refund_recovery_unknown`. It is never computed as if recovery were zero.
- `recovered_amount` may not exceed the buying cost attributable to the refunded quantity. Enforce in the domain layer.
- Settled profit uses buying cost **net of recovery**, per `refund-v1`.
- This record is a financial write. In a previously closed month it is Owner-only (BR-08).

## Reconciliation module

### `order_reconciliations`

Represents review status for an order against its current settlement picture.

| Column | Type | Notes |
|---|---|---|
| id | uuid pk | Primary key |
| order_id | uuid fk -> orders.id unique | One current reconciliation summary per order |
| status_code | text | `not_ready`, `pending_settlement`, `needs_review`, `reconciled`, `reconciled_with_exception` |
| latest_estimate_version_id | uuid fk -> order_estimate_versions.id nullable | Trace current estimate |
| settlement_evidence_revision | text nullable | Which settlement picture was reviewed (US-019 AC-01) |
| coverage_code | text | Declared completeness of settlement evidence |
| currency_code | char(3) | Currency of the amounts below |
| total_settled_net_effect | numeric(12,2) nullable | Aggregate of committed, allocated entries |
| settled_total_cost | numeric(12,2) nullable | Six components using settled Walmart values |
| settled_net_profit | numeric(12,2) nullable | Null unless coverage is complete |
| settled_roi | numeric(9,4) nullable | Null when settled total cost is zero or coverage is incomplete |
| variance_profit_amount | numeric(12,2) nullable | `settled_net_profit - estimated_net_profit`; null unless both exist |
| variance_classification_code | text | `minor`, `material`, `unclassified` — advisory only |
| calculation_policy_version | text | `profit-v1` |
| incomplete_reason_codes | text[] nullable | Why a settled figure could not be produced, such as `refund_recovery_unknown` |
| decision_basis_code | text nullable | `evidence_based`, `manual_fallback` (BR-10, US-019 AC-03) |
| has_exception | boolean | Fast filtering |
| reviewer_id | uuid fk -> users.id nullable | Owner/Admin reviewer |
| reviewed_at | timestamptz nullable | Audit |
| note | text nullable | Review note |
| updated_at | timestamptz | Audit |

**This table owns the reconciliation state dimension.** `settlement_pending` and `reconciled` live here and nowhere else — in particular, not on `orders`.

Rules:

- This summary is derived from `settlement_entries` and `order_estimate_versions`. It is a cache for querying, never the only source of truth.
- Settled figures aggregate only entries that are **committed and allocated**. Staged, superseded, and unallocated entries are excluded (US-017 AC-03).
- `coverage_code` controls labeling: complete coverage yields `settled_net_profit`; partial coverage yields a settled-to-date figure that must never be presented as final; no coverage yields "Not settled", never zero (US-007 AC-04).
- Refunds reduce `settled_revenue` naturally as negative entries. Buying cost is taken **net of recorded recovery** from `refund_cost_recoveries`. An order with an unrecorded recovery is `incomplete`, never computed as if recovery were zero (`refund-v1`).
- `variance_classification_code` is advisory triage only. No value of it may cause a reconciliation to confirm itself (`settlement-v1`).
- Prior confirmations are preserved in `order_reconciliation_decisions` (below), so a later material entry can reset `status_code` to `needs_review` without destroying the earlier decision (US-020 AC-01, AC-02).
- A `manual_fallback` decision records a judgment. It must not create synthetic `settlement_entries` (US-019 AC-04).

### `order_reconciliation_decisions`

Append-only history of reconciliation decisions. Required because US-020 AC-02 demands that a superseded confirmation remain visible with its original coverage and evidence revision.

| Column | Type | Notes |
|---|---|---|
| id | uuid pk | Primary key |
| order_id | uuid fk -> orders.id | Order |
| decision_sequence | integer | 1, 2, 3... |
| status_code | text | Decided status |
| decision_basis_code | text | `evidence_based`, `manual_fallback` |
| estimate_version_id | uuid fk -> order_estimate_versions.id nullable | Basis at decision time |
| settlement_evidence_revision | text nullable | Basis at decision time |
| coverage_code | text | Declared coverage |
| reviewer_id | uuid fk -> users.id | Owner/Admin |
| decided_at | timestamptz | Audit |
| reason | text nullable | Required for manual fallback |
| superseded_at | timestamptz nullable | Set when later activity invalidates this decision |

Recommended unique constraint: `(order_id, decision_sequence)`. Insert-only.

### `reconciliation_exceptions`

Stores unresolved or accepted exception details.

| Column | Type | Notes |
|---|---|---|
| id | uuid pk | Primary key |
| order_id | uuid fk -> orders.id | Order |
| reconciliation_id | uuid fk -> order_reconciliations.id | Reconciliation summary |
| exception_code | text | Classification; see the code table below |
| exception_class | text | `data_quality` or `financial` — determines who may resolve (`access-v1`) |
| severity_code | text | `info`, `warning`, `critical` |
| is_hard_blocker | boolean default false | Unwaivable by any role; resolved only by fixing the data |
| status_code | text | `open`, `accepted`, `resolved` |
| description | text | Human explanation |
| created_by | uuid fk -> users.id nullable | Actor |
| created_at | timestamptz | Audit |
| resolved_by | uuid fk -> users.id nullable | Actor |
| resolved_at | timestamptz nullable | Audit |
| resolution_note | text nullable | Context |

#### Exception code classification

`exception_class` is a property of the code, not a per-row judgment. Seed it with the codes themselves.

| Code | Class | Hard blocker |
|---|---|:--:|
| `missing_cost_data` | `data_quality` | No |
| `unmatched_sku` | `data_quality` | No |
| `stale_order_data` | `data_quality` | No |
| `order_detail_mismatch` | `data_quality` | No |
| `settlement_variance` | `financial` | No |
| `unallocated_settlement_entry` | `financial` | **Yes** |
| `duplicate_settlement_conflict` | `financial` | **Yes** |
| `coverage_incomplete` | `financial` | No |
| `currency_mismatch` | `financial` | **Yes** |
| `refund_recovery_unknown` | `financial` | No |
| `historical_impact` | `financial` | No |
| `late_activity_closed_period` | `financial` | No |
| `posting_period_protected` | `financial` | No |
| `unpostable_order` | `financial` | **Yes** |

Resolution authority, per `access-v1`:

- `data_quality` — Owner, Admin, or Manager.
- `financial` — Owner or Admin only.
- Severity `critical` requires Owner or Admin regardless of class.
- `is_hard_blocker = true` cannot be waived, accepted, or noted away by anyone. It clears only when the underlying data is corrected (US-021 AC-03).

## Imports module

### `import_batches`

Tracks every import run.

| Column | Type | Notes |
|---|---|---|
| id | uuid pk | Primary key |
| import_type | text | `orders`, `settlements`, `stores` |
| store_id | uuid fk -> stores.id nullable | Store-scoped when relevant |
| source_name | text nullable | File or integration name |
| status_code | text | `pending`, `processing`, `completed`, `failed`, `partial` |
| imported_by | uuid fk -> users.id nullable | Actor |
| started_at | timestamptz | Audit |
| finished_at | timestamptz nullable | Audit |
| row_count | integer default 0 | Stats |
| success_count | integer default 0 | Stats |
| error_count | integer default 0 | Stats |

### `import_errors`

Reviewable import errors.

| Column | Type | Notes |
|---|---|---|
| id | uuid pk | Primary key |
| import_batch_id | uuid fk -> import_batches.id | Batch |
| row_number | integer nullable | Source row |
| external_reference | text nullable | Helpful key |
| error_code | text | Machine code |
| error_message | text | Human explanation |
| raw_row | jsonb nullable | Source data |
| created_at | timestamptz | Audit |

### `raw_import_records` optional

Useful if you want durable staging for reprocessing without reuploading.

| Column | Type | Notes |
|---|---|---|
| id | uuid pk | Primary key |
| import_batch_id | uuid fk -> import_batches.id | Parent batch |
| record_type | text | Source classification |
| external_reference | text nullable | Source key |
| payload | jsonb | Raw imported row |
| normalized | boolean default false | Processing state |
| created_at | timestamptz | Audit |

## Audit and approvals module

### `approvals`

Generic approval log for override, close, reopen, or future workflows.

| Column | Type | Notes |
|---|---|---|
| id | uuid pk | Primary key |
| entity_type | text | Example: `accounting_month`, `edit_override_request` |
| entity_id | uuid | Referenced record id |
| action_code | text | `approve_close`, `approve_override`, `reopen` |
| decision_code | text | `approved`, `rejected` |
| actor_id | uuid fk -> users.id | Actor |
| note | text nullable | Reason |
| created_at | timestamptz | Audit |

### `audit_events`

Append-only event log for sensitive business actions. Good audit trails require the right events, preserved history, and evidence of who changed what and when.[cite:718][cite:567][cite:212]

| Column | Type | Notes |
|---|---|---|
| id | uuid pk | Primary key |
| actor_id | uuid fk -> users.id nullable | User or system |
| entity_type | text | Table or domain object |
| entity_id | uuid nullable | Target entity |
| event_type | text | Example: `cost_updated`, `month_closed`, `override_approved` |
| before_state | jsonb nullable | Prior state |
| after_state | jsonb nullable | New state |
| reason | text nullable | Why |
| created_at | timestamptz | Audit |

## Recommended keys and constraints

### Business uniqueness

Recommended unique constraints:

- `stores.walmart_store_key`
- `accounting_months (store_id, year, month)`
- `orders (store_id, walmart_order_id)` — **scoped to the store, not global.** Two stores may legitimately receive the same external order identifier (US-005 AC-02)
- `month_checklist_results (accounting_month_id, checklist_definition_id)`
- `month_close_snapshots (accounting_month_id, snapshot_version)`
- `order_estimate_versions (order_id, version_no)`
- `order_cost_field_edits (order_id, field_code, edit_sequence)`
- `order_posting_history (order_id, sequence)`
- `order_cost_field_edits (operation_id)`
- `order_reconciliation_decisions (order_id, decision_sequence)`
- `settlement_batches (store_id, external_batch_id)` — store-scoped, same reasoning as orders
- `settlement_entries (operation_id)`
- `cost_edit_policies (store_id, field_code, policy_version)`
- `store_settings (store_id, settings_version)`

### Partial uniqueness

Useful partial indexes:

- one current estimate version per order where `is_current = true`
- one current store settings row per store where `effective_to is null`
- one current cost edit policy per `(store_id, field_code)` where `effective_to is null`
- one active order assignment row per `(order_id, user_id)` where `unassigned_at is null`
- one active store-manager assignment row per `(store_id, user_id)` where `active_to is null`
- one unsuperseded close snapshot per month where `superseded_by_id is null`

### Cross-store integrity

An order, its costs, its estimates, its reconciliation, its accounting month, and any settlement entry linked to it must all belong to the same store. Enforce with composite foreign keys carrying `store_id`, or with triggers. A cross-store reference is corruption, not a business exception.

### Validation constraints

Recommended checks:

- month between 1 and 12
- quantity greater than 0 where applicable
- `currency_code` matches ISO 4217 format and **has no column default anywhere**
- monetary fields allow negative values only where financially valid, especially settlement entries and refunds
- `orders.fulfillment_status_code` restricted to the four-value fulfillment set — a check constraint here is the cheapest guard against status re-overloading
- `accounting_months.has_ever_been_closed` may transition false → true only; add a trigger rejecting the reverse
- status codes restricted through enum or check/code tables

### Constraints that must NOT be added

- No `NOT NULL` on monetary fields where null means "unknown". Unknown is a required state (US-008 AC-02, US-013 AC-03).
- No default of `0` or `'USD'` on any monetary or currency column.
- No column default on `cost_edit_policies.max_edits`. The value `2` belongs to the seed row only.
- No generated column, trigger, or view expression implementing a profit formula. `profit-v1` lives in the domain layer so its version can be stamped and audited.
- No `double precision` or `float` on any monetary column.
- No generated column, trigger, or foreign-key cascade deriving `orders.accounting_month_id` from `orders.order_date`. Posting is a decision, not a derivation (`period-v1`).
- No `NOT NULL` on `orders.accounting_month_id`. Unpostable orders must be storable.
- No FX rate table, rate column, or converted-amount column. Conversion does not exist in this release.

## Reporting model

The transactional schema should stay modular, but reporting should not depend on slow ad hoc joins everywhere. Create reporting views.

### Recommended views

- `vw_order_financials_current`
- `vw_store_order_month` — grouped by `orders.order_date`
- `vw_store_posting_month` — grouped by `orders.accounting_month_id`
- `vw_refund_month_effects` — grouped by `settlement_entries.event_date`
- `vw_deferred_orders` — orders where `posting_reason_code != 'natural'`
- `vw_unpostable_orders` — orders with no posting period
- `vw_reconciliation_queue`
- `vw_month_close_readiness`
- `vw_orders_missing_costs`
- `vw_closed_month_changes`

Every view must expose `currency_code`, a basis indicator (`estimated` / `settled` / `snapshot`), a completeness indicator, and `calculation_policy_version` (US-026 AC-02). A view that returns a bare number is incomplete.

Views must not sum across currencies. Group by `currency_code` and let the presentation layer refuse to combine them (BR-12, US-026 AC-04).

Profit-bearing views read figures already computed and stamped under `profit-v1`. They must not reimplement the formula in SQL — two implementations of a profit calculation will diverge, and the version stamp would then be a lie.

Aggregate views exclude `incomplete` rows from totals and report the excluded count alongside, rather than treating an unknown component as zero.

### Important reporting rule

Reporting must distinguish **four** bases and label which one it used:

| Basis | Column | Question |
|---|---|---|
| Order month | `orders.order_date` | "What did we sell in March?" |
| Posting month | `orders.accounting_month_id` | "What is in our March books?" |
| Settlement event month | `settlement_entries.event_date` | "What money moved in March?" |
| Snapshot | `month_close_snapshots` | "What did we approve at close?" |

These are four different questions. A view answering one must never be presented as an answer to another.

Order month and posting month agree for every order except deferred ones, so `vw_deferred_orders` is the reconciliation between the two bases and must stay queryable.

Double counting of refunds is prevented by never summing across bases, not by an allocation rule. A refund appears once in the order-month basis, attributed to its original order's month, and once in the settlement-event basis, attributed to its own date. The two are never combined (`refund-v1`, US-027 AC-03).

## Modularity recommendations for future changes

To keep the schema easy to update later, use these practices:

### 1. Prefer new tables over widening old core tables

If a new business capability appears, such as supplier invoices, commission calculations, or dispute tracking, add a module table instead of stuffing more nullable columns into `orders`.

### 2. Use code tables where business vocabulary may change

Examples:
- settlement entry types,
- checklist definitions,
- approval actions,
- exception types.

### 3. Avoid encoding too much logic in column names

For example, use `order_estimate_versions` instead of hardcoding several future estimate stages as separate columns like `estimate_v1_fee`, `estimate_v2_fee`, and so on.

### 4. Snapshot where history matters

Store effective snapshots for settings-derived values, such as warehouse default cost used at order time, so later setting changes do not corrupt historical reporting.[cite:88]

### 5. Keep write model and read model separate when needed

If the app grows, introduce materialized views or reporting tables for dashboards without polluting transactional tables.

## Suggested migration strategy

A modular schema is only useful if migration discipline is good.

Recommended rules:

- Use one migration per business change.
- Never repurpose an old column for a new meaning.
- Prefer additive changes first, then data migration, then deprecation.
- Keep old data readable during transitions.
- Version imported payload shapes if Walmart data structures evolve.

## Suggested implementation order

1. Identity and roles
2. Stores, versioned settings, and cost edit policies
3. Accounting months
4. Orders and fulfillment history
5. Operational costs and edit tracking
6. Estimate versions
7. Import batches and settlement batches
8. Settlement entries
9. Reconciliation summary, decisions, and exceptions
10. Month close snapshots
11. Audit events and approvals
12. Reporting views

This order keeps the core model stable before advanced controls and dashboards are layered on top.[cite:747][cite:88]

## Review checklist for schema changes

Before any migration is accepted, confirm:

- [ ] No `order_items` or line-item table was introduced (BR-02).
- [ ] No compensation, payment, or payroll table was introduced (BR-09).
- [ ] No column combines fulfillment, refund, reconciliation, or period state (BR-10 interpretation).
- [ ] No column holds an estimated value that a settlement import later overwrites (BR-03, BR-05).
- [ ] No numeric edit limit appears in a column default, check constraint, or application constant. The seed row is the only place `2` exists.
- [ ] No profit formula was implemented in a generated column, trigger, or view; `profit-v1` stays in the domain layer.
- [ ] No monetary column uses a binary floating-point type.
- [ ] Every stored financial result carries `calculation_policy_version`.
- [ ] `accounting_month_id` was not turned into a derived column (`period-v1`, BR-13).
- [ ] No converted-currency or FX rate structure was introduced (BR-12).
- [ ] New date columns state their timezone basis and whether they are local calendar dates or UTC instants.
- [ ] Every new monetary column has an accompanying currency and no default (BR-12).
- [ ] Every new external identifier is unique per store, not globally.
- [ ] Append-only tables gained no `UPDATE` or `DELETE` path.
- [ ] `has_ever_been_closed` still cannot be reset (BR-08).
- [ ] The change is additive, or includes backfill and retirement steps.

## Final recommendation

The most important structural choice is this: **treat orders, estimates, settlement entries, reconciliation decisions, accounting months, and close snapshots as separate but linked modules**. That is what makes the schema resilient to change.

It supports optional manager assignment, multiple settlement entries per order, configurable per-field edit limits, refund-event-month reporting, durable closure protection that survives reopening, per-store currency, and permanent visibility of both estimated and settled values — without forcing destructive redesign later.[cite:684][cite:685][cite:88][web:738]

The single highest-risk regression is someone adding a convenient combined `order_status` column because four lookups felt tedious. That column is how the previous version died.
