-- Incremental migration for settlement (fee) data.
-- Run this in the Supabase SQL Editor (after schema.sql and migration_order_details.sql).

-- Raw rows pulled from Walmart's recon (settlement) report. One row per
-- transaction line (sale, referral fee, WFS fee, refund, etc).
create table if not exists settlement_lines (
  id                     uuid primary key default gen_random_uuid(),
  store_id               uuid references stores(store_id) on delete cascade,
  report_date            date not null,
  customer_order_id      text,
  order_line_number      text,
  sku                    text,
  transaction_type       text,
  amount_type            text,
  amount                 numeric,
  quantity               numeric,
  transaction_posted_at  timestamptz,
  synced_at              timestamptz default now()
);

create index if not exists settlement_lines_lookup on settlement_lines (store_id, customer_order_id, sku);

-- Maps Walmart's recon "Amount Type" strings to the 4 fee columns shown in
-- Order Details. Populate this after the first settlement sync, once you can
-- see the real Amount Type values for your account.
create table if not exists fee_category_map (
  amount_type text primary key,
  category    text not null check (category in ('walmart_fee', 'wfs_label_fee', 'refund_fee', 'extra_service_fee', 'ignore'))
);

alter table settlement_lines enable row level security;
alter table fee_category_map enable row level security;

create policy "read own settlement lines" on settlement_lines for select using (user_has_store(store_id));

-- Lookup table, not store-scoped: any authenticated user can read it.
create policy "read fee category map" on fee_category_map for select using (auth.role() = 'authenticated');
