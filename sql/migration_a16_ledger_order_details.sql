-- Migration A16: put the Orders tab on the ledger.
--
-- WHY
-- Dashboard and Profit were moved onto the ledger in A2d, but Order Details was
-- left reading the original order_details view -- which still recomputes profit
-- from current state on every read, using sku_landed_cost with no date filter.
-- So the Orders tab has been showing pre-ledger numbers that drift, while the
-- other two show frozen ones. Two sources of truth, disagreeing, which is
-- precisely what this rebuild set out to remove.
--
-- Column names deliberately match the old view so the frontend swap is a
-- one-line change rather than a rewrite.
--
-- WHAT IS DIFFERENT FROM THE OLD VIEW
--   * order_date is the SHIP date, which is when revenue is earned and when
--     Walmart settles. The old view dated everything by order placement.
--   * Costs come from frozen ledger entries, so a line's profit cannot change
--     because stock was bought later.
--   * Third-party labels appear in wfs_label_fees. They are invisible to the
--     old view because they never reach settlement.
--   * Unrealized lines (Created / Cancelled) still appear, with zeros and
--     counts_in_profit = false, so the Pending table keeps working.

create or replace view ledger_order_details with (security_invoker = on) as
with line_entries as (
  select
    store_id,
    source_id,
    sum(amount) filter (where entry_type = 'revenue_product')  as product_revenue,
    sum(amount) filter (where entry_type = 'revenue_shipping') as shipping_revenue,
    -sum(amount) filter (where entry_type = 'cogs')            as cogs,
    -sum(amount) filter (where entry_type = 'warehouse')       as warehouse,
    sum(amount) filter (where entry_type in
      ('return_revenue','return_cogs'))                        as returns,
    min(entry_date)                                            as entry_date
  from ledger_entries
  where source_kind = 'order_line'
  group by store_id, source_id
),
-- Fees are posted per (customer order, sku) rather than per line, because that
-- is the grain Walmart reports them at.
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
    bool_or(source_kind = 'settlement')                     as from_settlement,
    bool_or(source_kind = 'manual')                         as from_workbook
  from ledger_entries
  where entry_type in ('fee_referral','fee_label','fee_label_3p','fee_refund','fee_other')
  group by store_id, split_part(source_id, ':', 3), sku
),
-- Settlement fees key on customer order; manual ones key on the order line.
-- Normalise both to the customer order so they aggregate together.
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
settlement_fees as (
  select
    store_id, customer_order_id, sku,
    fee_referral, fee_label, fee_label_3p, fee_refund, fee_other
  from fee_entries
  where from_settlement
),
-- A sku can appear on more than one line of an order, so a fee keyed at order
-- level has to be split. Proportional to quantity, mirroring the old view, so
-- it is never charged twice.
lines as (
  select
    ol.store_id,
    ol.purchase_order_id,
    ol.line_number,
    ol.sku,
    ol.product_name,
    ol.quantity,
    ol.status,
    o.customer_order_id,
    o.customer_name, o.customer_city, o.customer_state,
    o.order_date as placed_at,
    ol.purchase_order_id || ':' || ol.line_number as source_id,
    case when sum(coalesce(ol.quantity, 0)) over w > 0
         then coalesce(ol.quantity, 0) / sum(coalesce(ol.quantity, 0)) over w
         else 1.0 / count(*) over w end as fee_share
  from order_lines ol
  join orders o
    on o.store_id = ol.store_id and o.purchase_order_id = ol.purchase_order_id
  window w as (partition by ol.store_id, o.customer_order_id, ol.sku)
)
select
  l.store_id,
  coalesce(e.entry_date::timestamptz, l.placed_at)             as order_date,
  l.sku,
  l.product_name,
  l.customer_name, l.customer_city, l.customer_state,
  l.customer_order_id,
  l.quantity                                                   as order_qty,
  case when l.quantity > 0
       then round(coalesce(e.product_revenue, 0) / l.quantity, 2) else 0 end as unit_selling_price,
  case when l.quantity > 0
       then round(coalesce(e.shipping_revenue, 0) / l.quantity, 2) else 0 end as extra_shipping,
  case when l.quantity > 0
       then round(coalesce(e.cogs, 0) / l.quantity, 4) else 0 end as unit_purchase_price,
  round(coalesce(e.product_revenue, 0), 2)                     as total_selling_price,
  round(coalesce(e.shipping_revenue, 0), 2)                    as total_extra_shipping,
  round(coalesce(e.cogs, 0), 2)                                as total_purchase_price,
  round(coalesce(coalesce(sf.fee_referral, mf.fee_referral), 0) * l.fee_share, 2) as walmart_fees,
  -- Third-party labels sit here too: they are a real shipping cost, just one
  -- Walmart never sees.
  round((coalesce(coalesce(sf.fee_label, mf.fee_label), 0)
       + coalesce(coalesce(sf.fee_label_3p, mf.fee_label_3p), 0)) * l.fee_share, 2) as wfs_label_fees,
  round(coalesce(sf.fee_refund, 0) * l.fee_share, 2)           as refund_fees,
  round(coalesce(sf.fee_other, 0) * l.fee_share, 2)            as extra_service_fees,
  round(coalesce(e.warehouse, 0), 2)                           as warehouse_cost,
  round(coalesce(e.cogs, 0) + coalesce(e.warehouse, 0)
        - (coalesce(coalesce(sf.fee_referral, mf.fee_referral), 0)
         + coalesce(coalesce(sf.fee_label, mf.fee_label), 0)
         + coalesce(coalesce(sf.fee_label_3p, mf.fee_label_3p), 0)
         + coalesce(sf.fee_refund, 0) + coalesce(sf.fee_other, 0)) * l.fee_share, 2) as total_cost,
  round(coalesce(e.product_revenue, 0) + coalesce(e.shipping_revenue, 0)
        - coalesce(e.cogs, 0) - coalesce(e.warehouse, 0)
        + coalesce(e.returns, 0)
        + (coalesce(coalesce(sf.fee_referral, mf.fee_referral), 0)
         + coalesce(coalesce(sf.fee_label, mf.fee_label), 0)
         + coalesce(coalesce(sf.fee_label_3p, mf.fee_label_3p), 0)
         + coalesce(sf.fee_refund, 0) + coalesce(sf.fee_other, 0)) * l.fee_share, 2) as net_profit,
  case when coalesce(e.product_revenue, 0) + coalesce(e.shipping_revenue, 0) > 0
       then round(100 * (coalesce(e.product_revenue, 0) + coalesce(e.shipping_revenue, 0)
            - coalesce(e.cogs, 0) - coalesce(e.warehouse, 0) + coalesce(e.returns, 0)
            + (coalesce(coalesce(sf.fee_referral, mf.fee_referral), 0)
             + coalesce(coalesce(sf.fee_label, mf.fee_label), 0)
             + coalesce(coalesce(sf.fee_label_3p, mf.fee_label_3p), 0)
             + coalesce(sf.fee_refund, 0) + coalesce(sf.fee_other, 0)) * l.fee_share)
            / (coalesce(e.product_revenue, 0) + coalesce(e.shipping_revenue, 0)), 2)
       else null end                                           as margin_pct,
  l.status                                                     as order_status,
  case when sf.customer_order_id is not null then 'settlement'
       when mf.customer_order_id is not null then 'workbook (estimate)'
       else 'none' end                                         as fee_source,
  round(coalesce(e.returns, 0), 2)                             as returns,
  (u.source_id is not null)                                    as is_uncertain,
  u.reason                                                     as uncertain_reason,
  -- Only realized lines ever reach the ledger, so presence IS realization.
  (e.source_id is not null)                                    as counts_in_profit
from lines l
left join line_entries e  on e.store_id = l.store_id and e.source_id = l.source_id
left join settlement_fees sf on sf.store_id = l.store_id
                           and sf.customer_order_id = l.customer_order_id and sf.sku = l.sku
left join manual_fees mf  on mf.store_id = l.store_id
                         and mf.customer_order_id = l.customer_order_id and mf.sku = l.sku
left join uncosted_lines u on u.store_id = l.store_id and u.source_id = l.source_id;

insert into schema_migrations (filename, note)
values ('migration_a16_ledger_order_details.sql', 'Orders tab reads the ledger')
on conflict (filename) do nothing;
