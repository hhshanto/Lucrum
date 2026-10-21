-- Migration A8: let the syncer call rebuild_ledger.
--
-- A6 guarded the function with user_is_store_admin(), which reads auth.uid().
-- The syncer authenticates with the service key, where auth.uid() is NULL, so
-- the check rejected it:
--     P0001: not an admin of store 7b3710be-...
--
-- The guard is still wanted -- without it any authenticated user could wipe any
-- store's ledger -- it just has to recognise that the service role is the
-- trusted backend rather than an anonymous caller.
--
-- Role comes from the JWT claims PostgREST publishes. Reading those rather than
-- current_user matters: under security definer current_user is the function
-- owner, which would tell us nothing about who actually called.

create or replace function rebuild_ledger(p_store_id uuid)
returns table (deleted_entries bigint, deleted_uncosted bigint)
language plpgsql
security definer
set search_path = public
as $$
declare
  v_claims text := current_setting('request.jwt.claims', true);
  v_role   text := coalesce(nullif(v_claims, '')::json ->> 'role', '');
  n_entries  bigint;
  n_uncosted bigint;
begin
  if v_role <> 'service_role' and not user_is_store_admin(p_store_id) then
    raise exception 'not an admin of store %', p_store_id
      using hint = 'Call this with the service key, or as an admin of the store.';
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

insert into schema_migrations (filename, note)
values ('migration_a8_fix_rebuild_rpc.sql', 'rebuild_ledger accepts the service role')
on conflict (filename) do nothing;
