-- Migration A12: fix the transaction filter in fee_calibration.
--
-- THE BUG
-- A11 added `and transaction_type = 'Sale'` to keep refund rows out of the
-- calibration. Refund commissions arrive POSITIVE -- Walmart handing a fee back
-- -- so averaging them against sale commissions would corrupt the ratio.
--
-- But label charges are not Sale rows either. 'Fee/Reimbursement' only ever
-- arrives as an Adjustment, so restricting to Sale excluded every label pair.
-- fee_label calibration disappeared, workbook labels posted at face value
-- instead of / 1.0295, and net profit fell 17.25.
--
-- Exactly the mistake the test fixtures made an hour earlier: assuming a charge
-- arrives on a Sale row because that is where sales live. The transaction types
-- in this feed are:
--     Sale            commissions and revenue
--     Adjustment      Walmart shipping label charges
--     Refund          reversals
--     Service Fee     item fees
--     PaymentSummary  payout header, no amounts
--
-- THE FIX
-- Exclude what must be excluded rather than enumerate what is allowed. Only
-- Refund rows need keeping out; everything else is a genuine charge.

create or replace view fee_calibration with (security_invoker = on) as
with settled as (
  select
    sl.store_id,
    sl.customer_order_id,
    sl.sku,
    case m.category
      when 'walmart_fee'   then 'fee_referral'
      when 'wfs_label_fee' then 'fee_label'
    end as entry_type,
    -sum(sl.amount) as settled_amount
  from settlement_lines sl
  join fee_category_map m
    on m.store_id = sl.store_id
   and m.transaction_type = coalesce(sl.transaction_type, '')
   and m.amount_type      = coalesce(sl.amount_type, '')
  where m.category in ('walmart_fee', 'wfs_label_fee')
    -- Refund rows carry the fee back as a POSITIVE amount. Mixing those with
    -- the original charge would net them out and corrupt the ratio.
    and coalesce(sl.transaction_type, '') <> 'Refund'
  group by sl.store_id, sl.customer_order_id, sl.sku, m.category
),
workbook as (
  select c.store_id, o.customer_order_id, c.sku, c.label_walmart, c.walmart_fee
  from order_line_costs c
  join order_lines ol
    on ol.store_id = c.store_id
   and ol.purchase_order_id = split_part(c.source_id, ':', 1)
   and ol.line_number       = split_part(c.source_id, ':', 2)
  join orders o
    on o.store_id = ol.store_id and o.purchase_order_id = ol.purchase_order_id
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
  where s.entry_type is not null
)
select
  store_id,
  entry_type,
  count(*) as pairs,
  round(percentile_cont(0.10) within group (order by workbook_amount / settled_amount)::numeric, 4) as ratio_p10,
  round(percentile_cont(0.50) within group (order by workbook_amount / settled_amount)::numeric, 4) as ratio_median,
  round(percentile_cont(0.90) within group (order by workbook_amount / settled_amount)::numeric, 4) as ratio_p90,
  round((sum(workbook_amount) / nullif(sum(settled_amount), 0))::numeric, 4) as ratio_aggregate
from paired
where settled_amount > 0 and workbook_amount > 0
group by store_id, entry_type;

insert into schema_migrations (filename, note)
values ('migration_a12_calibration_filter.sql', 'calibration excludes refunds, not non-sales')
on conflict (filename) do nothing;
