-- ============================================================================
-- A27: the sheet's "Total Selling" must include shipping
-- ============================================================================
--
-- WHY
-- A26 mapped the workbook's "Total Selling Price" (column K) to
-- ledger_order_details.total_selling_price. That column is PRODUCT revenue
-- only; shipping lives separately in total_extra_shipping.
--
-- The workbook computes K as (Sold Price + Shipping) x Qty -- verified on a
-- real Oct-26 row: 13.72 = (0.88 + 5.98) x 2. So the two are not the same
-- figure, and A26 was understating the column on every line that carried
-- shipping.
--
-- Caught by a reconciliation that did not add up: October showed $378.79 sold
-- against $219.92 net profit and $1,309.79 spent. Net cannot exceed revenue
-- minus spend. Net was right -- it has always included shipping revenue -- so
-- the revenue column was the one lying.
--
-- Adds total_extra_shipping and a total_sold that sums both halves, and points
-- the sheet at total_sold. Computed from the exact per-line totals rather than
-- (unit_selling_price + extra_shipping) * qty: those unit figures are already
-- rounded to the cent, so multiplying them back up reintroduces the rounding
-- error the stored totals avoid.
--
-- Safe to re-run.

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
  d.extra_shipping,                                 -- shipping per unit (header merged into I2:K2)
  d.total_extra_shipping,                           -- shipping for the line
  -- Column K. Product revenue plus shipping revenue, which is what the
  -- workbook's (Sold Price + Shipping) x Qty actually evaluates to.
  round(coalesce(d.total_selling_price, 0)
        + coalesce(d.total_extra_shipping, 0), 2)   as total_sold,
  d.total_selling_price,                            -- product revenue alone, kept for reconciliation
  d.unit_purchase_price,                            -- Per Item Cost
  d.total_purchase_price,                           -- Total Cost
  d.walmart_fees,                                   -- Walmart Fee
  d.label_walmart,                                  -- Buy Label From Walmart
  d.label_third_party,                              -- Buy Label From 3rd Party
  d.warehouse_cost,                                 -- Warehouse
  d.net_profit,                                     -- Net Profit
  case when d.total_cost > 0
       then round(100 * d.net_profit / d.total_cost, 2)
       else null end                   as roi_pct,   -- ROI
  d.order_status,                                   -- STATUS (Walmart's, not typed)
  tk.tracking_number,                               -- Tracking
  tk.tracking_url,
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
  'total_sold is the workbook''s Total Selling Price: product revenue PLUS '
  'shipping. Filter by order_date to get a month.';

insert into schema_migrations (filename, note)
values ('migration_a27_sheet_total_sold.sql', 'sheet Total Selling includes shipping revenue')
on conflict (filename) do nothing;
