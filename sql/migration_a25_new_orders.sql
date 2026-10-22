-- ============================================================================
-- A25: the work queue -- orders you have not costed yet
-- ============================================================================
--
-- WHY
-- This is buy-to-order arbitrage: a sale lands, THEN the item is bought from a
-- supplier. What was paid has until now been recorded in a spreadsheet and
-- imported once by hand (syncer/import_costs.py), which has two consequences.
-- The app cannot tell you what still needs doing, and anything the spreadsheet
-- missed is invisible -- 505 of 507 realized lines were covered, and nothing
-- surfaced the other two.
--
-- WHAT COUNTS AS "NOT PROCESSED"
-- A line with no order_line_costs row. Deliberately NOT a new status flag:
-- the cost you type IS the completion signal, so there is one source of truth
-- and no way for a flag to disagree with the data. Save a cost, the line leaves
-- the queue.
--
-- Measured at the time of writing: 553 order lines, 504 costed.
--   Acknowledged    7 uncosted   <- genuinely new work
--   Shipped         1 uncosted   \  shipped without ever being costed; these
--   Delivered       2 uncosted   /  are the gap the workbook import left
--   Cancelled      39 uncosted   <- excluded, see below
-- So the queue is 10 lines, and it doubles as a backstop for anything that
-- slips past the normal flow.
--
-- CANCELLED LINES ARE EXCLUDED
-- They post nothing to the ledger (ORDER_FLOW.md section 4), so a cost entered
-- against one would be stored and then ignored -- worse than useless, because
-- it would read as money accounted for. If a supplier purchase was already made
-- when Walmart cancelled, that is a real loss, but the ledger has no entry type
-- for it today. Recording it needs its own design, not a text box here.
--
-- Safe to re-run.

drop view if exists new_orders;

create view new_orders with (security_invoker = on) as
select
  l.store_id,
  -- The key every other per-line table uses: ledger_entries.source_id,
  -- uncosted_lines, order_line_costs. Built the same way here so a row saved
  -- from this view lands exactly where posting.py looks for it.
  l.purchase_order_id || ':' || l.line_number as source_id,
  l.purchase_order_id,
  l.line_number,
  o.customer_order_id,
  o.order_date,
  o.customer_name,
  o.customer_city,
  o.customer_state,
  l.sku,
  l.product_name,
  l.quantity,
  l.product_revenue,
  l.shipping_revenue,
  round(coalesce(l.product_revenue, 0) + coalesce(l.shipping_revenue, 0), 2) as total_revenue,
  l.status,
  -- Whole days since the order was placed. EDGE_CASES.md O1: an Acknowledged
  -- line older than about two weeks is usually a line that shipped in reality
  -- and whose status the syncer never came back for -- a sync defect wearing
  -- the costume of a backlog. Surfacing the age makes the two distinguishable.
  floor(extract(epoch from (now() - o.order_date)) / 86400)::int as days_waiting,
  -- Counted over ALL lines of the order, not just uncosted ones: warehouse_cost
  -- is charged per ORDER rather than per unit (posting.py:292-295 -- the
  -- workbook bills 1.50 once on a six-unit line). On a multi-line order the
  -- figure must be entered once, and the UI needs to know to say so. Only 1 of
  -- 552 orders currently has more than one line, but silently double-counting
  -- it would be a real error.
  (select count(*) from order_lines l2
    where l2.store_id = l.store_id
      and l2.purchase_order_id = l.purchase_order_id) as lines_in_order
from order_lines l
join orders o
  on  o.store_id          = l.store_id
  and o.purchase_order_id = l.purchase_order_id
left join order_line_costs c
  on  c.store_id  = l.store_id
  and c.source_id = l.purchase_order_id || ':' || l.line_number
where c.source_id is null
  -- Walmart's status is free text with no check constraint, so match loosely:
  -- both the British and American spellings appear in the wild.
  and lower(trim(coalesce(l.status, ''))) not in ('cancelled', 'canceled');

comment on view new_orders is
  'Order lines with no order_line_costs row and not cancelled: the queue of '
  'sales whose supplier cost has not been recorded yet. Entering a cost removes '
  'the line from this view.';

insert into schema_migrations (filename, note)
values ('migration_a25_new_orders.sql', 'work queue of uncosted order lines')
on conflict (filename) do nothing;
