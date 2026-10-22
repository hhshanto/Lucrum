-- ============================================================================
-- A23: replace a settlement date ATOMICALLY
-- ============================================================================
--
-- WHY
-- sync_settlement.py replaced a report date with two separate PostgREST calls:
--
--     .delete().eq(store_id).eq(report_date)     <-- commits on its own
--     .insert(rows)                              <-- may never happen
--
-- Anything between them -- a crash, a dropped connection, the 300-second
-- subprocess timeout in web/app.py:132 -- leaves the date deleted and never
-- re-inserted. settlement_lines is the ONLY source of truth for real Walmart
-- fees, and the next run re-pulls only dates inside --days, so a date lost this
-- way is lost permanently. Every fee on it silently reverts to a workbook
-- estimate and the ledger quietly restates.
--
-- The syncer's own comment claimed this was "Replaced ... in
-- migration_settlement_fixes.sql". That file has never existed. A1 looked at
-- the same problem and explicitly declined it (migration_a1_settlement_fixes.sql
-- lines 134-147), so the code has been carrying a comment that reads as fixed
-- for as long as the bug has been live.
--
-- A function body is one transaction. Delete and insert now either both land or
-- neither does.
--
-- NOT the content-hash unique index that A1 deferred. That index existed to make
-- a repeated INSERT idempotent; an atomic replace is idempotent by construction,
-- so the index is no longer needed and the 2,290 live rows never have to be
-- audited for duplicates first.
--
-- EMPTY REPORTS
-- Walmart returning zero rows for a date is far more likely to be an API hiccup
-- than a genuinely empty settlement day, and the old code wiped the date either
-- way. This refuses to delete live rows when the incoming set is empty, and
-- reports `skipped` so the caller can say so out loud. Pass p_allow_empty => true
-- to force a genuine clear.
--
-- Safe to re-run.

create or replace function replace_settlement_date(
  p_store_id    uuid,
  p_report_date date,
  p_rows        jsonb,
  p_allow_empty boolean default false
)
returns table (deleted bigint, inserted bigint, skipped boolean)
language plpgsql
security definer
set search_path = public
as $$
declare
  -- Role comes from the JWT claims PostgREST publishes, not current_user: under
  -- security definer current_user is the function owner and says nothing about
  -- who actually called. Same reasoning as A8.
  v_claims   text   := current_setting('request.jwt.claims', true);
  v_role     text   := coalesce(nullif(v_claims, '')::json ->> 'role', '');
  v_incoming bigint := jsonb_array_length(coalesce(p_rows, '[]'::jsonb));
  v_existing bigint;
  n_deleted  bigint := 0;
  n_inserted bigint := 0;
begin
  -- Stricter than A8's rebuild_ledger, which also accepts a store admin.
  -- settlement_lines is machine-written and read-only to app users by design
  -- (schema.sql line 228); letting an admin POST rows here would be a way to
  -- fabricate fees, and no UI path needs it.
  if v_role <> 'service_role' then
    raise exception 'replace_settlement_date requires the service key'
      using hint = 'settlement_lines is written by the syncer only.';
  end if;

  select count(*) into v_existing
  from settlement_lines
  where store_id = p_store_id and report_date = p_report_date;

  if v_incoming = 0 and v_existing > 0 and not p_allow_empty then
    return query select 0::bigint, 0::bigint, true;
    return;
  end if;

  delete from settlement_lines
  where store_id = p_store_id and report_date = p_report_date;
  get diagnostics n_deleted = row_count;

  if v_incoming > 0 then
    -- store_id and report_date are taken from the parameters and NOT from the
    -- payload, so a malformed or hostile row cannot write into another store or
    -- smear itself across dates the delete above did not cover.
    insert into settlement_lines (
      store_id, report_date, customer_order_id, order_line_number, sku,
      transaction_type, amount_type, amount, quantity, transaction_posted_at
    )
    select
      p_store_id, p_report_date,
      r.customer_order_id, r.order_line_number, r.sku,
      r.transaction_type, r.amount_type, r.amount, r.quantity, r.transaction_posted_at
    from jsonb_to_recordset(p_rows) as r (
      customer_order_id     text,
      order_line_number     text,
      sku                   text,
      transaction_type      text,
      amount_type           text,
      amount                numeric,
      quantity              numeric,
      transaction_posted_at timestamptz
    );
    get diagnostics n_inserted = row_count;
  end if;

  return query select n_deleted, n_inserted, false;
end $$;

comment on function replace_settlement_date(uuid, date, jsonb, boolean) is
  'Atomically replace one store''s settlement lines for one report date. '
  'Delete and insert share a transaction, so an interruption can no longer '
  'leave the date empty. Refuses to clear a populated date from an empty '
  'payload unless p_allow_empty.';

revoke all on function replace_settlement_date(uuid, date, jsonb, boolean)
  from public, anon, authenticated;
grant execute on function replace_settlement_date(uuid, date, jsonb, boolean)
  to service_role;

insert into schema_migrations (filename, note)
values ('migration_a23_atomic_settlement.sql', 'settlement date replace is transactional')
on conflict (filename) do nothing;
