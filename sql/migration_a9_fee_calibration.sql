-- Migration A9: calibrate workbook fee estimates against settlement.
--
-- WHAT THE DATA SAYS
-- Comparing every line where BOTH a workbook figure and a settled figure exist:
--
--   fee_referral   366 pairs   p10 1.247   median 1.249   p90 1.253
--   fee_label       96 pairs   p10 0.857   median 1.030   p90 1.030
--
-- The referral relationship is not noise. A spread of 0.6% across 366 lines is a
-- formula difference: the workbook bills referral at a rate about 25% higher
-- than Walmart actually charges -- consistent with computing 15% where Walmart
-- charges 12%.
--
-- So an unsettled line should not post the workbook figure at face value. It
-- should post workbook / 1.249, which is a far better estimate of what Walmart
-- will actually charge. The line stays flagged as an estimate, but it is now a
-- calibrated one.
--
-- Labels are messier -- 12% agree exactly, most of the rest sit at 1.030, and a
-- few are far below -- so the band there is genuinely wide and the reporting
-- views expose it rather than pretending to a single number.
--
-- This view recomputes itself as recon files arrive, so the calibration gets
-- better over time and needs no maintenance.

create or replace view fee_calibration with (security_invoker = on) as
with workbook as (
  select
    c.store_id,
    o.customer_order_id,
    c.sku,
    c.label_walmart,
    c.walmart_fee
  from order_line_costs c
  join order_lines ol
    on ol.store_id = c.store_id
   and ol.purchase_order_id = split_part(c.source_id, ':', 1)
   and ol.line_number       = split_part(c.source_id, ':', 2)
  join orders o
    on o.store_id = ol.store_id and o.purchase_order_id = ol.purchase_order_id
),
settled as (
  select
    store_id,
    split_part(source_id, ':', 2) as customer_order_id,
    sku,
    entry_type,
    -sum(amount) as settled_amount
  from ledger_entries
  where source_kind = 'settlement'
    and entry_type in ('fee_referral', 'fee_label')
  group by store_id, split_part(source_id, ':', 2), sku, entry_type
),
paired as (
  select
    s.store_id,
    s.entry_type,
    case s.entry_type when 'fee_label' then w.label_walmart else w.walmart_fee end as workbook_amount,
    s.settled_amount
  from settled s
  join workbook w
    on w.store_id = s.store_id
   and w.customer_order_id = s.customer_order_id
   and w.sku = s.sku
)
select
  store_id,
  entry_type,
  count(*)                                                             as pairs,
  round(percentile_cont(0.10) within group (order by workbook_amount / settled_amount)::numeric, 4) as ratio_p10,
  round(percentile_cont(0.50) within group (order by workbook_amount / settled_amount)::numeric, 4) as ratio_median,
  round(percentile_cont(0.90) within group (order by workbook_amount / settled_amount)::numeric, 4) as ratio_p90,
  round((sum(workbook_amount) / nullif(sum(settled_amount), 0))::numeric, 4)                        as ratio_aggregate
from paired
where settled_amount > 0 and workbook_amount > 0
group by store_id, entry_type;

-- ============================================================================
-- UNCERTAINTY BAND ON THE MONTHLY SUMMARY
-- ============================================================================
-- An estimated fee is not a wrong number, it is a number with a range. Showing
-- the range is more useful than nulling the margin over a handful of lines --
-- August had 5 estimated lines out of 138 and reported no margin at all.
--
-- net_low / net_high come from applying the observed p10 and p90 ratios to the
-- estimated portion only. Where nothing is estimated the band collapses and the
-- figures are exact.

drop view if exists ledger_summary;

create view ledger_summary with (security_invoker = on) as
with volume as (
  select
    store_id,
    date_trunc('month', entry_date)::date as month,
    count(distinct split_part(source_id, ':', 1)) as orders,
    sum(quantity) as units
  from ledger_entries
  where source_kind = 'order_line' and entry_type = 'revenue_product'
  group by store_id, date_trunc('month', entry_date)
),
-- Fees we had to estimate: workbook-sourced Walmart charges only. Third-party
-- labels are also source_kind 'manual' but are the seller's own record, so they
-- carry no uncertainty and must not widen the band.
estimated as (
  select
    store_id,
    date_trunc('month', entry_date)::date as month,
    -sum(amount) as estimated_fees
  from ledger_entries
  where source_kind = 'manual'
    and entry_type in ('fee_referral', 'fee_label')
  group by store_id, date_trunc('month', entry_date)
),
spread as (
  select
    store_id,
    min(ratio_p10) as lo,
    max(ratio_p90) as hi
  from fee_calibration
  group by store_id
)
select
  m.store_id,
  m.month,
  coalesce(v.orders, 0) as orders,
  coalesce(v.units, 0)  as units,
  m.revenue, m.cogs, m.warehouse, m.fees, m.returns, m.net_profit,
  coalesce(c.lines, 0)            as lines,
  coalesce(c.uncosted_lines, 0)   as uncosted_lines,
  coalesce(c.unknown_lines, 0)    as unknown_lines,
  coalesce(c.estimated_lines, 0)  as estimated_lines,
  c.pct_costed,
  round(coalesce(e.estimated_fees, 0), 2) as estimated_fees,
  -- A higher ratio means the workbook overstates the fee, so the true fee is
  -- lower and profit is HIGHER. Hence p90 drives net_high.
  round(m.net_profit + coalesce(e.estimated_fees, 0)
        * (1 - 1 / nullif(coalesce(s.lo, 1), 0)), 2) as net_low,
  round(m.net_profit + coalesce(e.estimated_fees, 0)
        * (1 - 1 / nullif(coalesce(s.hi, 1), 0)), 2) as net_high,
  (coalesce(c.unknown_lines, 0) = 0 and coalesce(e.estimated_fees, 0) = 0) as is_complete,
  case
    when m.revenue > 0 then round(100 * m.net_profit / m.revenue, 2)
    else null
  end as margin_pct
from ledger_monthly m
left join volume v    on v.store_id = m.store_id and v.month = m.month
left join period_completeness c on c.store_id = m.store_id and c.month = m.month
left join estimated e on e.store_id = m.store_id and e.month = m.month
left join spread s    on s.store_id = m.store_id;

insert into schema_migrations (filename, note)
values ('migration_a9_fee_calibration.sql', 'calibrate workbook fees; margin band')
on conflict (filename) do nothing;
