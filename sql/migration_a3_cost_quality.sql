-- Migration A3: distinguish a KNOWN cost from an ESTIMATED one, and stop
-- treating a stored 0.00 as "this product was free".
--
-- WHY
-- After A2d every month reported is_complete = true and a confident margin --
-- 72.56% for September, 81.85% for October -- because the 52 placeholder costs
-- are structurally identical to real ones. The completeness machinery was
-- answering "do we have a cost?" when the question is "is the cost real?".
-- That is worse than the old behaviour: previously uncosted SKUs showed $0
-- profit, which at least looked wrong.
--
-- Separately, 9 genuine product_costs rows hold unit_cost = 0.00. Those are
-- rows nobody filled in, not free products, and a cost of zero reports the
-- entire sale price as profit.
--
-- Three cost states after this migration:
--     known      real purchases, or a hand-entered non-zero cost
--     estimated  a placeholder -- COGS posts, but every total carries "≥"
--     unknown    no cost at all, or a 0.00 row -- no COGS posted
--
-- Run in the Supabase SQL Editor, then re-run: python post_ledger.py

-- ============================================================================
-- 1. FLAG THE PLACEHOLDERS
-- ============================================================================

alter table product_costs
  add column if not exists is_placeholder boolean not null default false;

-- Identified by explicit SKU list, NOT by warehouse_cost = 0. 54 rows have a
-- zero warehouse cost but only 52 are placeholders -- two genuine rows share
-- that trait, so the heuristic alone would have mislabelled them.
update product_costs set is_placeholder = true
where sku in (
  'AP-17768920448','AP-20154414536','Ap-00612461403322','Ap-17699066821',
  'Ap-18254251314','Ap-19637015708','Ap-20254364812','Ap-20370658309',
  'Ap-20569862487','Ap-20611304375','Ap-20789621052','Ap-20836754193',
  'SWIFTZAR110','SWIFTZAR117','SWIFTZAR118','SWIFTZAR119','SWIFTZAR127',
  'SWIFTZAR136','SWIFTZAR137','SWIFTZAR141','SWIFTZAR142','SWIFTZAR151',
  'SWIFTZAR152','SWIFTZAR158','SWIFTZAR160','SWIFTZAR182','SWIFTZAR234',
  'SWIFTZAR292','SWT-634707742484','SWT-634707798764','SWT-634707833564',
  'SWT-634707876004','SWT-634707895852','Sty-15624861392','Sty-15646602723',
  'Sty-17331473824','Sty-18056801993','Sty-1987311721','Sty-20666424737',
  'Sty-20824761202','Sty-21054666326','Sty-7512572904','WA-634707765599',
  'WA-634707972508','ZAR-634707674488','ZAR-634707720741','ZAR-634707734861',
  'ZAR-634707764974','ZAR-634707894299','ZAR-634707943034','ZAR-634707981616',
  'swt-634707836411'
) and warehouse_cost = 0;

-- ============================================================================
-- 2. NEW UNCOSTED REASONS
-- ============================================================================

alter table uncosted_lines drop constraint if exists uncosted_lines_reason_check;
alter table uncosted_lines add constraint uncosted_lines_reason_check
  check (reason in ('no_purchase_history','insufficient_qty','estimated_cost','zero_cost'));

-- ============================================================================
-- 3. VIEWS: SPLIT ESTIMATED FROM UNKNOWN
-- ============================================================================
-- Both make a total a lower bound, but they call for different action:
-- "unknown" needs a purchase logged, "estimated" needs the guess replaced.

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
         count(*) filter (where reason = 'estimated_cost')                     as estimated_lines,
         count(*) filter (where reason = 'insufficient_qty')                   as partial_lines,
         count(*) as uncosted_lines
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
  m.store_id, m.month,
  coalesce(v.orders, 0) as orders,
  coalesce(v.units, 0)  as units,
  m.revenue, m.cogs, m.warehouse, m.fees, m.returns, m.net_profit,
  coalesce(c.lines, 0)            as lines,
  coalesce(c.uncosted_lines, 0)   as uncosted_lines,
  coalesce(c.unknown_lines, 0)    as unknown_lines,
  coalesce(c.estimated_lines, 0)  as estimated_lines,
  c.pct_costed,
  (coalesce(c.uncosted_lines, 0) = 0) as is_complete,
  case
    when coalesce(c.uncosted_lines, 0) > 0 then null
    when m.revenue > 0 then round(100 * m.net_profit / m.revenue, 2)
    else null
  end as margin_pct
from ledger_monthly m
left join volume v on v.store_id = m.store_id and v.month = m.month
left join period_completeness c on c.store_id = m.store_id and c.month = m.month;

create or replace view ledger_sku_monthly with (security_invoker = on) as
with base as (
  select
    store_id, sku, date_trunc('month', entry_date)::date as month,
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
         count(*) filter (where reason in ('no_purchase_history','zero_cost')) as unknown_lines,
         count(*) filter (where reason = 'estimated_cost')                     as estimated_lines,
         count(*) as uncosted_lines
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

insert into schema_migrations (filename, note)
values ('migration_a3_cost_quality.sql', 'known / estimated / unknown cost states')
on conflict (filename) do nothing;
