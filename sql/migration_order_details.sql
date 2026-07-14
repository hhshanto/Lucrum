-- Incremental migration for the "Order Details" tab.
-- Run this in the Supabase SQL Editor (after the existing schema.sql has already been applied).

-- ============ NEW COLUMNS ============
alter table orders
  add column if not exists customer_name  text,
  add column if not exists customer_city  text,
  add column if not exists customer_state text;

alter table order_lines
  add column if not exists shipping_revenue numeric not null default 0;

alter table product_costs
  add column if not exists warehouse_cost numeric not null default 0;

-- ============ ORDER DETAILS VIEW (RLS-aware) ============
-- Spreadsheet-style, per order line. Walmart Fees / WFS-Label Fees / Refund Fees /
-- Extra Walmart Service Fees are not included here -- those require the Phase 6
-- settlement report sync and are shown as "--" in the dashboard for now, so
-- total_cost / net_profit / margin_pct below are gross figures.
create view order_details with (security_invoker = on) as
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
  coalesce(pc.warehouse_cost, 0) as warehouse_cost,
  round(
    ol.quantity * coalesce(pc.unit_cost, 0)
    + ol.shipping_revenue
    + ol.quantity * coalesce(pc.warehouse_cost, 0)
  , 2) as total_cost,
  round(
    ol.product_revenue
    - (ol.quantity * coalesce(pc.unit_cost, 0) + ol.shipping_revenue + ol.quantity * coalesce(pc.warehouse_cost, 0))
  , 2) as net_profit,
  case when ol.product_revenue > 0 then round(
    100 * (ol.product_revenue - (ol.quantity * coalesce(pc.unit_cost, 0) + ol.shipping_revenue + ol.quantity * coalesce(pc.warehouse_cost, 0)))
    / ol.product_revenue
  , 2) else 0 end as margin_pct,
  ol.status as order_status
from order_lines ol
join orders o
  on o.store_id = ol.store_id and o.purchase_order_id = ol.purchase_order_id
left join product_costs pc
  on pc.store_id = ol.store_id and pc.sku = ol.sku;
