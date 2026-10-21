-- Migration A22: retire the pre-ledger views.
--
-- WHY
-- Four views still compute profit the old way -- recomputing from current state
-- on every read, using sku_landed_cost with no date filter, so their numbers
-- drift as new purchases arrive:
--
--     order_details    superseded by ledger_order_details  (A16-A20)
--     profit_by_sku    superseded by ledger_sku_monthly    (A2d)
--     monthly_profit   superseded by ledger_summary        (A2d, A9)
--     returns          superseded by ledger_returns        (A19)
--
-- Nothing reads them: verified by extracting every relation the frontend
-- queries and every one the syncer touches. The only remaining mentions are
-- comments.
--
-- Leaving them is not neutral. Twice this session a tab was found still wired
-- to the old view while the rest of the app read the ledger -- Orders in A16,
-- Returns in A19 -- each quietly disagreeing with every other number on screen.
-- A dead view that still returns plausible figures is a trap, not a fallback.
--
-- RECOVERY
-- Definitions remain in git (schema.sql, migration_dashboard_net_profit.sql,
-- migration_profit_by_month.sql, migration_monthly_profit.sql) and these views
-- hold no data, so restoring one is a paste if it is ever needed.

-- profit_by_sku and monthly_profit both read order_details, so they go first.
drop view if exists profit_by_sku;
drop view if exists monthly_profit;
drop view if exists returns;
drop view if exists order_details;

-- ============================================================================
-- KEPT, DELIBERATELY
-- ============================================================================
-- sku_landed_cost is now unused by any profit path -- the ledger freezes COGS
-- at the sale instead. It stays as an informational "what would this cost to
-- replace today" figure, which is a legitimate question and a different one
-- from "what did this cost when it sold". DATA_MODEL.md section 10 calls this
-- out: useful for display, never as COGS.

comment on view sku_landed_cost is
  'Current weighted-average replacement cost per SKU. NOT a profit input -- the '
  'ledger freezes COGS at the moment of sale. Using this for profit is what let '
  'a September purchase rewrite July.';

-- inventory_levels is still read by the Inventory tab and is unaffected: it
-- answers a stock question, not a money one.

insert into schema_migrations (filename, note)
values ('migration_a22_retire_legacy_views.sql', 'dropped pre-ledger profit views')
on conflict (filename) do nothing;
