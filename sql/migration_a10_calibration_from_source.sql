-- Migration A10: derive fee_calibration from settlement_lines, not the ledger.
--
-- THE BUG
-- A9 built fee_calibration on top of ledger_entries. post_ledger.py --rebuild
-- empties the ledger and then posts, so by the time it read the calibration
-- there were no settled entries left to calibrate against. The view returned
-- nothing, posting silently fell back to uncalibrated figures, and fee_referral
-- came out identical to the previous run.
--
-- It failed quietly, which is the failure mode this whole project keeps running
-- into: the calibration was optional by design (too few pairs => do not adjust),
-- so "no data" was indistinguishable from "no adjustment needed".
--
-- THE FIX
-- Calibration is a property of the SOURCE data -- what Walmart charged versus
-- what the workbook recorded. Neither input is derived from the ledger, so the
-- view should not read it. Reading settlement_lines directly removes the
-- circular dependency entirely: the calibration is now correct before a single
-- entry is posted, including on a completely empty ledger.

create or replace view fee_calibration with (security_invoker = on) as
with settled as (
  -- Straight from the mirror of Walmart's recon report, bucketed the same way
  -- posting buckets it, so the comparison is like for like.
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
   and m.amount_type = sl.amount_type
  where m.category in ('walmart_fee', 'wfs_label_fee')
  group by sl.store_id, sl.customer_order_id, sl.sku, m.category
),
workbook as (
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
values ('migration_a10_calibration_from_source.sql', 'calibration reads settlement_lines, not the ledger')
on conflict (filename) do nothing;
