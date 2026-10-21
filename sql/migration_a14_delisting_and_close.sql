-- Migration A14: product delisting + period close.
--
-- Two unrelated features in one file purely to save a round trip.

-- ============================================================================
-- 1. DELISTING
-- ============================================================================
-- 36 products have not appeared in Walmart's catalog since June or July, yet 33
-- still read published_status = PUBLISHED. The sync only ever upserted what
-- Walmart returned and never marked what had gone, so a dead listing was
-- indistinguishable from a live one. Eleven of them have sales history,
-- including SWIFTZAR105-108 and 117.
--
-- This was reported as U2 in UNPUBLISHED_PRODUCTS.md months ago. It stayed
-- invisible because products.synced_at was itself broken -- `default now()`
-- only fires on INSERT, so upserting left it at first-seen. Fixing that column
-- is what finally exposed this.

alter table products add column if not exists delisted_at timestamptz;

comment on column products.delisted_at is
  'Set when a FULL successful catalog pull did not return this sku. Cleared if '
  'it reappears. Never set from a partial pull -- that would mass-delist the '
  'catalog on a transient API failure.';

create index if not exists products_delisted on products (store_id, delisted_at);

-- Catalog state the app can trust: a sku is live, delisted, or never seen.
create or replace view product_status with (security_invoker = on) as
select
  p.store_id,
  p.sku,
  p.product_name,
  p.published_status,
  p.lifecycle_status,
  p.synced_at,
  p.delisted_at,
  case
    when p.delisted_at is not null then 'delisted'
    when p.published_status = 'PUBLISHED' then 'live'
    else 'unpublished'
  end as state,
  exists (select 1 from ledger_entries le
           where le.store_id = p.store_id and le.sku = p.sku
             and le.entry_type = 'revenue_product') as has_sales
from products p;

-- ============================================================================
-- 2. PERIOD CLOSE
-- ============================================================================
-- A month you can still quote differently tomorrow was never trustworthy.
-- Closing freezes it: no entry may afterwards be dated into that month, so a
-- late fact becomes a dated correction in the open period instead of silently
-- restating history.

create table if not exists periods (
  store_id  uuid not null references stores(store_id) on delete cascade,
  month     date not null,
  status    text not null default 'open' check (status in ('open','closed')),
  closed_at timestamptz,
  closed_by uuid references auth.users(id),
  note      text,
  primary key (store_id, month)
);

alter table periods enable row level security;

drop policy if exists "read own periods" on periods;
create policy "read own periods" on periods
  for select using (user_has_store(store_id));

drop policy if exists "manage own periods" on periods;
create policy "manage own periods" on periods
  for all using (user_is_store_admin(store_id))
  with check (user_is_store_admin(store_id));

-- Enforcement. Without this "closed" is a label rather than a guarantee, and
-- the syncer holds the service key so RLS alone would not stop it.
create or replace function ledger_entry_period_open()
returns trigger language plpgsql as $$
declare
  v_status text;
begin
  -- The rebuild path legitimately rewrites everything, including closed months.
  if coalesce(current_setting('lucrum.allow_ledger_rewrite', true), 'off') = 'on' then
    return new;
  end if;

  select status into v_status
  from periods
  where store_id = new.store_id
    and month = date_trunc('month', new.entry_date)::date;

  if v_status = 'closed' then
    raise exception
      'period % is closed for store %', to_char(new.entry_date, 'YYYY-MM'), new.store_id
      using hint = 'Post this to the open period as a correction, with '
                   'effective_for set to the original date.';
  end if;
  return new;
end $$;

drop trigger if exists ledger_entries_period_guard on ledger_entries;
create trigger ledger_entries_period_guard
  before insert on ledger_entries
  for each row execute function ledger_entry_period_open();

-- ============================================================================
-- 3. CLOSE READINESS
-- ============================================================================
-- Turns "I do not trust these numbers" into a finite checklist. Each blocker
-- names what to do about it.

create or replace view period_close_readiness with (security_invoker = on) as
with months as (
  select distinct store_id, date_trunc('month', entry_date)::date as month
  from ledger_entries
),
settled as (
  -- Has any recon file landed covering this month?
  select store_id, date_trunc('month', report_date)::date as month, count(*) as rows
  from settlement_lines
  group by store_id, date_trunc('month', report_date)
),
unmapped as (
  select store_id, count(*) as types from unmapped_amount_types group by store_id
)
select
  m.store_id,
  m.month,
  coalesce(p.status, 'open')              as status,
  coalesce(s.rows, 0) > 0                 as settlement_ingested,
  coalesce(u.types, 0) = 0                as fees_all_mapped,
  coalesce(c.unknown_lines, 0) = 0        as costs_known,
  coalesce(c.estimated_lines, 0) = 0      as fees_final,
  coalesce(c.unknown_lines, 0)            as unknown_lines,
  coalesce(c.estimated_lines, 0)          as estimated_lines,
  coalesce(u.types, 0)                    as unmapped_types,
  (coalesce(s.rows, 0) > 0
   and coalesce(u.types, 0) = 0
   and coalesce(c.unknown_lines, 0) = 0
   and coalesce(c.estimated_lines, 0) = 0) as can_close
from months m
left join periods p             on p.store_id = m.store_id and p.month = m.month
left join settled s             on s.store_id = m.store_id and s.month = m.month
left join period_completeness c on c.store_id = m.store_id and c.month = m.month
left join unmapped u            on u.store_id = m.store_id;

insert into schema_migrations (filename, note)
values ('migration_a14_delisting_and_close.sql', 'product delisting + period close')
on conflict (filename) do nothing;
