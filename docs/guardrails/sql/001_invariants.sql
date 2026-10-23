-- Walmart Store Manager - enforceable database invariants
-- Version 1.0
-- guardrail:selfexempt -- detection logic necessarily names forbidden patterns.
--
-- Apply AFTER the base schema migrations. These are not optional hardening;
-- they are the mechanical form of rules stated in walmart-user-stories.md.
--
-- Invariant ids (DB-xx) map to guardrails/INVARIANTS.md.
--
-- Design note: everything here fails the WRITE, not a later report. A rule that
-- only produces a warning is a rule that will be ignored under deadline.

begin;

create schema if not exists guardrails;

-- Every block below is guarded by to_regclass. The file is applied from
-- slice 0 onward and each invariant activates as its table appears, so a
-- later slice cannot introduce a table that quietly escapes enforcement.

-- ---------------------------------------------------------------------------
-- DB-01  Fulfillment is one of exactly four values.
--        settlement_pending / reconciled belong to reconciliation.
--        refunded is settlement evidence, not a fulfillment state.
-- ---------------------------------------------------------------------------

do $guard$
begin
  if to_regclass('orders') is not null then
    execute 'alter table orders
  drop constraint if exists orders_fulfillment_status_code_check';
  end if;
end;
$guard$;

do $guard$
begin
  if to_regclass('orders') is not null then
    execute 'alter table orders
  add constraint orders_fulfillment_status_code_check
  check (fulfillment_status_code in (
    ''acknowledged'',
    ''shipped_in_transit'',
    ''delivered'',
    ''cancelled''
  ))';
  end if;
end;
$guard$;

do $guard$
begin
  if to_regclass('order_fulfillment_history') is not null then
    execute 'alter table order_fulfillment_history
  drop constraint if exists ofh_fulfillment_status_code_check';
  end if;
end;
$guard$;

do $guard$
begin
  if to_regclass('order_fulfillment_history') is not null then
    execute 'alter table order_fulfillment_history
  add constraint ofh_fulfillment_status_code_check
  check (fulfillment_status_code in (
    ''acknowledged'',
    ''shipped_in_transit'',
    ''delivered'',
    ''cancelled''
  ))';
  end if;
end;
$guard$;

-- ---------------------------------------------------------------------------
-- DB-02  Closure history is monotonic. BR-08 protection must survive a reopen.
--        Without this, an Admin reopening a month silently grants themselves
--        historical edit authority - the exact bug BR-08 exists to prevent.
-- ---------------------------------------------------------------------------

create or replace function guardrails.enforce_closure_history_monotonic()
returns trigger
language plpgsql
as $$
begin
  if old.has_ever_been_closed = true and new.has_ever_been_closed = false then
    raise exception
      'DB-02 violated: has_ever_been_closed cannot be reset to false (month %)',
      old.id
      using errcode = 'check_violation';
  end if;

  if new.close_count < old.close_count then
    raise exception
      'DB-02 violated: close_count cannot decrease (month %)', old.id
      using errcode = 'check_violation';
  end if;

  return new;
end;
$$;

do $guard$
begin
  if to_regclass('accounting_months') is not null then
    execute 'drop trigger if exists trg_closure_history_monotonic on accounting_months';
  end if;
end;
$guard$;

do $guard$
begin
  if to_regclass('accounting_months') is not null then
    execute 'create trigger trg_closure_history_monotonic
  before update on accounting_months
  for each row
  execute function guardrails.enforce_closure_history_monotonic()';
  end if;
end;
$guard$;

-- ---------------------------------------------------------------------------
-- DB-09 / DB-10  Append-only tables.
--
--   Insert-only, no exceptions:
--     order_cost_field_edits, order_estimate_versions, order_posting_history,
--     order_reconciliation_decisions, settlement_entries, month_actions,
--     approvals, audit_events
--
--   Insert-only with one permitted mutation:
--     month_close_snapshots  -> superseded_by_id may be set once
--     settlement_entries     -> handled below, supersession only
-- ---------------------------------------------------------------------------

create or replace function guardrails.reject_mutation()
returns trigger
language plpgsql
as $$
begin
  raise exception
    'DB-09 violated: % is append-only; % is not permitted',
    tg_table_name, tg_op
    using errcode = 'check_violation';
end;
$$;

do $$
declare
  t text;
  append_only text[] := array[
    'order_cost_field_edits',
    'order_estimate_versions',
    'order_posting_history',
    'order_reconciliation_decisions',
    'month_actions',
    'approvals',
    'audit_events'
  ];
begin
  foreach t in array append_only loop
    if to_regclass(t) is not null then
      execute format('drop trigger if exists trg_append_only on %I', t);
      execute format(
        'create trigger trg_append_only
           before update or delete on %I
           for each row execute function guardrails.reject_mutation()', t);
    end if;
  end loop;
end;
$$;

-- month_close_snapshots: immutable except superseded_by_id, never deletable.

create or replace function guardrails.snapshot_immutable()
returns trigger
language plpgsql
as $$
begin
  if tg_op = 'DELETE' then
    raise exception 'DB-10 violated: close snapshots are never deleted'
      using errcode = 'check_violation';
  end if;

  if old.superseded_by_id is not null
     and new.superseded_by_id is distinct from old.superseded_by_id then
    raise exception 'DB-10 violated: superseded_by_id is write-once'
      using errcode = 'check_violation';
  end if;

  if row(new.*) is distinct from row(old.*)
     and new.superseded_by_id is not distinct from old.superseded_by_id then
    raise exception
      'DB-10 violated: close snapshot % is immutable; only superseded_by_id may be set',
      old.id
      using errcode = 'check_violation';
  end if;

  return new;
end;
$$;

do $guard$
begin
  if to_regclass('month_close_snapshots') is not null then
    execute 'drop trigger if exists trg_snapshot_immutable on month_close_snapshots';
  end if;
end;
$guard$;

do $guard$
begin
  if to_regclass('month_close_snapshots') is not null then
    execute 'create trigger trg_snapshot_immutable
  before update or delete on month_close_snapshots
  for each row
  execute function guardrails.snapshot_immutable()';
  end if;
end;
$guard$;

-- settlement_entries: source evidence. Corrections supersede, never overwrite.

create or replace function guardrails.settlement_entry_supersede_only()
returns trigger
language plpgsql
as $$
begin
  if tg_op = 'DELETE' then
    raise exception 'DB-09 violated: settlement entries are never deleted'
      using errcode = 'check_violation';
  end if;

  if new.settled_revenue_amount    is distinct from old.settled_revenue_amount
  or new.settled_walmart_fee       is distinct from old.settled_walmart_fee
  or new.settled_walmart_label_cost is distinct from old.settled_walmart_label_cost
  or new.settled_other_amount      is distinct from old.settled_other_amount
  or new.currency_code             is distinct from old.currency_code
  or new.event_date                is distinct from old.event_date then
    raise exception
      'DB-09 violated: settlement entry % amounts are immutable; insert a superseding entry',
      old.id
      using errcode = 'check_violation';
  end if;

  return new;
end;
$$;

do $guard$
begin
  if to_regclass('settlement_entries') is not null then
    execute 'drop trigger if exists trg_settlement_entry_supersede_only on settlement_entries';
  end if;
end;
$guard$;

do $guard$
begin
  if to_regclass('settlement_entries') is not null then
    execute 'create trigger trg_settlement_entry_supersede_only
  before update or delete on settlement_entries
  for each row
  execute function guardrails.settlement_entry_supersede_only()';
  end if;
end;
$guard$;

-- ---------------------------------------------------------------------------
-- DB-11  Currency codes are ISO-4217 shaped. No defaults anywhere (see DB-04).
-- ---------------------------------------------------------------------------

do $$
declare
  r record;
begin
  for r in
    select c.table_name, c.column_name
    from information_schema.columns c
    where c.table_schema = 'public'
      and c.column_name like '%currency_code'
  loop
    execute format(
      'alter table %I drop constraint if exists %I',
      r.table_name, r.table_name || '_' || r.column_name || '_fmt');
    execute format(
      'alter table %I add constraint %I check (%I is null or %I ~ ''^[A-Z]{3}$'')',
      r.table_name,
      r.table_name || '_' || r.column_name || '_fmt',
      r.column_name, r.column_name);
  end loop;
end;
$$;

-- ---------------------------------------------------------------------------
-- DB-13  Calendar sanity on accounting periods.
-- ---------------------------------------------------------------------------

do $guard$
begin
  if to_regclass('accounting_months') is not null then
    execute 'alter table accounting_months
  drop constraint if exists accounting_months_month_range';
  end if;
end;
$guard$;

do $guard$
begin
  if to_regclass('accounting_months') is not null then
    execute 'alter table accounting_months
  add constraint accounting_months_month_range
  check (month between 1 and 12)';
  end if;
end;
$guard$;

-- ---------------------------------------------------------------------------
-- DB-14  A settlement entry linked to an order must share that order's store.
--        A cross-store link is corruption, not a reconciliation exception.
-- ---------------------------------------------------------------------------

create or replace function guardrails.enforce_settlement_store_match()
returns trigger
language plpgsql
as $$
declare
  batch_store uuid;
  order_store uuid;
begin
  if new.order_id is null then
    return new;
  end if;

  select b.store_id into batch_store
    from settlement_batches b
   where b.id = new.settlement_batch_id;

  select o.store_id into order_store
    from orders o
   where o.id = new.order_id;

  if batch_store is distinct from order_store then
    raise exception
      'DB-14 violated: settlement entry links store % to order in store %',
      batch_store, order_store
      using errcode = 'foreign_key_violation';
  end if;

  return new;
end;
$$;

do $guard$
begin
  if to_regclass('settlement_entries') is not null then
    execute 'drop trigger if exists trg_settlement_store_match on settlement_entries';
  end if;
end;
$guard$;

do $guard$
begin
  if to_regclass('settlement_entries') is not null then
    execute 'create trigger trg_settlement_store_match
  before insert or update on settlement_entries
  for each row
  execute function guardrails.enforce_settlement_store_match()';
  end if;
end;
$guard$;

commit;
