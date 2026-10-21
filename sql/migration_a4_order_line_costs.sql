-- Migration A4: exact per-line costs from the seller's own spreadsheets.
--
-- The two SWIFTZAR workbooks record what was actually paid for each sale --
-- "Unit Purchase Price" in the Jul/Aug book, "Per Item Cost" in the Aug-Oct
-- book -- keyed by customer order and SKU. 199 rows cover 505 of 507 realized
-- lines by SKU, which is 97.5% of revenue.
--
-- This is SPECIFIC IDENTIFICATION: the actual cost of the actual item sold. For
-- buy-to-order arbitrage it is more accurate than any averaging method, so it
-- takes precedence over the moving average from purchases.
--
-- New cost precedence (highest first):
--     1. order_line_costs   exact cost for this specific line
--     2. sku_landed_cost    moving average from logged purchases
--     3. product_costs      hand-entered fallback (placeholder => estimated)
--     4. none               unknown
--
-- Run in the SQL Editor, then:  python import_costs.py && python post_ledger.py

create table if not exists order_line_costs (
  store_id   uuid not null references stores(store_id) on delete cascade,
  source_id  text not null,                 -- "{purchase_order_id}:{line_number}"
  sku        text not null,
  unit_cost  numeric not null check (unit_cost > 0),

  -- How confident this figure is:
  --   exact       the spreadsheet has a row for THIS customer order + SKU
  --   sku_single  no row for this line, but every spreadsheet row for the SKU
  --               agrees on one cost, so it is not really in doubt
  --   sku_imputed no row for this line and the SKU's cost varies between rows;
  --               the median is used and the line is flagged as estimated
  basis      text not null check (basis in ('exact','sku_single','sku_imputed')),

  source     text,                           -- workbook#sheet it came from
  imported_at timestamptz not null default now(),
  primary key (store_id, source_id)
);

create index if not exists order_line_costs_sku on order_line_costs (store_id, sku);

alter table order_line_costs enable row level security;

drop policy if exists "read own line costs" on order_line_costs;
create policy "read own line costs" on order_line_costs
  for select using (user_has_store(store_id));

drop policy if exists "add own line costs" on order_line_costs;
create policy "add own line costs" on order_line_costs
  for insert with check (user_is_store_admin(store_id));

drop policy if exists "edit own line costs" on order_line_costs;
create policy "edit own line costs" on order_line_costs
  for update using (user_is_store_admin(store_id))
  with check (user_is_store_admin(store_id));

drop policy if exists "delete own line costs" on order_line_costs;
create policy "delete own line costs" on order_line_costs
  for delete using (user_is_store_admin(store_id));

insert into schema_migrations (filename, note)
values ('migration_a4_order_line_costs.sql', 'exact per-line costs from seller spreadsheets')
on conflict (filename) do nothing;
