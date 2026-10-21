-- Migration A7: treat workbook-sourced Walmart charges as estimates.
--
-- WHY
-- The workbooks' "Walmart Fee" and "Buy Label From Walmart" columns are
-- transcribed from Walmart by hand, and the seller reports they are not
-- reliably entered. fee_label_disagreements shows it across 84 lines: the
-- workbook consistently exceeds settlement by 2-3% ($6.03 vs $5.86, $8.05 vs
-- $7.82). That is systematic, so those columns cannot be treated as fact.
--
-- The reliable/unreliable split follows who produced the number:
--     Per Item Cost             the seller paid a supplier        RELIABLE
--     Buy Label From 3rd Party  the seller paid directly          RELIABLE
--     Warehouse                 the seller's own policy           RELIABLE
--     Walmart Fee               copied from Walmart               NOT
--     Buy Label From Walmart    copied from Walmart               NOT
--
-- Settlement remains the only authority for Walmart-side charges. The workbook
-- still fills gaps -- dropping October's fees entirely would overstate profit
-- far more than an imperfect estimate does -- but a line relying on it is now
-- flagged, so the month cannot report a confident margin.
--
-- Before this, October showed 11.66% as though it were known, on fees that were
-- entirely workbook-sourced.

alter table uncosted_lines drop constraint if exists uncosted_lines_reason_check;
alter table uncosted_lines add constraint uncosted_lines_reason_check
  check (reason in ('no_purchase_history','insufficient_qty','estimated_cost',
                    'zero_cost','estimated_fee'));

-- Column names and order are unchanged, so these can be replaced in place --
-- only the filter expressions move.

create or replace view period_completeness with (security_invoker = on) as
with sold as (
  select store_id, date_trunc('month', entry_date)::date as month,
         count(distinct source_id) as lines,
         sum(amount) as revenue
  from ledger_entries
  where entry_type = 'revenue_product'
  group by store_id, date_trunc('month', entry_date)
),
missing as (
  select store_id, date_trunc('month', sale_date)::date as month,
         count(*) filter (where reason in ('no_purchase_history','zero_cost')) as unknown_lines,
         count(*) filter (where reason in ('estimated_cost','estimated_fee'))  as estimated_lines,
         count(*) filter (where reason = 'insufficient_qty')                   as partial_lines,
         count(distinct source_id) as uncosted_lines
  from uncosted_lines
  group by store_id, date_trunc('month', sale_date)
)
select
  s.store_id,
  s.month,
  s.lines,
  coalesce(m.unknown_lines, 0)   as unknown_lines,
  coalesce(m.estimated_lines, 0) as estimated_lines,
  coalesce(m.partial_lines, 0)   as partial_lines,
  coalesce(m.uncosted_lines, 0)  as uncosted_lines,
  round(100.0 * (s.lines - coalesce(m.uncosted_lines, 0)) / nullif(s.lines, 0), 1) as pct_costed,
  round(s.revenue, 2) as revenue
from sold s
left join missing m on m.store_id = s.store_id and m.month = s.month;

create or replace view ledger_sku_monthly with (security_invoker = on) as
with base as (
  select
    store_id, sku, date_trunc('month', entry_date)::date as month,
    sum(amount) filter (where entry_type = 'revenue_product')  as product_revenue,
    sum(amount) filter (where entry_type = 'revenue_shipping') as shipping_revenue,
    -sum(amount) filter (where entry_type = 'cogs')            as cogs,
    -sum(amount) filter (where entry_type = 'warehouse')       as warehouse,
    -sum(amount) filter (where entry_type in
      ('fee_referral','fee_label','fee_label_3p','fee_refund','fee_other')) as fees,
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
         count(*) filter (where reason in ('no_purchase_history','zero_cost')) as unknown_lines,
         count(*) filter (where reason in ('estimated_cost','estimated_fee'))  as estimated_lines,
         count(distinct source_id) as uncosted_lines
  from uncosted_lines
  group by store_id, sku, date_trunc('month', sale_date)
)
select
  b.store_id, b.sku, b.month,
  coalesce(b.units, 0) as units,
  coalesce(b.lines, 0) as lines,
  round(coalesce(b.product_revenue,0) + coalesce(b.shipping_revenue,0), 2) as revenue,
  round(coalesce(b.product_revenue, 0), 2)  as product_revenue,
  round(coalesce(b.shipping_revenue, 0), 2) as shipping_revenue,
  round(coalesce(b.cogs, 0), 2)             as cogs,
  round(coalesce(b.warehouse, 0), 2)        as warehouse,
  round(coalesce(b.fees, 0), 2)             as fees,
  round(coalesce(b.returns, 0), 2)          as returns,
  round(b.net_profit, 2)                    as net_profit,
  coalesce(m.uncosted_lines, 0)             as uncosted_lines,
  coalesce(m.unknown_lines, 0)              as unknown_lines,
  coalesce(m.estimated_lines, 0)            as estimated_lines,
  case
    when coalesce(m.uncosted_lines, 0) > 0 then null
    when coalesce(b.product_revenue,0) + coalesce(b.shipping_revenue,0) > 0
      then round(100 * b.net_profit
           / (coalesce(b.product_revenue,0) + coalesce(b.shipping_revenue,0)), 2)
    else null
  end as margin_pct,
  (coalesce(m.uncosted_lines, 0) = 0) as is_complete
from base b
left join missing m
  on m.store_id = b.store_id and m.sku = b.sku and m.month = b.month;

-- ledger_monthly also missed fee_label_3p in its fees bucket, so third-party
-- labels were reducing net_profit (correctly, since it sums every entry) while
-- not appearing in the fees column. Same column list, so replace in place.
create or replace view ledger_monthly with (security_invoker = on) as
select
  store_id,
  date_trunc('month', entry_date)::date as month,
  round(sum(amount) filter (where entry_type in ('revenue_product','revenue_shipping')), 2) as revenue,
  round(-sum(amount) filter (where entry_type = 'cogs'), 2)      as cogs,
  round(-sum(amount) filter (where entry_type = 'warehouse'), 2) as warehouse,
  round(-sum(amount) filter (where entry_type in
    ('fee_referral','fee_label','fee_label_3p','fee_refund','fee_other')), 2) as fees,
  round(sum(amount) filter (where entry_type in
    ('return_revenue','return_cogs')), 2)                        as returns,
  round(sum(amount), 2)                                          as net_profit
from ledger_entries
group by store_id, date_trunc('month', entry_date);

insert into schema_migrations (filename, note)
values ('migration_a7_estimated_fees.sql', 'workbook Walmart charges are estimates')
on conflict (filename) do nothing;
