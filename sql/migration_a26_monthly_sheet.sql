-- ============================================================================
-- A26: the monthly sheet -- Swiftzar Llc.xlsx, as a view
-- ============================================================================
--
-- WHY
-- The seller's working document is a per-month sheet (Swiftzar Llc.xlsx, tabs
-- Aug-26 / Sep-26 / Oct-26), one row per order line, with a totals row on top.
-- ledger_order_details already carries 16 of its 21 columns under different
-- names -- Order# is customer_order_id, "Per Item Cost" is unit_purchase_price,
-- and the unnamed column J is just shipping (its header is merged into I2:K2).
-- This closes the remaining five.
--
-- Verified against the Oct-26 sheet before writing anything:
--   Total Selling = (Sold Price + Shipping) x Qty      13.72 = (0.88 + 5.98) x 2
--   Net Profit    = Total Selling - Total Cost - Walmart Fee
--                   - Label WM - Label 3P - Warehouse  5.2424
--   ROI           = Net Profit / (all of the above)    0.6183825611
--
-- ROI IS NOT MARGIN
-- The sheet's ROI divides by what was SPENT; margin_pct divides by what was
-- SOLD. They are different numbers -- one real line reads 33.06% margin and
-- 49.4% ROI -- so this adds a column rather than renaming one. The denominator
-- is exactly ledger_order_details.total_cost, which already sums COGS,
-- warehouse and every fee.
--
-- THE LABEL SPLIT
-- The sheet keeps "Buy Label From Walmart" and "Buy Label From 3rd Party" in
-- separate columns; the view lumps both into wfs_label_fees. Rather than
-- re-deriving the split in a second place -- which would eventually disagree,
-- because fee_share, the settlement/workbook fallback and the manual-fee join
-- all have to match exactly -- the two components are added to
-- ledger_order_details itself, beside the total they already sum to.
--
-- Part 1 therefore REPLACES ledger_order_details with the A20 definition plus
-- four appended columns. Nothing existing changes: create or replace permits
-- adding columns at the end, so dependent objects survive untouched.
--
-- Safe to re-run.

-- ----------------------------------------------------------------------------
-- Part 1: ledger_order_details + label split + the order line's own keys.
--
-- purchase_order_id and line_number are exposed so the monthly view can reach
-- orders.raw through the primary key instead of split_part(source_id, ':', n).
-- Commit 6b35746 removed exactly those split_part joins for timing out the
-- Profit tab; re-introducing one here would be repeating a known mistake.
-- ----------------------------------------------------------------------------
create or replace view ledger_order_details with (security_invoker = on) as
with line_entries as (
  select
    store_id, source_id,
    sum(amount) filter (where entry_type = 'revenue_product')  as product_revenue,
    sum(amount) filter (where entry_type = 'revenue_shipping') as shipping_revenue,
    -sum(amount) filter (where entry_type = 'cogs')            as cogs,
    -sum(amount) filter (where entry_type = 'warehouse')       as warehouse,
    min(entry_date)                                            as entry_date
  from ledger_entries
  where source_kind = 'order_line'
  group by store_id, source_id
),
return_entries as (
  select store_id, split_part(source_id, ':', 3) as customer_order_id, sku,
         sum(amount) as returns
  from ledger_entries
  where entry_type in ('return_revenue', 'return_cogs')
  group by store_id, split_part(source_id, ':', 3), sku
),
fee_entries as (
  select
    store_id, split_part(source_id, ':', 3) as customer_order_id, sku,
    sum(amount) filter (where entry_type = 'fee_referral')  as fee_referral,
    sum(amount) filter (where entry_type = 'fee_label')     as fee_label,
    sum(amount) filter (where entry_type = 'fee_label_3p')  as fee_label_3p,
    sum(amount) filter (where entry_type = 'fee_refund')    as fee_refund,
    sum(amount) filter (where entry_type = 'fee_other')     as fee_other
  from ledger_entries
  where entry_type in ('fee_referral','fee_label','fee_label_3p','fee_refund','fee_other')
    and source_kind = 'settlement'
  group by store_id, split_part(source_id, ':', 3), sku
),
manual_fees as (
  select
    le.store_id, c.customer_order_id, le.sku,
    sum(le.amount) filter (where le.entry_type = 'fee_referral') as fee_referral,
    sum(le.amount) filter (where le.entry_type = 'fee_label')    as fee_label,
    sum(le.amount) filter (where le.entry_type = 'fee_label_3p') as fee_label_3p
  from ledger_entries le
  join order_line_costs c
    on c.store_id = le.store_id and c.source_id = le.source_id
  where le.source_kind = 'manual'
    and le.entry_type in ('fee_referral','fee_label','fee_label_3p')
  group by le.store_id, c.customer_order_id, le.sku
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
    coalesce(coalesce(sf.fee_referral, mf.fee_referral), 0) * l.fee_share as eff_referral,
    (coalesce(coalesce(sf.fee_label, mf.fee_label), 0)
     + coalesce(coalesce(sf.fee_label_3p, mf.fee_label_3p), 0)) * l.fee_share as eff_label,
    -- The same two halves again, kept apart. Summing these must reproduce
    -- eff_label exactly; they are split here only so the sheet can show them
    -- in separate columns.
    coalesce(coalesce(sf.fee_label, mf.fee_label), 0)       * l.fee_share as eff_label_wm,
    coalesce(coalesce(sf.fee_label_3p, mf.fee_label_3p), 0) * l.fee_share as eff_label_3p,
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
  c.sku, c.product_name,
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
        + c.eff_returns + c.eff_referral + c.eff_label + c.eff_refund + c.eff_other, 2) as net_profit,
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
  (c.posted_source_id is not null)          as counts_in_profit,
  c.source_id                               as source_id,
  -- ---- appended by A26; everything above is byte-for-byte A20 ----
  round(c.eff_label_wm, 2)                  as label_walmart,
  round(c.eff_label_3p, 2)                  as label_third_party,
  c.purchase_order_id                       as purchase_order_id,
  c.line_number                             as line_number
from combined c
left join uncosted_lines u on u.store_id = c.store_id and u.source_id = c.source_id;

-- ----------------------------------------------------------------------------
-- Part 2: the sheet itself.
--
-- Column order deliberately follows Swiftzar Llc.xlsx left to right, so reading
-- across this table and reading across the spreadsheet feel the same.
--
-- Fees stay NEGATIVE here, unlike the sheet where they are typed as positive
-- costs. Every other view in this app treats a cost as a negative amount, and
-- flipping the sign for one tab would make two screens disagree about the same
-- figure. The UI renders the sign.
-- ----------------------------------------------------------------------------
drop view if exists monthly_sheet;

create view monthly_sheet with (security_invoker = on) as
select
  d.store_id,
  d.source_id,
  d.order_date,                                     -- Date
  rl.image_url,                                     -- Item photo
  d.sku,                                            -- SKU CODE
  d.product_name,                                   -- Description
  d.customer_order_id,                              -- Order#
  d.customer_name,                                  -- Customer Name
  d.order_qty,                                      -- Qty
  d.unit_selling_price,                             -- Sold Price
  d.extra_shipping,                                 -- (merged header I2:K2)
  d.total_selling_price,                            -- Total Selling Price
  d.unit_purchase_price,                            -- Per Item Cost
  d.total_purchase_price,                           -- Total Cost
  d.walmart_fees,                                   -- Walmart Fee
  d.label_walmart,                                  -- Buy Label From Walmart
  d.label_third_party,                              -- Buy Label From 3rd Party
  d.warehouse_cost,                                 -- Warehouse
  d.net_profit,                                     -- Net Profit
  -- ROI over everything spent. total_cost is already COGS + warehouse + fees,
  -- so this is the sheet's denominator without re-adding the parts.
  case when d.total_cost > 0
       then round(100 * d.net_profit / d.total_cost, 2)
       else null end                   as roi_pct,   -- ROI
  d.order_status,                                   -- STATUS (Walmart's, not typed)
  tk.tracking_number,                               -- Tracking
  tk.tracking_url,
  -- Carried through so the sheet can show the same confidence signals as the
  -- Orders tab rather than quietly presenting an estimate as a fact.
  d.total_cost,
  d.margin_pct,
  d.refund_fees,
  d.extra_service_fees,
  d.returns,
  d.fee_source,
  d.is_uncertain,
  d.uncertain_reason,
  d.counts_in_profit
from ledger_order_details d
left join orders o
  on  o.store_id          = d.store_id
  and o.purchase_order_id = d.purchase_order_id
-- The order's raw payload holds the item image and the carrier's tracking, both
-- already synced and neither ever extracted. jsonb_array_elements raises on a
-- non-array, so the shape is checked inside the argument rather than in a WHERE
-- -- a WHERE would be applied only after the function had already run.
left join lateral (
  select ol -> 'item' ->> 'imageUrl' as image_url
  from jsonb_array_elements(
         case when jsonb_typeof(o.raw -> 'orderLines' -> 'orderLine') = 'array'
              then o.raw -> 'orderLines' -> 'orderLine'
              else '[]'::jsonb end) as ol
  where ol ->> 'lineNumber' = d.line_number
  limit 1
) rl on true
left join lateral (
  select
    st -> 'trackingInfo' ->> 'trackingNumber' as tracking_number,
    st -> 'trackingInfo' ->> 'trackingURL'    as tracking_url
  from jsonb_array_elements(
         case when jsonb_typeof(o.raw -> 'orderLines' -> 'orderLine') = 'array'
              then o.raw -> 'orderLines' -> 'orderLine'
              else '[]'::jsonb end) as ol2
  cross join lateral jsonb_array_elements(
         case when jsonb_typeof(ol2 -> 'orderLineStatuses' -> 'orderLineStatus') = 'array'
              then ol2 -> 'orderLineStatuses' -> 'orderLineStatus'
              else '[]'::jsonb end) as st
  where ol2 ->> 'lineNumber' = d.line_number
    and coalesce(st -> 'trackingInfo', 'null'::jsonb) <> 'null'::jsonb
  limit 1
) tk on true;

comment on view monthly_sheet is
  'One row per order line in the column order of Swiftzar Llc.xlsx, plus ROI, '
  'the Walmart/3rd-party label split, the item image and carrier tracking. '
  'Filter by order_date to get a month.';

insert into schema_migrations (filename, note)
values ('migration_a26_monthly_sheet.sql', 'monthly sheet view + label split and ROI')
on conflict (filename) do nothing;
