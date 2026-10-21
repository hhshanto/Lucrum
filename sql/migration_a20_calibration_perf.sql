-- Migration A20: stop fee_calibration joining on split_part expressions.
--
-- THE BUG
-- The Profit tab fails with "canceling statement due to statement timeout".
--
-- ledger_summary joins fee_calibration, which reaches order_line_costs ->
-- order_lines -> orders through
--     ol.purchase_order_id = split_part(c.source_id, ':', 1)
--     ol.line_number       = split_part(c.source_id, ':', 2)
-- An expression join cannot use an index, so it nested-loops. On its own that
-- would still be tolerable at this size -- but these are security_invoker views,
-- so every candidate row re-evaluates user_has_store(), a security definer
-- function that queries store_access. The row count is small; the function call
-- count is not.
--
-- It looked fine in testing because I measured with the service key, which
-- bypasses RLS entirely. The Dashboard works and Profit does not, and the only
-- difference between them is this join.
--
-- THE FIX
-- order_line_costs is built from workbook rows that already carry the customer
-- order id -- it was simply not stored. Keeping it turns a three-table
-- expression join into a direct indexed one, and removes order_lines and orders
-- from the calibration path completely.

alter table order_line_costs add column if not exists customer_order_id text;

-- One-time backfill. Runs as the migration role, so RLS is not in the way and
-- the expensive join is paid once rather than on every page load.
update order_line_costs c
set customer_order_id = o.customer_order_id
from order_lines ol
join orders o
  on o.store_id = ol.store_id and o.purchase_order_id = ol.purchase_order_id
where ol.store_id = c.store_id
  and ol.purchase_order_id = split_part(c.source_id, ':', 1)
  and ol.line_number       = split_part(c.source_id, ':', 2)
  and c.customer_order_id is null;

create index if not exists order_line_costs_order
  on order_line_costs (store_id, customer_order_id, sku);

-- ============================================================================
-- CALIBRATION, WITHOUT THE EXPRESSION JOIN
-- ============================================================================

create or replace view fee_calibration with (security_invoker = on) as
with settled as (
  select
    sl.store_id,
    sl.customer_order_id,
    sl.sku,
    case m.category
      when 'walmart_fee'   then 'fee_referral'
      when 'wfs_label_fee' then 'fee_label'
    end as entry_type,
    -sum(sl.amount) as settled_amount
  from settlement_lines sl
  join fee_category_map m
    on m.store_id = sl.store_id
   and m.transaction_type = coalesce(sl.transaction_type, '')
   and m.amount_type      = coalesce(sl.amount_type, '')
  where m.category in ('walmart_fee', 'wfs_label_fee')
    and coalesce(sl.transaction_type, '') <> 'Refund'
  group by sl.store_id, sl.customer_order_id, sl.sku, m.category
),
paired as (
  select
    s.store_id,
    s.entry_type,
    case s.entry_type when 'fee_label' then c.label_walmart else c.walmart_fee end as workbook_amount,
    s.settled_amount
  from settled s
  join order_line_costs c
    on c.store_id = s.store_id
   and c.customer_order_id = s.customer_order_id
   and c.sku = s.sku
  where s.entry_type is not null
)
select
  store_id,
  entry_type,
  count(*) as pairs,
  round(percentile_cont(0.10) within group (order by workbook_amount / settled_amount)::numeric, 4) as ratio_p10,
  round(percentile_cont(0.50) within group (order by workbook_amount / settled_amount)::numeric, 4) as ratio_median,
  round(percentile_cont(0.90) within group (order by workbook_amount / settled_amount)::numeric, 4) as ratio_p90,
  round((sum(workbook_amount) / nullif(sum(settled_amount), 0))::numeric, 4) as ratio_aggregate
from paired
where settled_amount > 0 and workbook_amount > 0
group by store_id, entry_type;

-- ============================================================================
-- SAME FIX IN ledger_order_details
-- ============================================================================
-- Its manual_fees CTE used the identical expression join, so the Orders tab was
-- on the same cliff edge -- it simply has not tipped over yet.

create or replace view ledger_order_details with (security_invoker = on) as
with line_entries as (
  select
    store_id, source_id,
    sum(amount) filter (where entry_type = 'revenue_product')  as product_revenue,
    sum(amount) filter (where entry_type = 'revenue_shipping') as shipping_revenue,
    -sum(amount) filter (where entry_type = 'cogs')            as cogs,
    -sum(amount) filter (where entry_type = 'warehouse')       as warehouse,
    min(entry_date)                                            as entry_date
  from ledger_entries
  where source_kind = 'order_line'
  group by store_id, source_id
),
return_entries as (
  select store_id, split_part(source_id, ':', 3) as customer_order_id, sku,
         sum(amount) as returns
  from ledger_entries
  where entry_type in ('return_revenue', 'return_cogs')
  group by store_id, split_part(source_id, ':', 3), sku
),
fee_entries as (
  select
    store_id, split_part(source_id, ':', 3) as customer_order_id, sku,
    sum(amount) filter (where entry_type = 'fee_referral')  as fee_referral,
    sum(amount) filter (where entry_type = 'fee_label')     as fee_label,
    sum(amount) filter (where entry_type = 'fee_label_3p')  as fee_label_3p,
    sum(amount) filter (where entry_type = 'fee_refund')    as fee_refund,
    sum(amount) filter (where entry_type = 'fee_other')     as fee_other
  from ledger_entries
  where entry_type in ('fee_referral','fee_label','fee_label_3p','fee_refund','fee_other')
    and source_kind = 'settlement'
  group by store_id, split_part(source_id, ':', 3), sku
),
-- Manual fees are posted against the order line, and order_line_costs now
-- carries the customer order directly, so this is a plain indexed join.
manual_fees as (
  select
    le.store_id, c.customer_order_id, le.sku,
    sum(le.amount) filter (where le.entry_type = 'fee_referral') as fee_referral,
    sum(le.amount) filter (where le.entry_type = 'fee_label')    as fee_label,
    sum(le.amount) filter (where le.entry_type = 'fee_label_3p') as fee_label_3p
  from ledger_entries le
  join order_line_costs c
    on c.store_id = le.store_id and c.source_id = le.source_id
  where le.source_kind = 'manual'
    and le.entry_type in ('fee_referral','fee_label','fee_label_3p')
  group by le.store_id, c.customer_order_id, le.sku
),
lines as (
  select
    ol.store_id, ol.purchase_order_id, ol.line_number, ol.sku, ol.product_name,
    ol.quantity, ol.status,
    o.customer_order_id, o.customer_name, o.customer_city, o.customer_state,
    o.order_date as placed_at,
    ol.purchase_order_id || ':' || ol.line_number as source_id,
    case when sum(coalesce(ol.quantity, 0)) over w > 0
         then coalesce(ol.quantity, 0) / sum(coalesce(ol.quantity, 0)) over w
         else 1.0 / count(*) over w end as fee_share
  from order_lines ol
  join orders o
    on o.store_id = ol.store_id and o.purchase_order_id = ol.purchase_order_id
  window w as (partition by ol.store_id, o.customer_order_id, ol.sku)
),
combined as (
  select
    l.*,
    e.entry_date, e.product_revenue, e.shipping_revenue, e.cogs, e.warehouse,
    e.source_id as posted_source_id,
    coalesce(coalesce(sf.fee_referral, mf.fee_referral), 0) * l.fee_share as eff_referral,
    (coalesce(coalesce(sf.fee_label, mf.fee_label), 0)
     + coalesce(coalesce(sf.fee_label_3p, mf.fee_label_3p), 0)) * l.fee_share as eff_label,
    coalesce(sf.fee_refund, 0) * l.fee_share as eff_refund,
    coalesce(sf.fee_other, 0)  * l.fee_share as eff_other,
    coalesce(r.returns, 0)     * l.fee_share as eff_returns,
    (sf.customer_order_id is not null) as has_settlement,
    (mf.customer_order_id is not null) as has_workbook
  from lines l
  left join line_entries e   on e.store_id = l.store_id and e.source_id = l.source_id
  left join fee_entries sf   on sf.store_id = l.store_id
                            and sf.customer_order_id = l.customer_order_id and sf.sku = l.sku
  left join manual_fees mf   on mf.store_id = l.store_id
                            and mf.customer_order_id = l.customer_order_id and mf.sku = l.sku
  left join return_entries r on r.store_id = l.store_id
                            and r.customer_order_id = l.customer_order_id and r.sku = l.sku
)
select
  c.store_id,
  coalesce(c.entry_date::timestamptz, c.placed_at) as order_date,
  c.sku, c.product_name,
  c.customer_name, c.customer_city, c.customer_state,
  c.customer_order_id,
  c.quantity as order_qty,
  case when c.quantity > 0 then round(coalesce(c.product_revenue, 0) / c.quantity, 2) else 0 end as unit_selling_price,
  case when c.quantity > 0 then round(coalesce(c.shipping_revenue, 0) / c.quantity, 2) else 0 end as extra_shipping,
  case when c.quantity > 0 then round(coalesce(c.cogs, 0) / c.quantity, 4) else 0 end as unit_purchase_price,
  round(coalesce(c.product_revenue, 0), 2)  as total_selling_price,
  round(coalesce(c.shipping_revenue, 0), 2) as total_extra_shipping,
  round(coalesce(c.cogs, 0), 2)             as total_purchase_price,
  round(c.eff_referral, 2)                  as walmart_fees,
  round(c.eff_label, 2)                     as wfs_label_fees,
  round(c.eff_refund, 2)                    as refund_fees,
  round(c.eff_other, 2)                     as extra_service_fees,
  round(coalesce(c.warehouse, 0), 2)        as warehouse_cost,
  round(coalesce(c.cogs, 0) + coalesce(c.warehouse, 0)
        - (c.eff_referral + c.eff_label + c.eff_refund + c.eff_other), 2) as total_cost,
  round(coalesce(c.product_revenue, 0) + coalesce(c.shipping_revenue, 0)
        - coalesce(c.cogs, 0) - coalesce(c.warehouse, 0)
        + c.eff_returns + c.eff_referral + c.eff_label + c.eff_refund + c.eff_other, 2) as net_profit,
  case when coalesce(c.product_revenue, 0) + coalesce(c.shipping_revenue, 0) > 0
       then round(100 * (coalesce(c.product_revenue, 0) + coalesce(c.shipping_revenue, 0)
            - coalesce(c.cogs, 0) - coalesce(c.warehouse, 0) + c.eff_returns
            + c.eff_referral + c.eff_label + c.eff_refund + c.eff_other)
            / (coalesce(c.product_revenue, 0) + coalesce(c.shipping_revenue, 0)), 2)
       else null end                        as margin_pct,
  c.status                                  as order_status,
  case when c.has_settlement then 'settlement'
       when c.has_workbook  then 'workbook (estimate)'
       else 'none' end                      as fee_source,
  round(c.eff_returns, 2)                   as returns,
  (u.source_id is not null)                 as is_uncertain,
  u.reason                                  as uncertain_reason,
  (c.posted_source_id is not null)          as counts_in_profit,
  c.source_id                               as source_id
from combined c
left join uncosted_lines u on u.store_id = c.store_id and u.source_id = c.source_id;

insert into schema_migrations (filename, note)
values ('migration_a20_calibration_perf.sql', 'drop split_part joins from calibration and order details')
on conflict (filename) do nothing;
