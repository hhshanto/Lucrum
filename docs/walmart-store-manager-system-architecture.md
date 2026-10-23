# Walmart Store Manager Web App System Architecture

**Version:** 1.0  
**Date:** 23 October 2026  
**Status:** Layer boundaries and deployment shape. Non-binding on business rules.

## Authority

`walmart-user-stories.md` is authoritative for business rules, scope, permissions, and state vocabulary. This document describes where code lives and which layer owns which decision. It states no business rules of its own.

## Purpose

This document defines the recommended high-level system architecture for the Walmart Store Manager web app. Its purpose is to clarify the boundaries between the frontend, backend application layer, Walmart integration layer, and database so the implementation stays modular and does not collapse into a tightly coupled prototype.[cite:747][cite:88]

This architecture is designed for an internal business application with financial workflows, reconciliation, month close controls, optional manager assignment, and audit-sensitive changes. Because the app must preserve estimated and settled financial values, support multiple settlement entries per order, and enforce close-period permissions, the architecture should prioritize clean boundaries over speed-hack shortcuts.[cite:88][cite:685][cite:718]

## Direct answer

Yes, the app should have a backend application layer, but that does **not** mean it needs a public external API product. The backend layer is simply the part that owns permissions, business rules, financial workflows, imports, and database writes. Walmart's API is a separate external integration used by that backend, not the thing your frontend should rely on directly.[cite:88][web:754][web:759]

## Recommended architecture

The application should use four main layers:

1. Frontend web app
2. Backend application layer
3. Walmart integration layer
4. Relational database and storage

This structure creates a safe boundary between user interface behavior and business-critical logic. Internal API patterns are commonly used to separate frontend concerns from backend validation and service orchestration, while external APIs are treated as distinct integration surfaces.[web:754][web:758][web:763]

## Architecture overview

| Layer | Responsibility | Should not do |
|---|---|---|
| Frontend | Render screens, capture input, show tables/charts, call backend actions | Own business rules or write directly to database |
| Backend application layer | Validate, authorize, apply workflows, calculate, orchestrate persistence | Contain Walmart-specific parsing details everywhere |
| Walmart integration layer | Import and normalize Walmart data, handle external API/file formats | Decide accounting rules or UI behavior |
| Database | Persist normalized facts, histories, audit trails, settings, reporting views | Contain all business logic in triggers or ad hoc SQL |

## Layer 1: Frontend web app

The frontend should be responsible for the user experience only: dashboards, tables, forms, filters, order details, month-close screens, reconciliation views, and audit-history display. It should never be trusted to enforce edit limits, closed-month rules, or approval permissions because those are business controls, not display preferences.[cite:88][cite:685]

### Frontend responsibilities

- Render authenticated user interface.
- Fetch lists and detail views from backend endpoints or actions.
- Submit commands such as change cost field, create estimate version, mark month ready, close month, or reopen month.
- Display permission-aware controls returned by backend context.
- Show validation errors from backend.
- Offer export triggers and import upload flows.

### Frontend should not do

- Calculate authoritative financial results in isolation.
- Decide whether a user is allowed to bypass edit limits.
- Decide whether a month may be closed.
- Overwrite estimated values with settled values.
- Link settlement rows to orders without backend validation.[cite:88][cite:685]

## Layer 2: Backend application layer

This is the most important layer. It is the boundary between UI requests and domain behavior. Even if the app is built in one monorepo or one full-stack framework, this layer should still exist conceptually and in code structure.[web:758][web:763]

### Backend responsibilities

- Authentication and authorization.
- Store access rules.
- Order retrieval and filtering.
- Cost edit workflow and per-field edit-limit enforcement.[cite:88]
- Estimate version creation and selection.[cite:88]
- Settlement linkage orchestration.[cite:88]
- Reconciliation summary and exception handling.
- Month checklist, close, and reopen workflows.[cite:685]
- Audit event writing.[cite:718][cite:212]
- Reporting aggregation and export preparation.

### Backend should expose

The backend can expose this logic through:

- REST endpoints,
- server actions,
- RPC-style handlers,
- controller methods,
- or another internal app interface.

The exact transport is less important than the separation of responsibilities. The real requirement is a clean application boundary, not a fancy public developer API.[web:751][web:753][web:763]

### Backend should not do

- Embed Walmart parsing logic all over unrelated services.
- Let controllers perform raw SQL and business rules together.
- Put all workflow behavior into one generic order update route.
- Depend on frontend code to enforce permissions.[cite:88][cite:685]

## Layer 3: Walmart integration layer

This is separate from the internal backend workflow layer. Its job is to communicate with Walmart or Walmart-sourced files and translate outside data into your internal model.

### Walmart integration responsibilities

- Pull Walmart orders when API access is available.
- Ingest Walmart settlements, payouts, or exported files.
- Normalize Walmart payloads into internal import records.
- Map external identifiers to internal stores and orders.
- Detect import errors, duplicates, or unmatched items.
- Preserve raw source rows where needed for troubleshooting.

### Walmart integration should not do

- Decide if an order is reconciled.
- Decide if the month may be closed.
- Change manual operational cost values directly.
- Replace your internal accounting logic.

This separation matters because external marketplace data often arrives in shapes that do not map cleanly to your internal business workflow. Settlement reconciliation often requires matching external line items to internal orders and later financial events rather than assuming one simple transaction path.[web:736][web:740][web:743]

## Layer 4: Database and storage

A relational database should store the application's system of record. The database should persist normalized business entities, immutable histories where required, import traces, and reporting views. Reconciliation and audit-heavy applications generally benefit from explicit relational integrity and transactional behavior.[cite:718][cite:212]

### Database responsibilities

- Store users, roles, stores, versioned settings, and editable policy.
- Store orders and fulfillment history.
- Store cost profiles and cost field edit history.[cite:88]
- Store estimate versions.[cite:88]
- Store settlement batches and settlement entries.[cite:88]
- Store reconciliation summaries, decision history, and exceptions.
- Store accounting months, checklist results, and immutable close snapshots.[cite:685]
- Store audit events and approvals.[cite:718]
- Support reporting views.

### Database should not do

- Become the only place where business rules exist.
- Rely on large trigger chains to decide close/reopen behavior.
- Mix import staging, current order state, and reporting output in one table.
- Let frontend clients connect directly.

## Boundary map

A useful mental model is:

- **Frontend asks** for data or sends user intent.
- **Backend decides** whether the action is valid and how it changes business state.
- **Walmart integration translates** external data into internal records.
- **Database records** the results and history.

This is the cleanest way to keep the app maintainable as more rules are added.

## Recommended request flow examples

## Flow: update order cost field

1. Frontend sends change request for `buying_cost_per_unit`.
2. Backend checks user permission and month status.
3. Backend checks field edit count for that specific field.[cite:88]
4. If limit exceeded, backend rejects or requires approved override.[cite:88]
5. Backend updates cost profile and writes cost field edit history.
6. Backend writes audit event.
7. Frontend shows updated values or validation error.

The frontend should never decide the edit count by itself.

## Flow: import settlement batch

1. User uploads settlement file or starts Walmart sync.
2. Backend integration layer creates import batch.
3. Walmart integration parser normalizes rows.
4. Backend persists settlement batch and settlement entries.
5. Backend attempts deterministic order linkage.
6. Unmatched or suspicious lines go to reconciliation queue.
7. Audit/import logs are written.

Because one order may have multiple settlement entries over time, the importer must support later refunds and adjustments rather than treating settlement as one final record.[cite:88][web:738]

## Flow: close accounting month

1. Frontend submits close action with a note and an `operation_id`.
2. Backend checks actor role: Owner or Admin only.[cite:685]
3. Backend validates checklist state and unresolved exceptions.[cite:685][cite:704]
4. Backend records close note and month action.
5. Backend changes month status to closed and sets the permanent closure flag.
6. Backend writes an immutable close snapshot in the same transaction.
7. Backend activates financial edit restrictions for that month.
8. Backend writes audit and approval evidence.[cite:718][cite:712]

This workflow must be owned by the backend application layer, not by a frontend toggle. Steps 4 through 8 share one transaction, and a retry with the same `operation_id` returns the existing snapshot rather than closing twice.

Reopening reverses step 5 only. It never reverses the permanent closure flag and never removes a snapshot.

## Which layer owns which decision

The recurring failure in this domain is a rule enforced in the wrong layer, or in two layers that disagree.

| Decision | Sole owner |
|---|---|
| Whether a user may perform an action | Backend policy layer, resolving the capability matrix |
| Whether a period is protected from financial edits | Backend, reading the permanent closure flag |
| Whether an edit allowance remains | Backend `OrderCostService`, inside the write transaction, reading configured policy |
| What an order's four state dimensions are | Four different modules; no layer merges them |
| What a monetary value means | Whichever record stores it, together with its currency |
| What a profit figure is | Backend `CalculationService`, the single implementation of `profit-v1` |
| Which period an order posts into | Backend `PeriodService`, under `period-v1`. Never a database derivation |
| What an order's true sale date is | The Walmart source, always. The backend converts it, never overrides it |
| Whether settlement evidence is financially effective | Backend, on explicit operator commit. Never on upload |
| Whether a user may resolve an exception | Backend, from the exception's class, not from UI affordance |
| Credentials and sessions | Backend `AuthService` only. Never the frontend, never the database |
| How a value is displayed | Frontend |

Two rules follow. The frontend may compute a preview, but the server's result is authoritative and the preview must be visibly advisory. The database may enforce integrity, but it must not become the workflow engine.

## Recommended codebase structure

Even in a single repository, the project should keep technical boundaries visible.

### Suggested structure

```text
app/
  frontend/
    pages-or-routes/
    components/
    tables/
    forms/
    state/
  backend/
    controllers/
    policies/
    services/
    repositories/
    dtos/
    validators/
  integrations/
    walmart/
      clients/
      parsers/
      mappers/
      sync-jobs/
  database/
    migrations/
    seeds/
    views/
  shared/
    types/
    constants/
    utils/
```

The names can vary by framework, but the separation should remain.

## Technology-shape options

Several implementation styles can fit this architecture.

### Option A: Single full-stack app with internal backend layer

Example shape:
- Next.js or similar frontend/server framework,
- internal route handlers or server actions,
- PostgreSQL database,
- scheduled jobs for Walmart sync.

Good when:
- one team,
- one web UI,
- moderate complexity,
- fast iteration desired.

### Option B: Separate frontend and backend services

Example shape:
- React/Vue frontend,
- separate backend service,
- PostgreSQL,
- background worker for imports.

Good when:
- strong separation needed,
- multiple clients expected later,
- more infrastructure tolerance exists.

### Recommended fit here

For this app, **Option A is usually enough**, as long as the internal backend/service boundary is preserved. You probably do not need microservices or a public developer platform yet.[cite:747][web:753][web:763]

## Decided stack

**Status: approved, 23 October 2026.** Option A, concretely:

| Layer | Choice |
|---|---|
| Language | Python 3.12+ |
| Web framework | FastAPI |
| UI | Server-rendered Jinja templates with htmx |
| Database | PostgreSQL, plain managed instance |
| DB access | Direct SQL via `psycopg`, with migrations as plain `.sql` files |
| Money type | `decimal.Decimal` in Python, `numeric` in Postgres |
| Tests | `pytest` |
| App code | `src/` |
| Specification | `docs/` |

### Why server-rendered with htmx

The screens are data-dense tables and forms, which is htmx's strongest case. It keeps the project in one language, removes a JavaScript build step, and — most importantly here — makes it structurally hard to reimplement business logic on the client. `profit-v1` must have exactly one implementation; a rich client framework invites a second one for "instant preview" that then quietly diverges from the server.

Where a screen genuinely needs local interaction, use small islands of vanilla JS or Alpine. Any figure a user could act on comes from the server.

### Why plain Postgres rather than Supabase

Authentication is built in-app under `access-v1`, which removes the main reason to adopt Supabase. The guardrail layer depends on direct `psql` access to install triggers, constraints and the schema self-test, so an unobstructed Postgres is the simpler fit.

### Why raw SQL rather than an ORM

The schema carries deliberate constraints an ORM tends to abstract away: append-only tables, partial unique indexes, closure-history triggers, per-store composite uniqueness. Migrations are plain SQL so that `guardrails/sql/001_invariants.sql` composes with them directly rather than fighting a model layer.

An ORM is not forbidden for simple reads. It must not own the financial write paths.

## Background processing

Settlement imports, Walmart sync, monthly summary refreshes, and heavy exports may be better handled asynchronously.

Recommended background jobs:
- order import sync,
- settlement import parsing,
- order-settlement relinking,
- report materialization refresh,
- anomaly or exception detection optional.

This keeps UI requests fast and reduces timeout risk.

## Caching and reporting

The source-of-truth data should stay transactional, but dashboard/reporting views can be optimized separately.

Recommended approach:
- transactional writes to normalized tables,
- reporting queries through SQL views or materialized views,
- selective caching for dashboard summaries.

This helps avoid polluting the core schema with denormalized dashboard-only columns.

## Security model

Because the app includes financial edits and close controls, security should be modeled as part of the architecture, not as an afterthought.

### Security requirements

- in-app email and password authentication with Argon2id or bcrypt cost 12+, per `access-v1`,
- TOTP MFA mandatory for Owner and Admin,
- server-side sessions revocable on sign-out and password change,
- per-account and per-IP rate limiting with lockout,
- role-based access checks in backend, resolved from the single capability matrix,
- store-scoped access filters applied to every query, including direct API requests and deep links,
- audit logging for sensitive mutations,[cite:718][cite:212]
- Owner-only financial edits in any month that has ever been closed,[cite:88]
- Owner/Admin month close and reopen control,[cite:685]
- validated imports with traceability,
- no secrets or customer PII in logs, URLs, exports, or client-visible errors.

## Why this architecture fits your app

This app is not just a simple CRUD admin panel. It contains several overlapping but different workflows:

- operational order handling,
- manual cost maintenance,
- estimate tracking,
- settlement ingestion,
- reconciliation review,
- accounting month control,
- financial auditability.[cite:88][cite:685][cite:686]

If these concerns are not separated, the implementation tends to become a tangled mix of UI assumptions, mutable database fields, and inconsistent workflows. That is exactly the kind of design drift that usually forces rewrites later.

## Anti-patterns to avoid

Avoid these architectural mistakes:

- Frontend directly reading and writing database rows.
- Frontend calling Walmart directly for core business state.
- One giant backend service that handles orders, settlement, months, and users without boundaries.
- One mutable `walmart_fee` field that changes from estimate to settled value.[cite:88]
- Using database triggers as the main workflow engine.
- Treating month close as just a boolean flag, with no snapshot of the approved result.
- Deriving historical-edit protection from a month's current status rather than its closure history.
- Collapsing fulfillment, refund, reconciliation, and period state into one order status field.
- Making reporting depend only on original order month when refund-month reporting is also required.[cite:88]
- Letting the integration layer create accounting periods so an import does not have to fail.
- Letting the integration layer decide posting periods. It normalizes source facts; `PeriodService` decides where they book.
- Treating the transaction date and the posting period as one field.
- Shipping a default for any value the business has not yet decided.
- Implementing the profit formula more than once — typically a backend version plus a convenient frontend copy that quietly diverges.
- Embedding the edit limit as a constant because reading policy felt like overhead.
- Binary floating point anywhere near money.

## Recommended next implementation sequence

1. Choose the stack shape, preferably one full-stack app with strong internal backend/service boundaries.
2. Implement authentication and authorization.
3. Implement stores, versioned settings, policy storage, and month provisioning.
4. Implement orders and manual cost workflows.
5. Implement estimate versioning.
6. Implement Walmart import and settlement ingestion.
7. Implement reconciliation.
8. Implement close, snapshot, and reopen workflows.
9. Add reporting, exports, and background jobs.

This order reflects the actual domain dependency structure rather than just UI priority.[cite:747][cite:88] It matches the delivery sequence in `walmart-user-stories.md`; where they differ, that document wins.

## Final recommendation

The safest architecture for your app is:

- **Frontend UI** for screens and user interaction,
- **Backend application layer** for rules and workflows,
- **Walmart integration layer** for external data ingestion,
- **PostgreSQL-style relational database** for durable business records and audit history.[cite:88][cite:685][cite:718]

That is enough structure to build the app correctly with AI without overengineering it into a distributed system. The key is not whether it is called an API, server action, or route handler. The key is keeping the business boundary clean so operational data, financial history, and accounting controls do not get mixed together again.[web:753][web:758][web:763]
