-- Migration A19: build Returns from the ledger, not from order status.
--
-- THE BUG
-- The Returns tab is empty despite 21 refunded orders worth 709.41.
--
-- The `returns` view (schema.sql:337) finds refunds by matching
-- order_lines.status against '%return%' or '%refund%'. Walmart's order API
-- never sets those: a refunded line still reports as Delivered. The status
-- simply does not carry the information, so the view was always going to
-- return nothing -- it has been empty since the day it was written, and looked
-- exactly like a seller with no returns.
--
-- Same root cause as the posting bug fixed in A11: a refund is only ever
-- visible in settlement. Both places assumed the order feed would say so.
--
-- THE FIX
-- Read the refunds the ledger already holds. They are dated to the refund, not
-- the sale, so this view also answers "what came back last month" rather than
-- "what was sold that later came back".

create or replace view ledger_returns with (security_invoker = on) as
with refunds as (
  select
    store_id,
    split_part(source_id, ':', 3) as customer_order_id,
    sku,
    min(entry_date)                                           as refund_date,
    sum(amount) filter (where entry_type = 'return_revenue')  as revenue_reversed,
    sum(amount) filter (where entry_type = 'return_cogs')     as cost_reversed,
    sum(amount)                                               as net_impact
  from ledger_entries
  where entry_type in ('return_revenue', 'return_cogs')
  group by store_id, split_part(source_id, ':', 3), sku
),
-- The original sale, for context: what it sold for and when.
sale as (
  select
    ol.store_id,
    o.customer_order_id,
    ol.sku,
    max(ol.product_name)  as product_name,
    sum(ol.quantity)      as quantity,
    sum(ol.product_revenue + coalesce(ol.shipping_revenue, 0)) as original_revenue,
    min(o.order_date)     as sold_at,
    max(ol.status)        as order_status
  from order_lines ol
  join orders o
    on o.store_id = ol.store_id and o.purchase_order_id = ol.purchase_order_id
  group by ol.store_id, o.customer_order_id, ol.sku
)
select
  r.store_id,
  -- Named order_date so the existing renderer needs no change; it is the
  -- REFUND date, which is the date that matters for the P&L.
  r.refund_date::timestamptz          as order_date,
  r.sku,
  s.product_name,
  s.quantity,
  round(-r.revenue_reversed, 2)       as product_revenue,
  r.customer_order_id,
  round(r.net_impact, 2)              as net_impact,
  round(coalesce(s.original_revenue, 0), 2) as original_revenue,
  s.sold_at,
  -- Walmart still reports the line as Delivered, so say what actually happened
  -- rather than echoing a status that is misleading here.
  'Refunded'                          as status
from refunds r
left join sale s
  on s.store_id = r.store_id
 and s.customer_order_id = r.customer_order_id
 and s.sku = r.sku;

insert into schema_migrations (filename, note)
values ('migration_a19_ledger_returns.sql', 'returns read from the ledger')
on conflict (filename) do nothing;
