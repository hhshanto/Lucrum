-- Migration: feed Landed Unit Cost (from purchases) into profit / COGS.
--
-- Cost precedence per SKU:
--   1. LANDED cost from purchases -- weighted average of
--      (qty * unit_cost + sales_tax + shipping) / qty across all its purchases.
--   2. Manual cost typed on the Products tab (product_costs.unit_cost) -- fallback
--      when the SKU has no purchases logged.
--   3. Neither -> treated as 0 and flagged in the UI as "no cost".
--
-- profit_by_sku now also exposes the EFFECTIVE unit_cost and a cost_source
-- ('landed' | 'manual' | null) so the dashboard can show where the cost came from.
--
-- This is a no-op on your numbers until purchases exist for a SKU; once they do,
-- that SKU's COGS switches to the true all-in landed cost automatically.
--
-- Run in the Supabase SQL Editor AFTER migration_purchase_columns.sql.

begin;

-- ============ 1. Weighted-average landed cost per SKU ============
create or replace view sku_landed_cost with (security_invoker = on) as
select
  store_id,
  sku,
  round(
    sum(quantity * unit_cost + coalesce(sales_tax, 0) + coalesce(shipping, 0))
    / nullif(sum(quantity), 0)
  , 4) as landed_unit_cost
from purchases
group by store_id, sku;

-- ============ 2. profit_by_sku: landed cost, falling back to manual ============
create or replace view profit_by_sku with (security_invoker = on) as
select
  ol.store_id,
  ol.sku,
  max(ol.product_name)                              as product_name,
  sum(ol.quantity)                                  as units,
  round(sum(ol.product_revenue), 2)                 as revenue,
  round(sum(ol.quantity * coalesce(lc.landed_unit_cost, pc.unit_cost, 0)), 2) as cogs,
  -- If no cost is known for this SKU, don't invent profit from a $0 cost:
  -- report 0 so uncosted SKUs never inflate the dashboard's profit total.
  case
    when coalesce(max(lc.landed_unit_cost), max(pc.unit_cost)) is null then 0::numeric
    else round(sum(ol.product_revenue)
               - sum(ol.quantity * coalesce(lc.landed_unit_cost, pc.unit_cost, 0)), 2)
  end as gross_profit,
  round(coalesce(max(lc.landed_unit_cost), max(pc.unit_cost)), 4) as unit_cost,
  case when max(lc.landed_unit_cost) is not null then 'landed'
       when max(pc.unit_cost) is not null then 'manual'
       else null end as cost_source
from order_lines ol
left join product_costs pc
  on pc.store_id = ol.store_id and pc.sku = ol.sku
left join sku_landed_cost lc
  on lc.store_id = ol.store_id and lc.sku = ol.sku
group by ol.store_id, ol.sku;

-- ============ 3. order_details: same cost precedence ============
-- Columns are unchanged (so monthly_profit, which reads this view, is unaffected);
-- only the unit-cost source changes inside the `lines` CTE.
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
  round(
    quantity * coalesce(unit_cost, 0)
    + shipping_revenue
    + quantity * coalesce(warehouse_cost, 0)
    - coalesce(walmart_fees, 0) * fee_share
    - coalesce(wfs_label_fees, 0) * fee_share
    - coalesce(refund_fees, 0) * fee_share
    - coalesce(extra_service_fees, 0) * fee_share
  , 2) as total_cost,
  round(
    product_revenue
    - (quantity * coalesce(unit_cost, 0) + shipping_revenue + quantity * coalesce(warehouse_cost, 0))
    + coalesce(walmart_fees, 0) * fee_share
    + coalesce(wfs_label_fees, 0) * fee_share
    + coalesce(refund_fees, 0) * fee_share
    + coalesce(extra_service_fees, 0) * fee_share
  , 2) as net_profit,
  case when product_revenue > 0 then round(
    100 * (
      product_revenue
      - (quantity * coalesce(unit_cost, 0) + shipping_revenue + quantity * coalesce(warehouse_cost, 0))
      + coalesce(walmart_fees, 0) * fee_share
      + coalesce(wfs_label_fees, 0) * fee_share
      + coalesce(refund_fees, 0) * fee_share
      + coalesce(extra_service_fees, 0) * fee_share
    ) / product_revenue
  , 2) else 0 end as margin_pct,
  status as order_status,
  round(coalesce(refund_fees, 0) * fee_share, 2) as refund_fees,
  round(coalesce(extra_service_fees, 0) * fee_share, 2) as extra_service_fees
from lines;

commit;
