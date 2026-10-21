-- Migration A6: make a ledger rebuild callable from the syncer.
--
-- WHY
-- Posting is idempotent on the natural key, which is what stops a re-run from
-- double-posting. The cost is that an entry posted from a WRONG input is never
-- replaced -- the second post sees the key and skips it. So every change to how
-- a figure is derived has needed a manual rebuild_ledger.sql run in the SQL
-- editor, and forgetting one silently leaves stale numbers in place. That has
-- already happened twice.
--
-- This exposes the same operation as an RPC so `post_ledger.py --rebuild` can do
-- it in one step, with no window where the ledger is empty but nobody knows.
--
-- The append-only trigger still blocks ordinary UPDATE and DELETE. This function
-- is the single, named, auditable way past it -- not a general escape.

create or replace function rebuild_ledger(p_store_id uuid)
returns table (deleted_entries bigint, deleted_uncosted bigint)
language plpgsql
security definer
set search_path = public
as $$
declare
  n_entries bigint;
  n_uncosted bigint;
begin
  -- Only an admin of the store may wipe its ledger. security definer means this
  -- runs with owner rights, so the check cannot be skipped.
  if not user_is_store_admin(p_store_id) then
    raise exception 'not an admin of store %', p_store_id;
  end if;

  perform set_config('lucrum.allow_ledger_rewrite', 'on', true);

  delete from ledger_entries where store_id = p_store_id;
  get diagnostics n_entries = row_count;

  delete from uncosted_lines where store_id = p_store_id;
  get diagnostics n_uncosted = row_count;

  return query select n_entries, n_uncosted;
end $$;

revoke all on function rebuild_ledger(uuid) from public, anon;
grant execute on function rebuild_ledger(uuid) to authenticated, service_role;

-- ============================================================================
-- REBUILD NOW
-- ============================================================================
-- The ledger currently holds warehouse entries computed per UNIT from
-- product_costs instead of per ORDER from the workbook, and no third-party
-- label entries at all, because post_ledger.py was selecting the old column
-- list when it last ran.
--
-- service_role bypasses the admin check above, so this runs directly.

begin;
set local lucrum.allow_ledger_rewrite = 'on';
delete from ledger_entries;
delete from uncosted_lines;
commit;

insert into schema_migrations (filename, note)
values ('migration_a6_rebuild_rpc.sql', 'rebuild_ledger RPC + rebuild')
on conflict (filename) do nothing;
