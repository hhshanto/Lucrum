-- Migration A1: migration tracking + correct the fee category map.
--
-- Run this in the Supabase SQL Editor BEFORE running sync_settlement.py with the
-- fixed syncer. Order matters -- see "WHY THIS MUST RUN FIRST" below.
--
-- ============================================================================
-- WHY THIS MUST RUN FIRST
-- ============================================================================
-- settlement_lines has been empty since day one because of two syncer bugs
-- (MMDDYYYY parsed as ISO; page-size parameter named `limit` instead of
-- `noOfRecords`). Both are now fixed, so 2,290 real rows are ready to load.
--
-- But fee_category_map currently holds four mappings:
--     'Sale', 'Shipping', 'Tax'  -> ignore
--     'Service Fee'              -> walmart_fee
--
-- and the amount types Walmart ACTUALLY sends for this account are:
--     Product Price, Shipping, Product tax, Product tax withheld,
--     Shipping tax, Shipping tax withheld, Commission on Product,
--     Commission on Shipping, Fee/Reimbursement, Item Fees,
--     Promo Code, Total Walmart Funded Savings
--
-- Only 'Shipping' overlaps. order_details (schema.sql:371, 377) sends anything
-- unmapped to 'extra_service_fee', and net_profit ADDS the fee buckets
-- (schema.sql:464) because settlement amounts are signed. So loading settlement
-- against today's map would add roughly +$7,100 of phantom profit -- the
-- revenue double-count warned about at schema.sql:355-360.
--
-- The date bug was, accidentally, the only thing preventing that.
--
-- ============================================================================
-- 1. MIGRATION TRACKING
-- ============================================================================
-- There has never been a record of which migrations are applied; they were
-- pasted into the SQL editor by hand. Introspection on 2026-10-21 confirmed the
-- live schema matches schema.sql plus all 13 migrations, so they are backfilled
-- here as already-applied.

create table if not exists schema_migrations (
  filename    text primary key,
  applied_at  timestamptz not null default now(),
  note        text
);

insert into schema_migrations (filename, note) values
  ('schema.sql',                             'baseline'),
  ('migration_order_details.sql',            'backfilled: verified present'),
  ('migration_settlement.sql',               'backfilled: verified present'),
  ('migration_settlement_fees.sql',          'backfilled: verified present'),
  ('migration_fee_scope_and_allocation.sql', 'backfilled: verified present'),
  ('migration_purchase_columns.sql',         'backfilled: verified present'),
  ('migration_landed_cost_profit.sql',       'backfilled: verified present'),
  ('migration_dashboard_net_profit.sql',     'backfilled: verified present'),
  ('migration_monthly_profit.sql',           'backfilled: verified present'),
  ('migration_profit_by_month.sql',          'backfilled: verified present'),
  ('migration_realized_orders.sql',          'backfilled: verified present'),
  ('migration_shipping_fix.sql',             'backfilled: verified present'),
  ('migration_manual_fees.sql',              'backfilled: verified present'),
  ('migration_admin_role.sql',               'backfilled: verified present')
on conflict (filename) do nothing;

-- ============================================================================
-- 2. CORRECT THE FEE CATEGORY MAP
-- ============================================================================
-- Derived from all 2,290 rows across the five available recon reports
-- (07/28, 08/11, 09/08, 09/22, 10/20 of 2026). Totals shown are across all five.
--
--   Product Price                 +4,290.61  already in order_lines.product_revenue
--   Shipping                      +4,511.15  already in order_lines.shipping_revenue
--   Product tax                     +231.48  Walmart collects and remits; not seller money
--   Product tax withheld            -231.48  exactly nets the above
--   Shipping tax                     +41.09  "
--   Shipping tax withheld            -41.09  "
--   Commission on Product           -458.29  the referral fee
--   Commission on Shipping          -314.65  commission charged on shipping revenue
--   Fee/Reimbursement               -863.48  every row reads "Walmart Shipping Label Service Charge"
--   Item Fees                        -32.74  misc per-item charges
--   Promo Code                       -0.22   customer discount...
--   Total Walmart Funded Savings     +0.22   ...reimbursed by Walmart; nets to zero
--   (blank)                           0.00   PaymentSummary header rows
--
--   Real fee burden = 863.48 + 458.29 + 314.65 + 32.74 = $1,669.16
--   on $8,801.76 gross, about 19%.

delete from fee_category_map
where amount_type in ('Sale', 'Tax', 'Service Fee');   -- never appear in this account's reports

insert into fee_category_map (store_id, amount_type, category)
select s.store_id, v.amount_type, v.category
from stores s
cross join (values
  ('Product Price',                'ignore'),
  ('Shipping',                     'ignore'),
  ('Product tax',                  'ignore'),
  ('Product tax withheld',         'ignore'),
  ('Shipping tax',                 'ignore'),
  ('Shipping tax withheld',        'ignore'),
  ('Promo Code',                   'ignore'),
  ('Total Walmart Funded Savings', 'ignore'),
  ('',                             'ignore'),
  ('Commission on Product',        'walmart_fee'),
  ('Commission on Shipping',       'walmart_fee'),
  ('Fee/Reimbursement',            'wfs_label_fee'),
  ('Item Fees',                    'extra_service_fee')
) as v(amount_type, category)
on conflict (store_id, amount_type) do update set category = excluded.category;

-- ============================================================================
-- 3. QUARANTINE VIEW
-- ============================================================================
-- Any amount_type Walmart starts sending that is not mapped above still falls
-- through to 'extra_service_fee' in order_details. The mapping above neutralises
-- the catastrophic cases (Product Price, Shipping -> ignore), so a new unmapped
-- type is now a modest error rather than a revenue double-count. This view makes
-- it visible instead of silent.
--
-- Check it after every settlement sync. It must be empty.

create or replace view unmapped_amount_types with (security_invoker = on) as
select
  sl.store_id,
  coalesce(sl.amount_type, '(null)') as amount_type,
  count(*)        as rows,
  sum(sl.amount)  as total_amount,
  min(sl.report_date) as first_seen,
  max(sl.report_date) as last_seen
from settlement_lines sl
left join fee_category_map m
  on m.store_id = sl.store_id
 and m.amount_type = coalesce(sl.amount_type, '')
where m.amount_type is null
group by sl.store_id, coalesce(sl.amount_type, '(null)');

-- ============================================================================
-- 4. NOT DONE HERE
-- ============================================================================
-- The content-hash unique index on settlement_lines (ORDER_FLOW.md §4.2), which
-- would replace the non-transactional delete-then-insert, is deliberately left
-- out. It must not be added until the 2,290 live rows are confirmed free of
-- genuine duplicates on the hash columns -- otherwise the insert starts failing.
--
-- Verify first:
--   select customer_order_id, order_line_number, sku, transaction_type,
--          amount_type, amount, quantity, count(*)
--   from settlement_lines
--   group by 1,2,3,4,5,6,7
--   having count(*) > 1;

insert into schema_migrations (filename, note)
values ('migration_a1_settlement_fixes.sql', 'fee map corrected from live recon data')
on conflict (filename) do nothing;
