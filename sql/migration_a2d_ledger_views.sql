-- Migration A2d: reporting views over the ledger.
--
-- These are what the app reads instead of recomputing profit from current state
-- on every page load. Every figure here is sum(amount) over a date range, so it
-- cannot move after the fact.
--
-- Aggregation happens in Postgres. The browser receives tens of rows, not
-- thousands -- which is the real fix for the 1000-row PostgREST cap
-- (EDGE_CASES.md Q1). That cap is no longer theoretical: settlement_lines is
-- 2,290 rows and the ledger is 2,397.
--
-- Run in the Supabase SQL Editor. Nothing is dropped; the legacy views stay
-- until the frontend has fully moved over.

-- ============================================================================
-- PER SKU, PER MONTH
-- ============================================================================
-- Fees carry their sku, so they aggregate alongside revenue and COGS with no
-- allocation step -- the proportional fee_share workaround in order_details
-- (schema.sql:404-424) existed only because that view had to split a per-order
-- fee across lines at read time.

create or replace view ledger_sku_monthly with (security_invoker = on) as
with base as (
  select
    store_id,
    sku,
    date_trunc('month', entry_date)::date as month,
    sum(amount) filter (where entry_type = 'revenue_product')  as product_revenue,
    sum(amount) filter (where entry_type = 'revenue_shipping') as shipping_revenue,
    -sum(amount) filter (where entry_type = 'cogs')            as cogs,
    -sum(amount) filter (where entry_type = 'warehouse')       as warehouse,
    -sum(amount) filter (where entry_type in
      ('fee_referral','fee_label','fee_refund','fee_other'))   as fees,
    sum(amount) filter (where entry_type in
      ('return_revenue','return_cogs'))                        as returns,
    sum(quantity) filter (where entry_type = 'revenue_product') as units,
    count(distinct source_id) filter (where entry_type = 'revenue_product') as lines,
    sum(amount) as net_profit
  from ledger_entries
  where sku is not null
  group by store_id, sku, date_trunc('month', entry_date)
),
missing as (
  select store_id, sku, date_trunc('month', sale_date)::date as month,
         count(*) as uncosted_lines
  from uncosted_lines
  group by store_id, sku, date_trunc('month', sale_date)
)
select
  b.store_id,
  b.sku,
  b.month,
  coalesce(b.units, 0)                                            as units,
  coalesce(b.lines, 0)                                            as lines,
  round(coalesce(b.product_revenue, 0) + coalesce(b.shipping_revenue, 0), 2) as revenue,
  round(coalesce(b.product_revenue, 0), 2)                        as product_revenue,
  round(coalesce(b.shipping_revenue, 0), 2)                       as shipping_revenue,
  round(coalesce(b.cogs, 0), 2)                                   as cogs,
  round(coalesce(b.warehouse, 0), 2)                              as warehouse,
  round(coalesce(b.fees, 0), 2)                                   as fees,
  round(coalesce(b.returns, 0), 2)                                as returns,
  round(b.net_profit, 2)                                          as net_profit,
  coalesce(m.uncosted_lines, 0)                                   as uncosted_lines,
  -- A margin computed over partly-unknown costs is not a fact. Null it rather
  -- than print a confident number (DATA_MODEL.md principle 3).
  case
    when coalesce(m.uncosted_lines, 0) > 0 then null
    when coalesce(b.product_revenue, 0) + coalesce(b.shipping_revenue, 0) > 0
      then round(100 * b.net_profit
           / (coalesce(b.product_revenue, 0) + coalesce(b.shipping_revenue, 0)), 2)
    else null
  end                                                             as margin_pct,
  (coalesce(m.uncosted_lines, 0) = 0)                             as is_complete
from base b
left join missing m
  on m.store_id = b.store_id and m.sku = b.sku and m.month = b.month;

-- ============================================================================
-- PER ORDER LINE
-- ============================================================================
-- Line-level drill-down for the Orders view. Fees are NOT included here: in the
-- ledger a fee belongs to a (customer order, sku), not to a line, and inventing
-- a per-line split is exactly the kind of silent approximation being removed.
-- The UI shows fees at the order level and links out.

create or replace view ledger_order_lines with (security_invoker = on) as
select
  le.store_id,
  le.source_id,
  split_part(le.source_id, ':', 1)                     as purchase_order_id,
  split_part(le.source_id, ':', 2)                     as line_number,
  min(le.entry_date)                                   as entry_date,
  max(le.sku)                                          as sku,
  max(le.quantity) filter (where le.entry_type = 'revenue_product') as quantity,
  round(sum(le.amount) filter (where le.entry_type = 'revenue_product'), 2)  as product_revenue,
  round(sum(le.amount) filter (where le.entry_type = 'revenue_shipping'), 2) as shipping_revenue,
  round(-sum(le.amount) filter (where le.entry_type = 'cogs'), 2)            as cogs,
  round(-sum(le.amount) filter (where le.entry_type = 'warehouse'), 2)       as warehouse,
  round(sum(le.amount), 2)                             as net_before_fees,
  (u.source_id is not null)                            as is_uncosted,
  u.reason                                             as uncosted_reason
from ledger_entries le
left join uncosted_lines u
  on u.store_id = le.store_id and u.source_id = le.source_id
where le.source_kind = 'order_line'
group by le.store_id, le.source_id, u.source_id, u.reason;

-- ============================================================================
-- STORE-LEVEL HEADLINE
-- ============================================================================
-- One row per store per month, carrying its own completeness, so the UI can
-- never render a confident total over partly-unknown data.

create or replace view ledger_summary with (security_invoker = on) as
with volume as (
  select
    store_id,
    date_trunc('month', entry_date)::date as month,
    count(distinct split_part(source_id, ':', 1)) as orders,
    sum(quantity) as units
  from ledger_entries
  where source_kind = 'order_line' and entry_type = 'revenue_product'
  group by store_id, date_trunc('month', entry_date)
)
select
  m.store_id,
  m.month,
  coalesce(v.orders, 0)         as orders,
  coalesce(v.units, 0)          as units,
  m.revenue,
  m.cogs,
  m.warehouse,
  m.fees,
  m.returns,
  m.net_profit,
  coalesce(c.lines, 0)          as lines,
  coalesce(c.uncosted_lines, 0) as uncosted_lines,
  c.pct_costed,
  (coalesce(c.uncosted_lines, 0) = 0) as is_complete,
  case
    when coalesce(c.uncosted_lines, 0) > 0 then null
    when m.revenue > 0 then round(100 * m.net_profit / m.revenue, 2)
    else null
  end as margin_pct
from ledger_monthly m
left join volume v
  on v.store_id = m.store_id and v.month = m.month
left join period_completeness c
  on c.store_id = m.store_id and c.month = m.month;

insert into schema_migrations (filename, note)
values ('migration_a2d_ledger_views.sql', 'reporting views over the ledger')
on conflict (filename) do nothing;
