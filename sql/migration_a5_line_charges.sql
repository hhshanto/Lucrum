-- Migration A5: per-line fulfilment charges from the seller's workbooks.
--
-- WHY
-- The ledger reported $5,161 net against the seller's own $2,615. The whole gap
-- was missing cost, in three categories:
--
--   1. Third-party shipping labels  -$790
--      Labels bought OUTSIDE Walmart. They appear in no API at all, so
--      settlement -- the only fee source the system had -- is structurally
--      blind to them. Nothing in the previous design could ever have found this.
--
--   2. Warehouse / handling        -$818
--      Charged per order. Only 12 SKUs had warehouse_cost set, so it was applied
--      to 30 lines instead of all 507.
--
--   3. Unsettled Walmart charges   -$857
--      October has not settled (last recon file 10/20), so its referral fees and
--      Walmart label charges do not exist yet. The UI showed pending as zero,
--      which reads as free.
--
-- The workbooks carry all of these per line, keyed the same way as the costs
-- already imported in A4.
--
-- PRECEDENCE: settlement wins wherever it exists -- it is what actually left the
-- account. The workbook fills gaps only, which handles October automatically and
-- retires itself as recon files arrive. Disagreements are not silently resolved;
-- see the view at the end.

alter table order_line_costs
  add column if not exists walmart_fee       numeric,
  add column if not exists label_walmart     numeric,
  add column if not exists label_third_party numeric,
  add column if not exists warehouse_cost    numeric;

comment on column order_line_costs.walmart_fee is
  'Referral/commission from the workbook. Used only when settlement has none.';
comment on column order_line_costs.label_walmart is
  'Label bought through Walmart. Used only when settlement has none.';
comment on column order_line_costs.label_third_party is
  'Label bought outside Walmart. NEVER in settlement -- always taken from here.';
comment on column order_line_costs.warehouse_cost is
  'Per-order handling charge. Confirmed by the seller as a real cost of sale.';

-- ============================================================================
-- NEW ENTRY TYPE
-- ============================================================================
-- Third-party labels are a distinct economic fact from Walmart-deducted ones:
-- they are paid directly rather than withheld from a payout, and they can never
-- be reconciled against settlement. Giving them their own type keeps that
-- visible instead of burying them in fee_label.

alter table ledger_entries drop constraint if exists ledger_entries_entry_type_check;
alter table ledger_entries add constraint ledger_entries_entry_type_check
  check (entry_type in (
    'revenue_product','revenue_shipping','cogs','warehouse',
    'fee_referral','fee_label','fee_label_3p','fee_refund','fee_other',
    'return_revenue','return_cogs','damage','correction'));

-- ============================================================================
-- DISAGREEMENT VIEW
-- ============================================================================
-- Settlement wins, but a difference between what Walmart says it took and what
-- the seller recorded is worth knowing about -- it means either the workbook is
-- wrong or Walmart charged something unexpected. Silently preferring one number
-- would hide both cases.

create or replace view fee_label_disagreements with (security_invoker = on) as
with settled as (
  select
    store_id,
    split_part(source_id, ':', 2) as customer_order_id,
    sku,
    round(-sum(amount), 2) as settlement_label
  from ledger_entries
  where entry_type = 'fee_label' and source_kind = 'settlement'
  group by store_id, split_part(source_id, ':', 2), sku
),
sheet as (
  select
    c.store_id,
    o.customer_order_id,
    c.sku,
    round(sum(c.label_walmart), 2) as workbook_label
  from order_line_costs c
  join order_lines ol
    on ol.store_id = c.store_id
   and ol.purchase_order_id = split_part(c.source_id, ':', 1)
   and ol.line_number       = split_part(c.source_id, ':', 2)
  join orders o
    on o.store_id = ol.store_id and o.purchase_order_id = ol.purchase_order_id
  where c.label_walmart is not null and c.label_walmart > 0
  group by c.store_id, o.customer_order_id, c.sku
)
select
  s.store_id,
  s.customer_order_id,
  s.sku,
  s.settlement_label,
  w.workbook_label,
  round(w.workbook_label - s.settlement_label, 2) as difference
from settled s
join sheet w
  on w.store_id = s.store_id
 and w.customer_order_id = s.customer_order_id
 and w.sku = s.sku
where abs(w.workbook_label - s.settlement_label) > 0.01;

insert into schema_migrations (filename, note)
values ('migration_a5_line_charges.sql', 'per-line labels, warehouse, unsettled fees')
on conflict (filename) do nothing;
