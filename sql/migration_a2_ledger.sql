-- Migration A2: the append-only ledger.
--
-- This is the change that makes months stop mutating. Today every reported
-- number is recomputed from current state on each read, so a closed month moves
-- whenever you buy stock or a line is returned. After this, a reported number is
-- sum(amount) over a date range and nothing recomputes, ever.
--
-- Run in the Supabase SQL Editor. Safe and idempotent.
-- Nothing is dropped; the existing views keep working until A2d replaces them.

-- ============================================================================
-- 1. SHIP DATE
-- ============================================================================
-- Revenue is recognised on shipment, not order date (ORDER_FLOW.md section 3).
-- No re-sync is needed: orders.raw already holds the full Walmart payload, so
-- shipped_at is backfilled from orderLineStatuses by the syncer.

alter table order_lines add column if not exists shipped_at timestamptz;

-- ============================================================================
-- 2. THE LEDGER
-- ============================================================================

create table if not exists ledger_entries (
  entry_id      uuid primary key default gen_random_uuid(),
  store_id      uuid not null references stores(store_id) on delete cascade,

  entry_date    date not null,
  entry_type    text not null check (entry_type in (
                  'revenue_product','revenue_shipping','cogs','warehouse',
                  'fee_referral','fee_label','fee_refund','fee_other',
                  'return_revenue','return_cogs','damage','correction')),
  sku           text,
  quantity      numeric,
  amount        numeric not null,          -- SIGNED: income +, cost -

  source_kind   text not null check (source_kind in (
                  'order_line','settlement','purchase','manual','adjustment','correction')),
  source_id     text not null,
  reverses      uuid references ledger_entries(entry_id),
  effective_for date,                      -- set when entry_date <> economic date
  note          text,
  posted_at     timestamptz not null default now(),

  constraint ledger_entries_natural_key
    unique (store_id, source_kind, source_id, entry_type)
);

-- The unique constraint above is what makes posting idempotent: re-running the
-- backfill cannot double-post. Combined with the date index, these two cover
-- every query the app makes.
create index if not exists ledger_entries_period
  on ledger_entries (store_id, entry_date);
create index if not exists ledger_entries_sku
  on ledger_entries (store_id, sku, entry_date);

-- ============================================================================
-- 3. APPEND-ONLY ENFORCEMENT
-- ============================================================================
-- Omitting UPDATE/DELETE policies stops app users, but the syncer holds the
-- service key, which bypasses RLS entirely. A trigger is the only thing that
-- makes "append-only" actually true.
--
-- The escape hatch exists because a ledger has to be rebuildable while the
-- posting logic is still being developed. Use it deliberately:
--     set local lucrum.allow_ledger_rewrite = 'on';
-- It lasts only for the current transaction.

create or replace function ledger_entries_append_only()
returns trigger language plpgsql as $$
begin
  if coalesce(current_setting('lucrum.allow_ledger_rewrite', true), 'off') = 'on' then
    return coalesce(new, old);
  end if;
  raise exception
    'ledger_entries is append-only: % blocked. Post a reversing entry instead. '
    'For a controlled rebuild: set local lucrum.allow_ledger_rewrite = ''on'';',
    tg_op;
end $$;

drop trigger if exists ledger_entries_no_mutate on ledger_entries;
create trigger ledger_entries_no_mutate
  before update or delete on ledger_entries
  for each row execute function ledger_entries_append_only();

-- ============================================================================
-- 4. UNCOSTED LINES  -- unknown is not zero
-- ============================================================================
-- Today an uncosted SKU contributes $0 profit, which reads exactly like a
-- genuinely break-even SKU. 470 of your 507 realized lines are in this state.
-- They must be countable, not invisible.

create table if not exists uncosted_lines (
  store_id    uuid not null references stores(store_id) on delete cascade,
  source_id   text not null,
  sku         text not null,
  quantity    numeric not null,
  sale_date   date not null,
  reason      text not null check (reason in ('no_purchase_history','insufficient_qty')),
  recorded_at timestamptz not null default now(),
  primary key (store_id, source_id)
);

create index if not exists uncosted_lines_period
  on uncosted_lines (store_id, sale_date);

-- ============================================================================
-- 5. RLS
-- ============================================================================

alter table ledger_entries enable row level security;
alter table uncosted_lines enable row level security;

drop policy if exists "read own ledger" on ledger_entries;
create policy "read own ledger" on ledger_entries
  for select using (user_has_store(store_id));

drop policy if exists "post own ledger" on ledger_entries;
create policy "post own ledger" on ledger_entries
  for insert with check (user_is_store_admin(store_id));
-- deliberately no update or delete policy, for anyone, ever

drop policy if exists "read own uncosted" on uncosted_lines;
create policy "read own uncosted" on uncosted_lines
  for select using (user_has_store(store_id));

-- ============================================================================
-- 6. REPORTING VIEWS OVER THE LEDGER
-- ============================================================================
-- Note these aggregate in Postgres. The browser fetches ~5 rows per month
-- instead of 549 lines to sum in JavaScript, which is the real fix for the
-- 1000-row PostgREST cap (UI_DESIGN.md section 6) -- a cap your data has now
-- crossed, at 2,290 settlement rows.

create or replace view ledger_monthly with (security_invoker = on) as
select
  store_id,
  date_trunc('month', entry_date)::date as month,
  round(sum(amount) filter (where entry_type in ('revenue_product','revenue_shipping')), 2) as revenue,
  round(-sum(amount) filter (where entry_type = 'cogs'), 2)      as cogs,
  round(-sum(amount) filter (where entry_type = 'warehouse'), 2) as warehouse,
  round(-sum(amount) filter (where entry_type in
    ('fee_referral','fee_label','fee_refund','fee_other')), 2)   as fees,
  round(sum(amount) filter (where entry_type in
    ('return_revenue','return_cogs')), 2)                        as returns,
  round(sum(amount), 2)                                          as net_profit
from ledger_entries
group by store_id, date_trunc('month', entry_date);

-- Completeness travels with the number it qualifies. A month's profit means
-- nothing without knowing how much of it is actually known.
create or replace view period_completeness with (security_invoker = on) as
with sold as (
  select store_id, date_trunc('month', entry_date)::date as month,
         count(distinct source_id) as lines,
         sum(amount) as revenue
  from ledger_entries
  where entry_type = 'revenue_product'
  group by store_id, date_trunc('month', entry_date)
),
missing as (
  select store_id, date_trunc('month', sale_date)::date as month,
         count(*) as uncosted_lines
  from uncosted_lines
  group by store_id, date_trunc('month', sale_date)
)
select
  s.store_id,
  s.month,
  s.lines,
  coalesce(m.uncosted_lines, 0) as uncosted_lines,
  round(100.0 * (s.lines - coalesce(m.uncosted_lines, 0)) / nullif(s.lines, 0), 1) as pct_costed,
  round(s.revenue, 2) as revenue
from sold s
left join missing m on m.store_id = s.store_id and m.month = s.month;

insert into schema_migrations (filename, note)
values ('migration_a2_ledger.sql', 'append-only ledger + uncosted_lines')
on conflict (filename) do nothing;
