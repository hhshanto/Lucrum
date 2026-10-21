-- Migration A13: incremental sync state and a run log.
--
-- WHY
-- sync.py fetches by createdStartDate with --days 2. An order created Monday
-- and shipped Friday is never re-fetched, so its status freezes at whatever it
-- was when first seen and its revenue never realises. The --days 180 backfill
-- cleared the historical damage, but the bug recurs every day the syncer runs.
--
-- Walmart supports lastModifiedStartDate, which returns orders whose STATUS
-- changed, not just new ones. Verified against the live API: 72 orders modified
-- since 2026-10-01 versus 104 created in the same window -- a genuinely
-- different and more useful set.
--
-- Two tables:
--   sync_state  where each feed got to. The watermark advances ONLY on success,
--               so a crash or the 300s web timeout simply means the next run
--               redoes that window. Safe, because every write is an upsert.
--   sync_runs   what happened. "When did settlement last load?" is currently
--               unanswerable, which is how settlement stayed empty for months
--               without anyone noticing.

create table if not exists sync_state (
  store_id    uuid not null references stores(store_id) on delete cascade,
  feed        text not null check (feed in ('orders','products','settlement')),
  watermark   timestamptz,
  last_run_at timestamptz,
  last_status text check (last_status in ('ok','failed','partial')),
  last_error  text,
  primary key (store_id, feed)
);

create table if not exists sync_runs (
  run_id       uuid primary key default gen_random_uuid(),
  store_id     uuid not null references stores(store_id) on delete cascade,
  feed         text not null,
  started_at   timestamptz not null default now(),
  finished_at  timestamptz,
  status       text not null check (status in ('running','ok','failed')),
  window_from  timestamptz,
  rows_seen    integer,
  rows_written integer,
  error        text
);

create index if not exists sync_runs_recent on sync_runs (store_id, feed, started_at desc);

alter table sync_state enable row level security;
alter table sync_runs  enable row level security;

drop policy if exists "read own sync state" on sync_state;
create policy "read own sync state" on sync_state
  for select using (user_has_store(store_id));

drop policy if exists "read own sync runs" on sync_runs;
create policy "read own sync runs" on sync_runs
  for select using (user_has_store(store_id));
-- Writes are the syncer's job and it holds the service key, so no insert or
-- update policy is granted to app users.

-- Freshness per feed, for the header strip described in UI_DESIGN.md section 3.
create or replace view sync_health with (security_invoker = on) as
select
  s.store_id,
  s.feed,
  s.watermark,
  s.last_run_at,
  s.last_status,
  s.last_error,
  extract(epoch from (now() - s.last_run_at)) / 3600 as hours_since_run,
  (select count(*) from sync_runs r
    where r.store_id = s.store_id and r.feed = s.feed
      and r.status = 'failed' and r.started_at > now() - interval '7 days') as failures_7d
from sync_state s;

insert into schema_migrations (filename, note)
values ('migration_a13_sync_state.sql', 'incremental sync watermark + run log')
on conflict (filename) do nothing;
