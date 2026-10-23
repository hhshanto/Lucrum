# Walmart Store Manager — Product Vision and UX Direction

**Version:** 1.0  
**Date:** 23 October 2026  
**Status:** Non-normative. Vision, scope framing, and UX direction only.

## How to read this document

This document is **rank 5 of 5** in the document set. It carries no authority over business rules, permissions, data model, or API contracts.

| Looking for | Read instead |
|---|---|
| Business rules, scope, acceptance criteria, decision gates | `walmart-user-stories.md` — **authoritative** |
| Capability/permission matrix | `walmart-user-stories.md` → "Capability matrix" |
| Order state vocabulary | `walmart-user-stories.md` → "Binding interpretation of BR-10" |
| Profit, ROI, rounding, tax, label treatment | `walmart-user-stories.md` → "Financial calculation policy — `profit-v1`" |
| Edit limits and the override workflow | `walmart-user-stories.md` → "Manual cost edit policy — `cost-edit-v1`" |
| Timezones, month boundaries, late orders | `walmart-user-stories.md` → "Period and timezone policy — `period-v1`" |
| Authentication, store scope, exception authority | `walmart-user-stories.md` → "Access and authorization policy — `access-v1`" |
| Settlement import, coverage, tolerance, fallback | `walmart-user-stories.md` → "Settlement ingestion policy — `settlement-v1`" |
| Period protection, correction, readiness, snapshots | `walmart-user-stories.md` → "Period protection and close policy — `close-v1`" |
| Refunds, recovery, cancellations | `walmart-user-stories.md` → "Refund and cancellation policy — `refund-v1`" |
| Tables, columns, constraints | `walmart-store-manager-database-schema.md` |
| Endpoints, services, transactions | `walmart-store-manager-api-service-design.md` |
| Layer boundaries, deployment shape | `walmart-store-manager-system-architecture.md` |

Version 0.1 of this file contained a competing entity list, a competing permission matrix, a hardcoded edit limit, and a settlement model that assumed one settlement row per order. All four contradicted the authoritative documents and have been **removed**, not corrected in place. Nothing in this file should be implemented directly.

## Purpose

This document defines why the product exists and what it should feel like to use. It exists to replace an earlier design that mixed concerns, blurred business rules, and produced a weak database model. The goal of the restart is a cleaner development process: stable domain rules, explicit workflows, a durable data model, real role boundaries, and audit-friendly financial handling.

The application is an internal operations and financial-control tool, not a generic e-commerce dashboard or a marketing surface.

## Product definition

The product is an internal multi-store Walmart order, cost, reconciliation, and month-close management application. It is not an order viewer. It must support day-to-day operational order handling, manual cost entry, estimated-versus-settled financial tracking, monthly review, and controlled closing of accounting periods.

The earlier design failed largely because single entities carried several meanings at once — especially around order state, financial state, and month state. The rebuild separates these into distinct bounded areas: order operations, financial estimates, settlement evidence, reconciliation review, and accounting period control.

## Product goals

- Fast daily order processing by managers.
- Reliable manual capture of changing cost inputs.
- Permanent visibility of both estimated and settled amounts.
- Clear store-level monthly profit tracking.
- A controlled month-close workflow driven by human judgment and a checklist.
- A strong audit trail for changes, overrides, close, and reopen actions.

## Product principles

- Separate operational state from financial state.
- Never overwrite original financial estimates with settled values.
- Keep month close a business decision, not a hidden automatic event.
- Prefer append-only history for sensitive financial changes.
- Make exceptions visible instead of silently normalizing them.
- Restrict privileged actions such as reopen, historical financial edits, and settings changes.
- Show uncertainty. "Not settled", "Incomplete", and "Unavailable" are better answers than a confident wrong number.

## Scope boundaries

In scope: store access, order import, manual costing, estimate versioning, settlement evidence, reconciliation, month close, reporting, export, and audit.

Explicitly out of scope for this release: listing management, inventory management, supplier purchasing, initiating marketplace refunds, **manager compensation tracking of any kind**, and payroll automation. Manager compensation is decided monthly by the Owner outside this application.

## Information architecture

A sober operations layout: left sidebar for primary navigation, sticky top bar for store selector, month selector, search, imports, and user menu, and one primary scrolling content region.

Top-level navigation:

- Overview
- Orders
- Settlements
- Reconciliation
- Exceptions
- Months
- Reports
- Team
- Settings
- Audit log

Screen inventory is maintained in `walmart-user-stories.md` → "Screen traceability" (UI-01 through UI-16). This navigation list is a grouping proposal over those screens, not a second inventory.

## UX baseline

This should look and behave like a serious internal finance-and-operations tool. Data-dense layouts, compact typography, sticky navigation, visible filters, concise labels, and clear table-to-detail flows.

- Desktop-first density, but every essential workflow must remain usable on a narrow viewport and by keyboard (US-032).
- Permission-aware rendering: controls a user cannot use are hidden or disabled with a reason, and the server enforces the rule regardless.
- Every monetary figure carries its currency, its basis (estimated / settled / snapshot), and its completeness.
- Loading, empty, partial, stale, conflict, and permission states are designed, not improvised.
- Never render fabricated data — no example charts, no placeholder amounts, no implied zero where the value is simply unknown.

### Overview screen (UI-03)

Work queue first, vanity metrics last.

Top: counts that link to filtered views — orders missing cost data, orders pending settlement, reconciliation work, open exceptions, month readiness.

Middle: current-month estimated and settled totals per store, each labeled with basis, currency, and completeness.

Bottom: imports with errors, recent close/reopen activity, and recent historical corrections.

When there is nothing to do, show an explicit all-clear state.

### Orders list (UI-04)

Column groups:

| Group | Columns |
|---|---|
| Identity | Order id, store, SKU, order date, posting period |
| Operational | Fulfillment state, quantity |
| Revenue | Selling amount, currency |
| Costs | Buying cost, warehouse, labels, other cost, cost completeness |
| Financial | Estimated profit, settled-to-date, variance |
| Control | Reconciliation state, accounting month, protection state, exception flag |

The four control dimensions are rendered separately. There is no single "status" column.

### Order detail (UI-05)

Sections, in order:

- Summary header with the four state dimensions shown side by side
- Imported facts, read-only and labeled by source
- Manual cost entry, with remaining edit allowance per field
- Estimate versions, comparable against each other
- Settlement entry ledger — every entry, including later adjustments and refunds
- Variance and reconciliation decision
- Exceptions
- Audit history

### Month workspace (UI-12)

- Month status banner, including "previously closed" where applicable
- Readiness summary with links to blocking records
- Checklist
- Exception list
- Close / reopen controls with mandatory reason
- Snapshot history — every close produces a version, and earlier versions remain readable
- Audit history

## Settlement intent

Uploading evidence and accepting it are two different acts, and the UI must make that unmistakable.

A file is staged and validated with nothing committed. The operator sees how many rows passed, how many failed, and what is unmatched, then decides. Committing a partial batch is allowed but must be a deliberate click, never a consequence of uploading.

Nothing reconciles itself. Small variances may be labeled minor to help triage, but every reconciliation is a person deciding.

## Month close intent

Close is a judgment call supported by evidence, not a rules engine.

1. The month is open; managers work orders and enter costs.
2. Settlement evidence is imported and reconciled where available.
3. A manager marks the month ready.
4. An Owner or Admin reviews the checklist and unresolved exceptions.
5. An Owner or Admin closes the month explicitly, with a note.
6. A snapshot of the approved result is retained.
7. Financial edits to that month become Owner-only, permanently.
8. Reopen is explicit, recorded, and does not erase prior snapshots or relax rule 7.

Correcting a closed month does **not** require reopening it. The Owner edits in place and a new snapshot version is created, leaving the original approved figures reproducible. Reopen exists for substantial rework, not for routine corrections.

Operational work continues in a closed month. Fulfillment updates, assignments, notes, and exception comments are never blocked — only changes that can move a financial figure are.

The checklist content is data, not code. A starting template:

| Checklist item | Required |
|---|:--:|
| Orders for the month reviewed | Yes |
| Manual costs entered or explicitly marked unknown | Yes |
| Settlement evidence imported or absence documented | Yes |
| Settlement differences reviewed | Yes |
| Unmatched settlement entries documented | Yes |
| Manager marked month ready | Yes |
| Owner/Admin reviewed exceptions | Yes |
| Owner/Admin approved close | Yes |

Each item records status, note, actor, and timestamp, and supports an explicit waiver where the item permits one.

## Reporting intent

Reports must answer one clear question each, and say which question they answered.

There are four date bases, and a report names exactly one:

| Basis | Question |
|---|---|
| Order month | What did we sell in March? |
| Posting month | What is in our March books? |
| Settlement event month | What money moved in March? |
| Snapshot | What did we approve for March at close? |

The reports themselves:

- Profit by store by month, on either the order-month or posting-month basis
- Estimated versus settled variance by month
- Refund and settlement activity by **event** month
- Deferred orders — the difference between order month and posting month
- Orders with unresolved exceptions
- Orders missing manual cost data
- Changes affecting previously closed months

Exports carry their store, filters, date basis, currency, and snapshot version with the data.

Three standing constraints:

- Totals from different currencies are never summed without an approved conversion policy.
- Orders with an incomplete cost picture are excluded from totals and counted separately. An unknown cost never enters a sum as zero.
- Every figure displays its basis — estimated, settled, settled-to-date, or snapshot — and its calculation policy version.

## Build phases

Phase ordering follows domain dependency, not UI priority. The authoritative sequence is `walmart-user-stories.md` → "Proposed delivery sequence". Summarised:

1. **Foundation** — authentication, stores, access management, audit foundation.
2. **Order and cost slice** — order import, order list and detail, manual costing, estimate versions, edit limits, concurrency safety.
3. **Settlement evidence** — validated, non-duplicating settlement imports.
4. **Financial review** — reconciliation and exception resolution.
5. **Close and reports** — accounting months, checklist, close/reopen, snapshots, basis-explicit reporting.
6. **Convenience** — exports and the overview work queue.

## Development process

Because much of the build is AI-assisted, the process constrains the AI with stable documents rather than asking it to invent architecture per task.

Document stack, in dependency order:

1. Business rules and scope — `walmart-user-stories.md`
2. Roles and capability matrix — `walmart-user-stories.md`
3. State dimensions — `walmart-user-stories.md`
4. Database schema — `walmart-store-manager-database-schema.md`
5. API and service contracts — `walmart-store-manager-api-service-design.md`
6. Architecture and boundaries — `walmart-store-manager-system-architecture.md`
7. Product vision and UX — this file

Build rules:

1. Freeze business rules before modeling data.
2. Model entities and relationships before defining transitions.
3. Define transitions and permissions before building screens.
4. Generate schema and API code only after the above are approved.
5. Implement imports and reconciliation only after the order-cost model is stable.
6. Add month close last; it depends on everything else.
7. Resolve a story's decision gates, or document the gated subset, before implementing it.

## The failure mode this rebuild exists to prevent

One mutable row trying to mean several things at once. Concretely, these are the specific mistakes that broke the previous version and must not reappear:

- A single `status` column representing fulfillment, refund, reconciliation, and period state.
- One month field doing duty as both "when the sale happened" and "which books it is in", so a late order forces a choice between corrupting a closed period and lying about the sale date.
- A single `walmart_fee` column overwritten from estimate to settled value.
- A settlement model assuming one final payout row per order.
- Month close implemented as a boolean.
- Store setting changes silently rewriting historical order economics.
- Permission checks that exist only in the UI.
- Reports that cannot say which basis, currency, or snapshot they used.

Every one of these is now blocked by a specific rule in the authoritative document. If an implementation proposal reintroduces one, that proposal is wrong, regardless of how convenient it is.
