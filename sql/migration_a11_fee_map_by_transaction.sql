-- Migration A11: key the fee map on (transaction_type, amount_type).
--
-- WHY
-- 'Product Price' means two opposite things depending on the transaction it
-- sits on:
--     Sale    / Product Price   +4,832.60   duplicates order_lines -> ignore
--     Refund  / Product Price     -541.99   reverses revenue       -> MUST post
--
-- Keyed on amount_type alone the map cannot express that, so 'ignore' silently
-- dropped 709.41 of refunds and 21 refunded orders stayed counted at full
-- price. Walmart's order API still shows those lines as Delivered, so nothing
-- else in the system could have noticed.
--
-- The stop-gap was a hardcoded set in posting.py naming the refund labels. That
-- works until Walmart adds a transaction type, at which point the knowledge is
-- buried in Python instead of sitting in a table the quarantine view can check.
-- This moves the rule into data.
--
-- All 21 combinations present in the data are enumerated below. Anything new
-- lands in unmapped_amount_types and is NOT posted.

-- ============================================================================
-- 1. NEW CATEGORY
-- ============================================================================
-- Revenue reversal is not a fee, so it needs its own category rather than being
-- forced into one of the fee buckets.

alter table fee_category_map drop constraint if exists fee_category_map_category_check;
alter table fee_category_map add constraint fee_category_map_category_check
  check (category in ('walmart_fee','wfs_label_fee','refund_fee','extra_service_fee',
                      'return_revenue','ignore'));

-- ============================================================================
-- 2. RE-KEY
-- ============================================================================

alter table fee_category_map add column if not exists transaction_type text;

-- Existing rows were written when every mapping applied to sales.
update fee_category_map set transaction_type = 'Sale' where transaction_type is null;

alter table fee_category_map drop constraint if exists fee_category_map_pkey;
alter table fee_category_map
  add constraint fee_category_map_pkey primary key (store_id, transaction_type, amount_type);

-- ============================================================================
-- 3. EVERY COMBINATION OBSERVED
-- ============================================================================
--   Sale   / Product Price, Shipping        already in order_lines -> ignore
--   Refund / Product Price, Shipping        revenue reversal       -> return_revenue
--   taxes  (both directions, both txn types) collected and remitted by Walmart,
--          net to zero, never the seller's money                   -> ignore
--   Commission on Product / Shipping        the referral fee. On a Refund it is
--          POSITIVE -- Walmart giving the fee back -- and the same bucket
--          handles it correctly because amounts stay signed.

delete from fee_category_map;

insert into fee_category_map (store_id, transaction_type, amount_type, category)
select s.store_id, v.txn, v.amt, v.cat
from stores s
cross join (values
  ('Sale',           'Product Price',                'ignore'),
  ('Sale',           'Shipping',                     'ignore'),
  ('Sale',           'Product tax',                  'ignore'),
  ('Sale',           'Product tax withheld',         'ignore'),
  ('Sale',           'Shipping tax',                 'ignore'),
  ('Sale',           'Shipping tax withheld',        'ignore'),
  ('Sale',           'Promo Code',                   'ignore'),
  ('Sale',           'Total Walmart Funded Savings', 'ignore'),
  ('Sale',           'Commission on Product',        'walmart_fee'),
  ('Sale',           'Commission on Shipping',       'walmart_fee'),

  ('Refund',         'Product Price',                'return_revenue'),
  ('Refund',         'Shipping',                     'return_revenue'),
  ('Refund',         'Product tax',                  'ignore'),
  ('Refund',         'Product tax withheld',         'ignore'),
  ('Refund',         'Shipping tax',                 'ignore'),
  ('Refund',         'Shipping tax withheld',        'ignore'),
  ('Refund',         'Commission on Product',        'walmart_fee'),
  ('Refund',         'Commission on Shipping',       'walmart_fee'),

  ('Adjustment',     'Fee/Reimbursement',            'wfs_label_fee'),
  ('Service Fee',    'Item Fees',                    'extra_service_fee'),
  ('PaymentSummary', '',                             'ignore')
) as v(txn, amt, cat);

-- ============================================================================
-- 4. QUARANTINE ON THE FULL KEY
-- ============================================================================
-- Dropped rather than replaced: transaction_type becomes column 2, and
-- "create or replace view" can only append trailing columns, never rename or
-- reorder (ERROR 42P16). Views hold no data, so this costs nothing.
--
-- fee_calibration below keeps its exact column list, so it can be replaced
-- in place.

drop view if exists unmapped_amount_types;

create view unmapped_amount_types with (security_invoker = on) as
select
  sl.store_id,
  coalesce(sl.transaction_type, '(null)') as transaction_type,
  coalesce(sl.amount_type, '(null)')      as amount_type,
  count(*)            as rows,
  sum(sl.amount)      as total_amount,
  min(sl.report_date) as first_seen,
  max(sl.report_date) as last_seen
from settlement_lines sl
left join fee_category_map m
  on m.store_id = sl.store_id
 and m.transaction_type = coalesce(sl.transaction_type, '')
 and m.amount_type      = coalesce(sl.amount_type, '')
where m.amount_type is null
group by sl.store_id, coalesce(sl.transaction_type, '(null)'), coalesce(sl.amount_type, '(null)');

-- fee_calibration reads the map, so it has to use the new key too.
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
    and coalesce(sl.transaction_type, '') = 'Sale'   -- compare like with like
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
values ('migration_a11_fee_map_by_transaction.sql', 'fee map keyed on transaction_type + amount_type')
on conflict (filename) do nothing;
