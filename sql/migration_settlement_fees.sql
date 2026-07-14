-- Incremental migration: wire settlement_lines into order_details so
-- Walmart Fees / WFS-Label Fees / Refund Fees / Extra Service Fees and
-- net profit/margin reflect real fee data.
-- Run this after migration_order_details.sql and migration_settlement.sql.

-- Seed best-guess mappings. "Sale" is the customer payment for the item --
-- it's already counted via order_lines.product_revenue, so it must be
-- ignored here or revenue gets double-counted. "Service Fee" is the one
-- fee type confirmed from the Walmart Transactions screenshot.
-- IMPORTANT: once settlement_lines has real rows, run
--   select distinct amount_type from settlement_lines;
-- and add/adjust rows below so every type is mapped to one of:
--   'walmart_fee', 'wfs_label_fee', 'refund_fee', 'extra_service_fee', 'ignore'
-- (anything left unmapped defaults to 'extra_service_fee').
insert into fee_category_map (amount_type, category) values
  ('Sale', 'ignore'),
  ('Shipping', 'ignore'),
  ('Tax', 'ignore'),
  ('Service Fee', 'walmart_fee')
on conflict (amount_type) do nothing;

-- ============ ORDER DETAILS VIEW (RLS-aware) ============
create or replace view order_details with (security_invoker = on) as
with settlement_agg as (
  select
    sl.store_id,
    sl.customer_order_id,
    sl.sku,
    coalesce(fcm.category, 'extra_service_fee') as category,
    sum(sl.amount) as amount
  from settlement_lines sl
  left join fee_category_map fcm on fcm.amount_type = sl.amount_type
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
)
select
  o.store_id,
  o.order_date,
  ol.sku,
  ol.product_name,
  o.customer_name,
  o.customer_city,
  o.customer_state,
  o.customer_order_id,
  ol.quantity as order_qty,
  case when ol.quantity > 0 then round(ol.product_revenue / ol.quantity, 2) else 0 end as unit_selling_price,
  case when ol.quantity > 0 then round(ol.shipping_revenue / ol.quantity, 2) else 0 end as extra_shipping,
  coalesce(pc.unit_cost, 0) as unit_purchase_price,
  round(ol.product_revenue, 2) as total_selling_price,
  round(ol.shipping_revenue, 2) as total_extra_shipping,
  round(ol.quantity * coalesce(pc.unit_cost, 0), 2) as total_purchase_price,
  round(coalesce(sp.walmart_fees, 0), 2) as walmart_fees,
  round(coalesce(sp.wfs_label_fees, 0), 2) as wfs_label_fees,
  coalesce(pc.warehouse_cost, 0) as warehouse_cost,
  round(
    ol.quantity * coalesce(pc.unit_cost, 0)
    + ol.shipping_revenue
    + ol.quantity * coalesce(pc.warehouse_cost, 0)
    - coalesce(sp.walmart_fees, 0)
    - coalesce(sp.wfs_label_fees, 0)
    - coalesce(sp.refund_fees, 0)
    - coalesce(sp.extra_service_fees, 0)
  , 2) as total_cost,
  round(
    ol.product_revenue
    - (ol.quantity * coalesce(pc.unit_cost, 0) + ol.shipping_revenue + ol.quantity * coalesce(pc.warehouse_cost, 0))
    + coalesce(sp.walmart_fees, 0)
    + coalesce(sp.wfs_label_fees, 0)
    + coalesce(sp.refund_fees, 0)
    + coalesce(sp.extra_service_fees, 0)
  , 2) as net_profit,
  case when ol.product_revenue > 0 then round(
    100 * (
      ol.product_revenue
      - (ol.quantity * coalesce(pc.unit_cost, 0) + ol.shipping_revenue + ol.quantity * coalesce(pc.warehouse_cost, 0))
      + coalesce(sp.walmart_fees, 0)
      + coalesce(sp.wfs_label_fees, 0)
      + coalesce(sp.refund_fees, 0)
      + coalesce(sp.extra_service_fees, 0)
    ) / ol.product_revenue
  , 2) else 0 end as margin_pct,
  ol.status as order_status,
  round(coalesce(sp.refund_fees, 0), 2) as refund_fees,
  round(coalesce(sp.extra_service_fees, 0), 2) as extra_service_fees
from order_lines ol
join orders o
  on o.store_id = ol.store_id and o.purchase_order_id = ol.purchase_order_id
left join product_costs pc
  on pc.store_id = ol.store_id and pc.sku = ol.sku
left join settlement_pivot sp
  on sp.store_id = ol.store_id and sp.customer_order_id = o.customer_order_id and sp.sku = ol.sku;
