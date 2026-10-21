-- Migration A17: include refunds in ledger_order_details.
--
-- THE BUG
-- A16 summed to 2,876.29 against ledger_summary's 2,166.88 -- out by exactly
-- 709.41, the refund total, with its own returns column reading 0.00.
--
-- Refunds are posted with source_kind = 'settlement', because Walmart's order
-- API still reports refunded lines as Delivered and settlement is the only
-- place a refund is visible at all. But line_entries filtered to
-- source_kind = 'order_line', so every return_revenue entry fell outside it.
--
-- Which is the very thing A16 set out to fix: a view disagreeing with the
-- ledger it claims to read. Worth noting that the discrepancy was caught by
-- summing the new view against ledger_summary rather than by looking at it --
-- the number looked entirely plausible on screen.
--
-- THE FIX
-- Returns key on (customer order, sku), the same grain as fees, so they join
-- the same way. The column list is unchanged, so this can be replaced in place.

create or replace view ledger_order_details with (security_invoker = on) as
with line_entries as (
  select
    store_id,
    source_id,
    sum(amount) filter (where entry_type = 'revenue_product')  as product_revenue,
    sum(amount) filter (where entry_type = 'revenue_shipping') as shipping_revenue,
    -sum(amount) filter (where entry_type = 'cogs')            as cogs,
    -sum(amount) filter (where entry_type = 'warehouse')       as warehouse,
    min(entry_date)                                            as entry_date
  from ledger_entries
  where source_kind = 'order_line'
  group by store_id, source_id
),
-- Refunds arrive on settlement rows keyed by customer order and sku, exactly
-- like fees. source_id is "report_date:transaction:customer_order:sku:amount_type",
-- so position 3 is the customer order.
return_entries as (
  select
    store_id,
    split_part(source_id, ':', 3) as customer_order_id,
    sku,
    sum(amount) as returns
  from ledger_entries
  where entry_type in ('return_revenue', 'return_cogs')
  group by store_id, split_part(source_id, ':', 3), sku
),
fee_entries as (
  select
    store_id,
    split_part(source_id, ':', 3) as customer_order_id,
    sku,
    sum(amount) filter (where entry_type = 'fee_referral')  as fee_referral,
    sum(amount) filter (where entry_type = 'fee_label')     as fee_label,
    sum(amount) filter (where entry_type = 'fee_label_3p')  as fee_label_3p,
    sum(amount) filter (where entry_type = 'fee_refund')    as fee_refund,
    sum(amount) filter (where entry_type = 'fee_other')     as fee_other,
    bool_or(source_kind = 'settlement')                     as from_settlement
  from ledger_entries
  where entry_type in ('fee_referral','fee_label','fee_label_3p','fee_refund','fee_other')
    and source_kind = 'settlement'
  group by store_id, split_part(source_id, ':', 3), sku
),
manual_fees as (
  select
    le.store_id,
    o.customer_order_id,
    le.sku,
    sum(le.amount) filter (where le.entry_type = 'fee_referral') as fee_referral,
    sum(le.amount) filter (where le.entry_type = 'fee_label')    as fee_label,
    sum(le.amount) filter (where le.entry_type = 'fee_label_3p') as fee_label_3p
  from ledger_entries le
  join order_lines ol
    on ol.store_id = le.store_id
   and ol.purchase_order_id = split_part(le.source_id, ':', 1)
   and ol.line_number       = split_part(le.source_id, ':', 2)
  join orders o
    on o.store_id = ol.store_id and o.purchase_order_id = ol.purchase_order_id
  where le.source_kind = 'manual'
    and le.entry_type in ('fee_referral','fee_label','fee_label_3p')
  group by le.store_id, o.customer_order_id, le.sku
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
    coalesce(coalesce(sf.fee_referral, mf.fee_referral), 0) * l.fee_share  as eff_referral,
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
  c.sku,
  c.product_name,
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
        + c.eff_returns
        + c.eff_referral + c.eff_label + c.eff_refund + c.eff_other, 2) as net_profit,
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
  (c.posted_source_id is not null)          as counts_in_profit
from combined c
left join uncosted_lines u on u.store_id = c.store_id and u.source_id = c.source_id;

insert into schema_migrations (filename, note)
values ('migration_a17_order_details_returns.sql', 'refunds included in order details')
on conflict (filename) do nothing;
