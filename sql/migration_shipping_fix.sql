-- Migration: fix how outbound shipping is treated in net profit.
--
-- THE BUG: order_details charged shipping against you twice --
--   net_profit = product_revenue - COGS - shipping_revenue - warehouse + fees
-- Shipping the customer paid you was never counted as income, AND was then
-- subtracted as if it were a cost. Net profit was understated by exactly the
-- shipping amount on every line that charged shipping.
--
-- THE FIX: shipping the customer pays is revenue. The seller buys labels through
-- Walmart, so the label cost is deducted by Walmart and arrives in the settlement
-- report (Label Fees) -- it is already inside `fees`, along with the referral fee
-- and the commission Walmart charges on shipping. So:
--   net_profit = product_revenue + shipping_revenue - COGS - warehouse + fees
-- which reconciles to Walmart's "Net payable amount" minus your own costs.
--
-- total_cost drops shipping (it was never a cost here) and keeps COGS +
-- warehouse + Walmart's fees. margin_pct is now measured against everything the
-- customer paid you excluding tax (product + shipping), matching the numerator.
--
-- monthly_profit's revenue follows suit: product + shipping.
--
-- Run in the Supabase SQL Editor AFTER migration_landed_cost_profit.sql.

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
  window w as (partition by ol.store_id, o.customer_order_id, ol.sku)
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
  round(coalesce(walmart_fees, 0) * fee_share, 2) as walmart_fees,
  round(coalesce(wfs_label_fees, 0) * fee_share, 2) as wfs_label_fees,
  coalesce(warehouse_cost, 0) as warehouse_cost,
  -- Costs only: goods + warehouse + Walmart's fees (fees arrive negative, so
  -- subtracting them adds their magnitude). Shipping is revenue, not a cost.
  round(
    quantity * coalesce(unit_cost, 0)
    + quantity * coalesce(warehouse_cost, 0)
    - coalesce(walmart_fees, 0) * fee_share
    - coalesce(wfs_label_fees, 0) * fee_share
    - coalesce(refund_fees, 0) * fee_share
    - coalesce(extra_service_fees, 0) * fee_share
  , 2) as total_cost,
  round(
    product_revenue + shipping_revenue
    - (quantity * coalesce(unit_cost, 0) + quantity * coalesce(warehouse_cost, 0))
    + coalesce(walmart_fees, 0) * fee_share
    + coalesce(wfs_label_fees, 0) * fee_share
    + coalesce(refund_fees, 0) * fee_share
    + coalesce(extra_service_fees, 0) * fee_share
  , 2) as net_profit,
  case when (product_revenue + shipping_revenue) > 0 then round(
    100 * (
      product_revenue + shipping_revenue
      - (quantity * coalesce(unit_cost, 0) + quantity * coalesce(warehouse_cost, 0))
      + coalesce(walmart_fees, 0) * fee_share
      + coalesce(wfs_label_fees, 0) * fee_share
      + coalesce(refund_fees, 0) * fee_share
      + coalesce(extra_service_fees, 0) * fee_share
    ) / (product_revenue + shipping_revenue)
  , 2) else 0 end as margin_pct,
  status as order_status,
  round(coalesce(refund_fees, 0) * fee_share, 2) as refund_fees,
  round(coalesce(extra_service_fees, 0) * fee_share, 2) as extra_service_fees
from lines;

-- Monthly P&L revenue = everything the customer paid you excluding tax.
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
group by store_id, date_trunc('month', order_date);

commit;
