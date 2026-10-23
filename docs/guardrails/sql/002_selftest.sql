-- Walmart Store Manager - schema self-test
-- Version 1.0
-- guardrail:selfexempt -- detection logic necessarily names forbidden patterns.
--
-- These invariants cannot be expressed as constraints because they are
-- statements ABOUT the schema rather than about rows. They inspect the
-- catalog and report anything that drifted.
--
-- Run in CI after migrations:
--     psql "$DATABASE_URL" -f 002_selftest.sql
--
-- Exit behaviour: guardrails.assert_clean() raises if any violation exists,
-- which makes psql exit non-zero and fails the build.

begin;

create schema if not exists guardrails;

create or replace function guardrails.verify()
returns table (invariant text, object text, detail text)
language plpgsql
as $$
begin

  -- -------------------------------------------------------------------------
  -- DB-03  No binary floating point anywhere near money.
  --        float8/float4 silently lose cents. Money is numeric, always.
  -- -------------------------------------------------------------------------
  return query
    select 'DB-03'::text,
           (c.table_name || '.' || c.column_name)::text,
           ('monetary column uses ' || c.data_type)::text
      from information_schema.columns c
     where c.table_schema = 'public'
       and c.data_type in ('double precision', 'real')
       and (c.column_name ~ '(cost|fee|amount|price|profit|total|revenue|recovered)'
            or c.column_name like '%_roi');

  -- -------------------------------------------------------------------------
  -- DB-04  No currency column carries a default. An amount whose currency
  --        could not be determined must be rejected, never assumed USD.
  -- -------------------------------------------------------------------------
  return query
    select 'DB-04'::text,
           (c.table_name || '.' || c.column_name)::text,
           ('currency column has default ' || c.column_default)::text
      from information_schema.columns c
     where c.table_schema = 'public'
       and c.column_name like '%currency_code'
       and c.column_default is not null;

  -- -------------------------------------------------------------------------
  -- DB-05  The edit limit is undecided policy data, not a schema default.
  --        BR-06 leaves the number to configuration; a column default would
  --        quietly reintroduce a hardcoded limit.
  -- -------------------------------------------------------------------------
  return query
    select 'DB-05'::text,
           'cost_edit_policies.max_edits'::text,
           ('has column default ' || c.column_default)::text
      from information_schema.columns c
     where c.table_schema = 'public'
       and c.table_name = 'cost_edit_policies'
       and c.column_name = 'max_edits'
       and c.column_default is not null;

  -- -------------------------------------------------------------------------
  -- DB-06  The posting period is a decision, not a derivation. If it becomes
  --        generated or trigger-maintained from order_date, deferred postings
  --        are destroyed and late orders start corrupting closed months.
  -- -------------------------------------------------------------------------
  return query
    select 'DB-06'::text,
           'orders.accounting_month_id'::text,
           'is a generated column; posting must be an explicit decision'::text
      from information_schema.columns c
     where c.table_schema = 'public'
       and c.table_name = 'orders'
       and c.column_name = 'accounting_month_id'
       and c.is_generated <> 'NEVER';

  return query
    select 'DB-06'::text,
           ('trigger ' || t.tgname)::text,
           'trigger on orders mentions accounting_month_id and order_date'::text
      from pg_trigger t
      join pg_class cl on cl.oid = t.tgrelid
      join pg_proc p on p.oid = t.tgfoid
     where cl.relname = 'orders'
       and not t.tgisinternal
       and p.prosrc ~ 'accounting_month_id'
       and p.prosrc ~ 'order_date';

  -- -------------------------------------------------------------------------
  -- DB-07  Forbidden tables. Each represents a rule someone decided to work
  --        around rather than follow.
  -- -------------------------------------------------------------------------
  return query
    select 'DB-07'::text,
           t.table_name::text,
           case
             when t.table_name ~ '(order_item|order_line|line_item)'
               then 'BR-02: one SKU per order, no line items'
             when t.table_name ~ '(compensation|payroll|manager_payment)'
               then 'BR-09: compensation is out of scope'
             when t.table_name ~ '(fx_rate|exchange_rate|currency_conversion)'
               then 'BR-12: no currency conversion in this release'
             else 'forbidden table'
           end::text
      from information_schema.tables t
     where t.table_schema = 'public'
       and t.table_type = 'BASE TABLE'
       and t.table_name ~ '(order_item|order_line|line_item|compensation|payroll|manager_payment|fx_rate|exchange_rate|currency_conversion)';

  -- -------------------------------------------------------------------------
  -- DB-08  No combined order status, in any table or view. This is the single
  --        highest-risk regression: four lookups feel tedious, so someone adds
  --        one convenient column. That column is how the previous system died.
  -- -------------------------------------------------------------------------
  return query
    select 'DB-08'::text,
           (c.table_name || '.' || c.column_name)::text,
           'combined order status column is forbidden; four dimensions stay separate'::text
      from information_schema.columns c
     where c.table_schema = 'public'
       and (c.column_name = 'order_status'
            or (c.table_name = 'orders' and c.column_name in ('status', 'status_code', 'state')));

  -- -------------------------------------------------------------------------
  -- DB-08b  Reconciliation and refund values must not appear in a fulfillment
  --         check constraint.
  -- -------------------------------------------------------------------------
  return query
    select 'DB-08'::text,
           ('constraint ' || con.conname)::text,
           'fulfillment constraint admits a reconciliation or refund value'::text
      from pg_constraint con
      join pg_class cl on cl.oid = con.conrelid
     where con.contype = 'c'
       and pg_get_constraintdef(con.oid) ~ 'fulfillment_status'
       and pg_get_constraintdef(con.oid) ~ '(settlement_pending|reconciled|refunded)';

  -- -------------------------------------------------------------------------
  -- DB-12  External identifiers are unique per store. A global unique index on
  --        walmart_order_id breaks the moment two stores share an identifier.
  -- -------------------------------------------------------------------------
  return query
    select 'DB-12'::text,
           ('index ' || i.indexname)::text,
           'external identifier is globally unique; it must be scoped to store_id'::text
      from pg_indexes i
     where i.schemaname = 'public'
       and i.indexdef ~ 'UNIQUE'
       and i.indexdef ~ '\((walmart_order_id|external_batch_id)\)';

  -- -------------------------------------------------------------------------
  -- DB-15  Unknown must remain representable. A NOT NULL on a cost component
  --        forces a zero to be written where the truth is "we do not know".
  -- -------------------------------------------------------------------------
  return query
    select 'DB-15'::text,
           (c.table_name || '.' || c.column_name)::text,
           'cost component is NOT NULL; unknown must stay representable'::text
      from information_schema.columns c
     where c.table_schema = 'public'
       and c.table_name = 'order_cost_profiles'
       and c.column_name in (
             'buying_cost_per_unit', 'buying_cost_total',
             'third_party_label_cost', 'other_cost',
             'warehouse_cost_override', 'warehouse_cost_effective')
       and c.is_nullable = 'NO';

  -- -------------------------------------------------------------------------
  -- DB-16  Profit must not be computed in the database. profit-v1 lives in the
  --        domain layer so its version can be stamped and audited. A generated
  --        column would be a second, unversioned implementation.
  -- -------------------------------------------------------------------------
  return query
    select 'DB-16'::text,
           (c.table_name || '.' || c.column_name)::text,
           'profit is a generated column; profit-v1 belongs in the domain layer'::text
      from information_schema.columns c
     where c.table_schema = 'public'
       and c.column_name ~ '(net_profit|_roi)'
       and c.is_generated <> 'NEVER';

end;
$$;


create or replace function guardrails.assert_clean()
returns void
language plpgsql
as $$
declare
  v record;
  n integer := 0;
begin
  for v in select * from guardrails.verify() loop
    raise warning '[%] % -- %', v.invariant, v.object, v.detail;
    n := n + 1;
  end loop;

  if n > 0 then
    raise exception
      'Schema invariant check failed: % violation(s). See guardrails/INVARIANTS.md', n
      using errcode = 'check_violation';
  end if;

  raise notice 'Schema invariants: all clean.';
end;
$$;

commit;

-- Fail the build if anything drifted.
select guardrails.assert_clean();
