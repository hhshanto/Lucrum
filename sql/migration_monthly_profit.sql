-- Migration: monthly Profit & Loss view for the Profit tab.
--
-- Aggregates the per-line order_details view by store + calendar month, so the
-- app can show a monthly P&L (orders, units, revenue, COGS, fees, net profit,
-- margin). Net profit already has fees folded in per line by order_details, so
-- this is a straight roll-up. Fees stay 0 until settlement data + fee_category_map
-- are in place.
--
-- Run in the Supabase SQL Editor (after migration_fee_scope_and_allocation.sql).
-- security_invoker = on so it respects each user's row-level access, same as
-- the underlying order_details view.

create or replace view monthly_profit with (security_invoker = on) as
select
  store_id,
  date_trunc('month', order_date)::date as month,
  count(distinct customer_order_id) as orders,
  sum(order_qty) as units,
  round(sum(total_selling_price), 2) as revenue,
  round(sum(total_purchase_price), 2) as cogs,
  round(sum(coalesce(walmart_fees, 0) + coalesce(wfs_label_fees, 0)
          + coalesce(refund_fees, 0) + coalesce(extra_service_fees, 0)), 2) as fees,
  round(sum(net_profit), 2) as net_profit,
  case when sum(total_selling_price) > 0
       then round(100 * sum(net_profit) / sum(total_selling_price), 2)
       else 0 end as margin_pct
from order_details
where order_date is not null
group by store_id, date_trunc('month', order_date);
