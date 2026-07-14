-- Migration: make the Dashboard show NET profit, not gross.
--
-- THE PROBLEM: profit_by_sku was computed straight off order_lines + costs, so it
-- only ever knew revenue - COGS. It ignored Walmart fees AND warehouse cost. For
-- SWIFTZAR107 it reported $42.69 while the seller actually made $24.39:
--     77.69 revenue - 35.00 COGS - 1.50 warehouse - 9.32 referral - 7.48 label
-- The Orders tab was already correct; only the Dashboard disagreed, because it was
-- answering a different (and much less useful) question.
--
-- THE FIX: derive profit_by_sku from order_details, which already carries the
-- effective landed cost, fee allocation, settlement-or-manual fees, and shipping.
-- One source of truth -> Dashboard, Orders and Profit can no longer disagree.
--
-- Columns change (gross_profit -> fees + net_profit), so this drops and recreates
-- the view. Nothing else depends on profit_by_sku -- only the app reads it.
--
-- Kept from before: a SKU with no known cost contributes 0 profit rather than
-- inventing profit from a $0 cost.
--
-- Run in the Supabase SQL Editor AFTER migration_manual_fees.sql.

begin;

drop view if exists profit_by_sku;

create view profit_by_sku with (security_invoker = on) as
select
  od.store_id,
  od.sku,
  max(od.product_name) as product_name,
  sum(od.order_qty)    as units,
  -- Everything the customer paid you excluding tax.
  round(sum(od.total_selling_price + od.total_extra_shipping), 2) as revenue,
  round(sum(od.total_purchase_price), 2) as cogs,
  round(sum(coalesce(od.walmart_fees, 0) + coalesce(od.wfs_label_fees, 0)
          + coalesce(od.refund_fees, 0) + coalesce(od.extra_service_fees, 0)), 2) as fees,
  -- No known cost -> don't invent profit from a $0 cost.
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
group by od.store_id, od.sku;

commit;
