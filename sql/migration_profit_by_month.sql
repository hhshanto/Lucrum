-- Migration: organize profit by month, and make the Profit tab agree with the
-- Dashboard about uncosted SKUs.
--
-- Two changes, both to how profit is reported:
--
-- 1. profit_by_sku gains a month grain. It was one row per (store, sku) across
--    all time, with no date column at all -- so there was nothing for a Period
--    filter to filter on. It now groups by (store, sku, month), and the app rolls
--    those rows back up per SKU when you pick "All time".
--
-- 2. monthly_profit gets the "no known cost -> no invented profit" rule that
--    profit_by_sku already had. Without it the two views disagreed: a SKU with no
--    buying price was treated as costing $0, so its full revenue landed in the
--    Profit tab as profit while the Dashboard correctly showed 0 for it. On the
--    live data that was SWIFTZAR117 -- Dashboard $404.00 vs Profit tab $453.88,
--    $49.88 of it fiction. Revenue, COGS and fees still count for those lines;
--    only net_profit (and therefore margin) waits for a real cost.
--
-- Both views still count shipped/delivered lines only, and every figure stays net
-- (fees + warehouse included).
--
-- Run in the Supabase SQL Editor AFTER migration_realized_orders.sql.

begin;

drop view if exists profit_by_sku;

create view profit_by_sku with (security_invoker = on) as
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
  -- unit_cost / cost_source are current values, identical across months.
  round(coalesce(max(lc.landed_unit_cost), max(pc.unit_cost)), 4) as unit_cost,
  case when max(lc.landed_unit_cost) is not null then 'landed'
       when max(pc.unit_cost) is not null then 'manual'
       else null end as cost_source,
  date_trunc('month', od.order_date)::date as month
from order_details od
left join sku_landed_cost lc
  on lc.store_id = od.store_id and lc.sku = od.sku
left join product_costs pc
  on pc.store_id = od.store_id and pc.sku = od.sku
where od.counts_in_profit
  and od.order_date is not null
group by od.store_id, od.sku, date_trunc('month', od.order_date);

-- Same cost-precedence rule the Dashboard uses: landed cost from purchases wins,
-- then the manual Products cost; neither = the cost is unknown, not zero.
drop view if exists monthly_profit;

create view monthly_profit with (security_invoker = on) as
with priced as (
  select
    od.*,
    coalesce(lc.landed_unit_cost, pc.unit_cost) is not null as has_cost
  from order_details od
  left join sku_landed_cost lc
    on lc.store_id = od.store_id and lc.sku = od.sku
  left join product_costs pc
    on pc.store_id = od.store_id and pc.sku = od.sku
  where od.order_date is not null
    and od.counts_in_profit
)
select
  store_id,
  date_trunc('month', order_date)::date as month,
  count(distinct customer_order_id) as orders,
  sum(order_qty) as units,
  round(sum(total_selling_price + total_extra_shipping), 2) as revenue,
  round(sum(total_purchase_price), 2) as cogs,
  round(sum(coalesce(walmart_fees, 0) + coalesce(wfs_label_fees, 0)
          + coalesce(refund_fees, 0) + coalesce(extra_service_fees, 0)), 2) as fees,
  round(sum(case when has_cost then net_profit else 0 end), 2) as net_profit,
  case when sum(total_selling_price + total_extra_shipping) > 0
       then round(100 * sum(case when has_cost then net_profit else 0 end)
                  / sum(total_selling_price + total_extra_shipping), 2)
       else 0 end as margin_pct
from priced
group by store_id, date_trunc('month', order_date);

commit;
