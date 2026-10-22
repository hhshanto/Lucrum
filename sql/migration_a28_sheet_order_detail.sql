-- ============================================================================
-- A28: the fields the order drill-down needs
-- ============================================================================
--
-- WHY
-- Clicking a line in the monthly sheet opens a drawer showing everything behind
-- it. Four fields it wants were not carried through: the destination city and
-- state, and the order line's own keys.
--
-- ledger_order_details has had all four since A26; monthly_sheet simply never
-- selected them. Caught by checking the live view's columns against what the
-- drawer referenced, rather than by the drawer quietly rendering a customer
-- with no address -- undefined reads as empty in JS, so nothing would have
-- looked broken.
--
-- purchase_order_id and line_number are shown as the line's identity. They are
-- also what settlement and the ledger key on, so having them visible makes a
-- disagreement traceable without a database session.
--
-- Safe to re-run.

drop view if exists monthly_sheet;

create view monthly_sheet with (security_invoker = on) as
select
  d.store_id,
  d.source_id,
  d.purchase_order_id,                              -- the line's own keys, as
  d.line_number,                                    -- used by ledger+settlement
  d.order_date,                                     -- Date
  rl.image_url,                                     -- Item photo
  d.sku,                                            -- SKU CODE
  d.product_name,                                   -- Description
  d.customer_order_id,                              -- Order#
  d.customer_name,                                  -- Customer Name
  d.customer_city,                                  -- where it shipped
  d.customer_state,
  d.order_qty,                                      -- Qty
  d.unit_selling_price,                             -- Sold Price
  d.extra_shipping,                                 -- shipping per unit (header merged into I2:K2)
  d.total_extra_shipping,                           -- shipping for the line
  round(coalesce(d.total_selling_price, 0)
        + coalesce(d.total_extra_shipping, 0), 2)   as total_sold,   -- Total Selling Price (col K)
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
  'the Walmart/3rd-party label split, the item image, carrier tracking and the '
  'destination. total_sold is the workbook''s Total Selling Price: product '
  'revenue PLUS shipping. Filter by order_date to get a month.';

insert into schema_migrations (filename, note)
values ('migration_a28_sheet_order_detail.sql', 'sheet carries destination + line keys for drill-down')
on conflict (filename) do nothing;
