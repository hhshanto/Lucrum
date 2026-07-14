-- Migration: only count SHIPPED / DELIVERED orders toward profit.
--
-- THE PROBLEM: every order line counted toward profit regardless of status.
-- A Cancelled line was contributing $21.50 of profit that will never exist, and
-- Created/Acknowledged lines added ~$100 of revenue that hasn't been earned yet
-- (you haven't shipped the goods, and Walmart won't settle them -- so their fees
-- stay $0 and they show fake-high margins).
--
-- THE RULE: profit counts a line only when it is Shipped or Delivered -- the same
-- set Walmart actually pays you for, so it lines up with settlement. Everything
-- else (Created, Acknowledged, Cancelled) is visible in the app but excluded.
-- If a Delivered order is later returned, Walmart flips the status and the line
-- drops out of the counted set on its own.
--
-- order_details gains `counts_in_profit`; profit_by_sku and monthly_profit filter
-- on it. order_details itself still returns EVERY line so the Orders tab can show
-- counted and not-counted side by side.
--
-- Run in the Supabase SQL Editor AFTER migration_dashboard_net_profit.sql.

begin;

create or replace view order_details with (security_invoker = on) as
with settlement_agg as (
  select
    sl.store_id,
    sl.customer_order_id,
    sl.sku,
    coalesce(fcm.category, 'extra_service_fee') as category,
    sum(sl.amount) as amount
  from settlement_lines sl
  left join fee_category_map fcm
    on fcm.store_id = sl.store_id
   and fcm.amount_type = sl.amount_type
  where coalesce(fcm.category, 'extra_service_fee') <> 'ignore'
  group by sl.store_id, sl.customer_order_id, sl.sku, coalesce(fcm.category, 'extra_service_fee')
),
settlement_pivot as (
  select
    store_id,
    customer_order_id,
    sku,
    sum(amount) filter (where category = 'walmart_fee')      as walmart_fees,
    sum(amount) filter (where category = 'wfs_label_fee')     as wfs_label_fees,
    sum(amount) filter (where category = 'refund_fee')        as refund_fees,
    sum(amount) filter (where category = 'extra_service_fee') as extra_service_fees
  from settlement_agg
  group by store_id, customer_order_id, sku
),
lines as (
  select
    ol.store_id, ol.sku, ol.product_name, ol.quantity,
    ol.product_revenue, ol.shipping_revenue, ol.status,
    o.order_date, o.customer_name, o.customer_city, o.customer_state, o.customer_order_id,
    coalesce(lc.landed_unit_cost, pc.unit_cost) as unit_cost,
    pc.warehouse_cost,
    sp.walmart_fees, sp.wfs_label_fees, sp.refund_fees, sp.extra_service_fees,
    (sp.store_id is not null) as has_settlement,
    mf.referral_fee as manual_referral,
    mf.label_fee    as manual_label,
    (mf.store_id is not null) as has_manual,
    case
      when sum(coalesce(ol.quantity, 0)) over w > 0
        then coalesce(ol.quantity, 0) / sum(coalesce(ol.quantity, 0)) over w
      else 1.0 / count(*) over w
    end as fee_share
  from order_lines ol
  join orders o
    on o.store_id = ol.store_id and o.purchase_order_id = ol.purchase_order_id
  left join product_costs pc
    on pc.store_id = ol.store_id and pc.sku = ol.sku
  left join sku_landed_cost lc
    on lc.store_id = ol.store_id and lc.sku = ol.sku
  left join settlement_pivot sp
    on sp.store_id = ol.store_id
   and sp.customer_order_id = o.customer_order_id
   and sp.sku = ol.sku
  left join manual_order_fees mf
    on mf.store_id = ol.store_id
   and mf.customer_order_id = o.customer_order_id
   and mf.sku = ol.sku
  window w as (partition by ol.store_id, o.customer_order_id, ol.sku)
),
alloc as (
  select
    l.*,
    round((case when l.has_settlement then coalesce(l.walmart_fees, 0)
                else -coalesce(l.manual_referral, 0) end) * l.fee_share, 2) as eff_walmart,
    round((case when l.has_settlement then coalesce(l.wfs_label_fees, 0)
                else -coalesce(l.manual_label, 0) end) * l.fee_share, 2) as eff_label,
    round(coalesce(l.refund_fees, 0) * l.fee_share, 2)        as eff_refund,
    round(coalesce(l.extra_service_fees, 0) * l.fee_share, 2) as eff_extra
  from lines l
)
select
  store_id,
  order_date,
  sku,
  product_name,
  customer_name,
  customer_city,
  customer_state,
  customer_order_id,
  quantity as order_qty,
  case when quantity > 0 then round(product_revenue / quantity, 2) else 0 end as unit_selling_price,
  case when quantity > 0 then round(shipping_revenue / quantity, 2) else 0 end as extra_shipping,
  coalesce(unit_cost, 0) as unit_purchase_price,
  round(product_revenue, 2) as total_selling_price,
  round(shipping_revenue, 2) as total_extra_shipping,
  round(quantity * coalesce(unit_cost, 0), 2) as total_purchase_price,
  eff_walmart as walmart_fees,
  eff_label   as wfs_label_fees,
  coalesce(warehouse_cost, 0) as warehouse_cost,
  round(
    quantity * coalesce(unit_cost, 0)
    + quantity * coalesce(warehouse_cost, 0)
    - eff_walmart - eff_label - eff_refund - eff_extra
  , 2) as total_cost,
  round(
    product_revenue + shipping_revenue
    - (quantity * coalesce(unit_cost, 0) + quantity * coalesce(warehouse_cost, 0))
    + eff_walmart + eff_label + eff_refund + eff_extra
  , 2) as net_profit,
  case when (product_revenue + shipping_revenue) > 0 then round(
    100 * (
      product_revenue + shipping_revenue
      - (quantity * coalesce(unit_cost, 0) + quantity * coalesce(warehouse_cost, 0))
      + eff_walmart + eff_label + eff_refund + eff_extra
    ) / (product_revenue + shipping_revenue)
  , 2) else 0 end as margin_pct,
  status as order_status,
  eff_refund as refund_fees,
  eff_extra  as extra_service_fees,
  case when has_settlement then 'settlement'
       when has_manual     then 'manual'
       else 'none' end as fee_source,
  -- Realized only: the same set Walmart actually pays you for.
  (lower(trim(coalesce(status, ''))) in ('shipped', 'delivered')) as counts_in_profit
from alloc;

-- Profit views count realized lines only.
create or replace view profit_by_sku with (security_invoker = on) as
select
  od.store_id,
  od.sku,
  max(od.product_name) as product_name,
  sum(od.order_qty)    as units,
  round(sum(od.total_selling_price + od.total_extra_shipping), 2) as revenue,
  round(sum(od.total_purchase_price), 2) as cogs,
  round(sum(coalesce(od.walmart_fees, 0) + coalesce(od.wfs_label_fees, 0)
          + coalesce(od.refund_fees, 0) + coalesce(od.extra_service_fees, 0)), 2) as fees,
  case
    when coalesce(max(lc.landed_unit_cost), max(pc.unit_cost)) is null then 0::numeric
    else round(sum(od.net_profit), 2)
  end as net_profit,
  round(coalesce(max(lc.landed_unit_cost), max(pc.unit_cost)), 4) as unit_cost,
  case when max(lc.landed_unit_cost) is not null then 'landed'
       when max(pc.unit_cost) is not null then 'manual'
       else null end as cost_source
from order_details od
left join sku_landed_cost lc
  on lc.store_id = od.store_id and lc.sku = od.sku
left join product_costs pc
  on pc.store_id = od.store_id and pc.sku = od.sku
where od.counts_in_profit
group by od.store_id, od.sku;

create or replace view monthly_profit with (security_invoker = on) as
select
  store_id,
  date_trunc('month', order_date)::date as month,
  count(distinct customer_order_id) as orders,
  sum(order_qty) as units,
  round(sum(total_selling_price + total_extra_shipping), 2) as revenue,
  round(sum(total_purchase_price), 2) as cogs,
  round(sum(coalesce(walmart_fees, 0) + coalesce(wfs_label_fees, 0)
          + coalesce(refund_fees, 0) + coalesce(extra_service_fees, 0)), 2) as fees,
  round(sum(net_profit), 2) as net_profit,
  case when sum(total_selling_price + total_extra_shipping) > 0
       then round(100 * sum(net_profit) / sum(total_selling_price + total_extra_shipping), 2)
       else 0 end as margin_pct
from order_details
where order_date is not null
  and counts_in_profit
group by store_id, date_trunc('month', order_date);

commit;
