-- Migration A30: replace the ledger for named sources only.
--
-- rebuild_ledger (A8) deletes EVERY entry for a store, so correcting one line's
-- cost rewrote all 3,489 rows. The arithmetic was never the cost -- building
-- every figure takes about ten milliseconds -- it was shipping the whole ledger
-- over the wire to say that five numbers moved.
--
-- This does NOT post one line in isolation. The caller still computes the whole
-- ledger from scratch and still compares it against what is stored; this just
-- lets it replace the rows that actually differ. The result is identical to a
-- full rebuild by construction, because it IS the full rebuild -- only the
-- unchanged 99% is left where it is.
--
-- Deleting at all requires the append-only trigger (A2) to be told to stand
-- down, and set_config is transaction-local, so the delete has to happen inside
-- this function rather than in the caller's separate PostgREST request.

create or replace function replace_ledger_sources(
  p_store_id uuid,
  p_source_ids text[]
)
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
  -- Same gate as rebuild_ledger: the service key is the trusted backend, and
  -- auth.uid() is NULL under it, so the admin check cannot be the only test.
  if v_role <> 'service_role' and not user_is_store_admin(p_store_id) then
    raise exception 'not an admin of store %', p_store_id
      using hint = 'Call this with the service key, or as an admin of the store.';
  end if;

  -- An empty list must mean "change nothing". Left to array semantics the
  -- delete below would match no rows anyway, but saying so explicitly keeps a
  -- caller that computed an empty delta from depending on that.
  if p_source_ids is null or array_length(p_source_ids, 1) is null then
    return query select 0::bigint, 0::bigint;
    return;
  end if;

  -- A closed period must not be rewritten even one line at a time. The whole
  -- point of A14's guard is that a closed month is settled; the partial path
  -- would otherwise be a way around it that the full rebuild does not have.
  if exists (
    select 1
    from ledger_entries le
    join periods p
      on p.store_id = le.store_id
     and p.month = date_trunc('month', le.entry_date)::date
    where le.store_id = p_store_id
      and le.source_id = any(p_source_ids)
      and p.status = 'closed'
  ) then
    raise exception 'refusing to rewrite a closed period'
      using hint = 'Re-open the period first.';
  end if;

  perform set_config('lucrum.allow_ledger_rewrite', 'on', true);

  delete from ledger_entries
   where store_id = p_store_id
     and source_id = any(p_source_ids);
  get diagnostics n_entries = row_count;

  delete from uncosted_lines
   where store_id = p_store_id
     and source_id = any(p_source_ids);
  get diagnostics n_uncosted = row_count;

  return query select n_entries, n_uncosted;
end $$;

revoke all on function replace_ledger_sources(uuid, text[]) from public, anon;
grant execute on function replace_ledger_sources(uuid, text[]) to authenticated, service_role;

insert into schema_migrations (filename, note)
values ('migration_a30_partial_rebuild.sql',
        'replace_ledger_sources: rewrite only the sources whose figures changed')
on conflict (filename) do nothing;
