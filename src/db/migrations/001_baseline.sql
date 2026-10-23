-- 001_baseline
--
-- The cross-cutting spine. Deliberately small: enough that the conventions
-- every later migration inherits are real and tested, and no more.
--
-- Three conventions are established here and never restated:
--   money is numeric, never a floating point type
--   every monetary column carries a currency, and currency has no default
--   every instant is timestamptz, stored in UTC

-- No BEGIN/COMMIT here. The runner wraps every migration in a transaction, so
-- atomicity is guaranteed by the tooling rather than by each author
-- remembering. migrate.py rejects files that try to manage it themselves.

create extension if not exists "pgcrypto";

-- ---------------------------------------------------------------------------
-- stores
--
-- Carries the three values that gate everything downstream. All three are
-- nullable at creation and required before financial reporting is activated,
-- because US-003 AC-02 forbids guessing any of them -- a store with a wrong
-- timezone produces months that are quietly off by a day at the boundary.
-- ---------------------------------------------------------------------------

create table stores (
  id                       uuid primary key default gen_random_uuid(),
  name                     text        not null,
  walmart_store_key        text        not null unique,

  -- period-v1: IANA identifier. No default; see clock.InvalidTimezone.
  timezone                 text,

  -- BR-12: no default. An amount whose currency cannot be determined is
  -- rejected, never assumed to be USD.
  reporting_currency_code  char(3),

  -- BR-14: orders dated before this are imported read-only and excluded from
  -- everything financial. Set explicitly by the Owner.
  managed_from_year        integer,
  managed_from_month       integer,

  is_active                boolean     not null default true,
  created_at               timestamptz not null default now(),
  updated_at               timestamptz not null default now(),

  constraint stores_currency_fmt
    check (reporting_currency_code is null or reporting_currency_code ~ '^[A-Z]{3}$'),
  constraint stores_managed_from_month_range
    check (managed_from_month is null or managed_from_month between 1 and 12),
  constraint stores_managed_from_paired
    check ((managed_from_year is null) = (managed_from_month is null))
);

comment on column stores.timezone is
  'IANA identifier. Required before financial reporting; never defaulted (period-v1).';
comment on column stores.reporting_currency_code is
  'ISO-4217. No column default by design (BR-12).';
comment on column stores.managed_from_year is
  'Scope boundary. Orders before this month are pre_onboarding (BR-14).';

-- ---------------------------------------------------------------------------
-- audit_events
--
-- Append-only. Written from the first endpoint rather than retrofitted, since
-- an audit trail added later has a hole exactly where the early mistakes are.
--
-- operation_id makes writes idempotent: a retry after an uncertain outcome
-- replays safely instead of double-counting (US-014 AC-03).
-- ---------------------------------------------------------------------------

create table audit_events (
  id            uuid        primary key default gen_random_uuid(),
  operation_id  text        not null,
  actor_id      uuid,
  store_id      uuid        references stores(id),
  entity_type   text        not null,
  entity_id     uuid,
  event_type    text        not null,
  before_state  jsonb,
  after_state   jsonb,
  reason        text,
  created_at    timestamptz not null default now(),

  constraint audit_events_operation_id_unique unique (operation_id)
);

create index audit_events_store_created_idx
  on audit_events (store_id, created_at desc);
create index audit_events_entity_idx
  on audit_events (entity_type, entity_id);

comment on table audit_events is
  'Append-only. UPDATE and DELETE are blocked by guardrails/sql/001_invariants.sql.';
comment on column audit_events.operation_id is
  'Idempotency key. A replayed write returns the original result rather than acting twice.';
