-- ============================================================================
-- A24: one sync per store per feed
-- ============================================================================
--
-- WHY
-- web/app.py:99 and :127 are bare subprocess.run calls with no locking of any
-- kind. sync_runs already records status='running' (migration_a13), but nothing
-- ever reads it -- it feeds only the failures_7d count in sync_health. The
-- browser disables the button (app.js), but Orders and Fees are separate
-- buttons with separate flags, and a second tab or a second user on the same
-- store defeats both.
--
-- On its own that burns Walmart API quota and races the sync_state watermark.
-- Combined with the non-atomic settlement replace it was worse: two overlapping
-- settlement runs could interleave delete and insert and duplicate or erase a
-- report date. A23 closed the atomicity half; this closes the concurrency half.
--
-- WHY A LEASE AND NOT pg_advisory_lock
-- pg_advisory_lock is session-scoped and the syncer talks to the database over
-- PostgREST, which is stateless -- consecutive calls are not guaranteed the same
-- backend session, so a session lock would be released at an arbitrary moment.
-- pg_advisory_xact_lock lasts only the one statement. A row-based lease is the
-- only form that survives the architecture, and it also survives multiple web
-- workers, which a process-level lock in app.py would not.
--
-- WHY ITS OWN TABLE AND NOT COLUMNS ON sync_state
-- The syncer upserts sync_state *while it holds the lock*, at the end of every
-- run. Parking locked_at there would make correctness depend on PostgREST's
-- partial-column upsert never touching a column absent from the payload -- true
-- today, but an invisible coupling of the exact kind this migration exists to
-- remove. A lock is ephemeral runtime state; a watermark is durable progress.
-- Separate concerns, separate tables. Idle is simply "no row".
--
-- STALE LOCKS
-- A normal failure releases in the syncer's finally block. A hard kill -- the
-- 300-second timeout at web/app.py:132 is the realistic one -- cannot, so the
-- lease expires on its own after p_ttl_seconds. The default of 900s is set
-- comfortably above that 300s cap so a live run is never stolen mid-flight;
-- the cost is a bounded wait after a kill. The lease is exposed on sync_health
-- so a stuck feed is visible rather than mysterious.
--
-- Safe to re-run.

create table if not exists sync_locks (
  store_id   uuid not null references stores(store_id) on delete cascade,
  feed       text not null,
  locked_at  timestamptz not null default now(),
  lock_token uuid not null,
  primary key (store_id, feed)
);

comment on table sync_locks is
  'One row per sync currently in flight. No row means idle. Rows older than the '
  'TTL passed to acquire_sync_lock are dead leases left by a killed syncer and '
  'are reclaimed automatically.';

alter table sync_locks enable row level security;

drop policy if exists "read own sync locks" on sync_locks;
create policy "read own sync locks" on sync_locks
  for select using (user_has_store(store_id));
-- No insert/update/delete policy: leases are taken by the syncer through the
-- functions below, which hold the service key. Same stance as sync_state (A13).

-- ----------------------------------------------------------------------------
-- Claim. Returns the token on success, NULL when someone else holds it.
-- ----------------------------------------------------------------------------
create or replace function acquire_sync_lock(
  p_store_id    uuid,
  p_feed        text,
  p_ttl_seconds integer default 900
)
returns uuid
language plpgsql
security definer
set search_path = public
as $$
declare
  -- Role comes from the JWT claims PostgREST publishes, not current_user: under
  -- security definer current_user is the function owner and says nothing about
  -- who actually called. Same reasoning as A8.
  v_claims text := current_setting('request.jwt.claims', true);
  v_role   text := coalesce(nullif(v_claims, '')::json ->> 'role', '');
  v_token  uuid := gen_random_uuid();
  v_got    uuid;
begin
  if v_role <> 'service_role' then
    raise exception 'acquire_sync_lock requires the service key'
      using hint = 'Only the syncer takes sync locks.';
  end if;

  -- A single statement, so two racing callers serialise on the (store_id, feed)
  -- primary key: the loser blocks, then sees the winner's committed row and its
  -- WHERE fails. SELECT-then-INSERT would leave exactly the gap being closed.
  insert into sync_locks as l (store_id, feed, locked_at, lock_token)
  values (p_store_id, p_feed, now(), v_token)
  on conflict (store_id, feed) do update
     set locked_at  = now(),
         lock_token = v_token
   where l.locked_at < now() - make_interval(secs => p_ttl_seconds)
  returning l.lock_token into v_got;

  return v_got;
end $$;

comment on function acquire_sync_lock(uuid, text, integer) is
  'Claim the sync lease for one store+feed. Returns a token, or NULL if a live '
  'run already holds it. Leases older than p_ttl_seconds are treated as dead.';

-- ----------------------------------------------------------------------------
-- Release. Token must match.
-- ----------------------------------------------------------------------------
create or replace function release_sync_lock(
  p_store_id uuid,
  p_feed     text,
  p_token    uuid
)
returns boolean
language plpgsql
security definer
set search_path = public
as $$
declare
  v_claims text := current_setting('request.jwt.claims', true);
  v_role   text := coalesce(nullif(v_claims, '')::json ->> 'role', '');
  n        bigint;
begin
  if v_role <> 'service_role' then
    raise exception 'release_sync_lock requires the service key';
  end if;

  -- Matching on the token matters: if this run overran its TTL and a second run
  -- legitimately took the lease, the straggler finishing later must not delete
  -- the lock its successor now holds.
  delete from sync_locks
   where store_id   = p_store_id
     and feed       = p_feed
     and lock_token = p_token;
  get diagnostics n = row_count;

  return n > 0;
end $$;

comment on function release_sync_lock(uuid, text, uuid) is
  'Release a sync lease. No-op unless the token still matches, so a run that '
  'outlived its TTL cannot free the lease its successor now holds.';

revoke all on function acquire_sync_lock(uuid, text, integer) from public, anon, authenticated;
revoke all on function release_sync_lock(uuid, text, uuid)   from public, anon, authenticated;
grant execute on function acquire_sync_lock(uuid, text, integer) to service_role;
grant execute on function release_sync_lock(uuid, text, uuid)    to service_role;

-- Surface the lease so a wedged feed is diagnosable from the Sync tab instead of
-- looking like a sync that mysteriously does nothing. Left join: no row is the
-- normal, idle case.
--
-- Dropped first, not CREATE OR REPLACE: replace can only append columns to the
-- end of a view, and locked_at belongs beside the other state columns. Same
-- trap A15 hit with period_close_readiness. Nothing depends on this view.
drop view if exists sync_health;

create view sync_health with (security_invoker = on) as
select
  s.store_id,
  s.feed,
  s.watermark,
  s.last_run_at,
  s.last_status,
  s.last_error,
  l.locked_at,
  extract(epoch from (now() - s.last_run_at)) / 3600 as hours_since_run,
  (select count(*) from sync_runs r
    where r.store_id = s.store_id and r.feed = s.feed
      and r.status = 'failed' and r.started_at > now() - interval '7 days') as failures_7d
from sync_state s
left join sync_locks l
  on l.store_id = s.store_id and l.feed = s.feed;

insert into schema_migrations (filename, note)
values ('migration_a24_sync_lock.sql', 'one sync per store+feed via a row lease')
on conflict (filename) do nothing;
