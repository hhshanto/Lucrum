-- Migration A15: fix the settlement check in period_close_readiness.
--
-- THE BUG
-- A14 tested whether a recon file exists whose REPORT DATE falls in the month
-- being closed. But Walmart settles in arrears: June's sales appear in the
-- 2026-07-28 report. So June showed settlement_ingested = false and could never
-- be closed, despite having zero estimated fees and zero unknown costs -- it is
-- the most complete month in the ledger.
--
-- Same mistake as the calibration filter in A11: reasoning about which bucket a
-- record lives in rather than which period it describes. A settlement row's
-- report_date says when Walmart published it, not which month it settles.
--
-- THE FIX
-- Ask the question that actually matters: does this month's revenue have
-- settlement-sourced fees behind it? That is answerable directly from the
-- ledger, where every entry already carries both its period and its provenance.

-- Dropped, not replaced. settled_entries and estimated_entries are inserted
-- before unmapped_types, which reorders the column list, and
-- "create or replace view" can only append (ERROR 42P16).
--
-- Third time this has bitten in this project. The reliable rule: if a view's
-- column list changes AT ALL -- added, removed, renamed or reordered -- drop it
-- first. Views hold no data, so the drop costs nothing and guessing wrong costs
-- a round trip.

drop view if exists period_close_readiness;

create view period_close_readiness with (security_invoker = on) as
with months as (
  select distinct store_id, date_trunc('month', entry_date)::date as month
  from ledger_entries
),
provenance as (
  select
    store_id,
    date_trunc('month', entry_date)::date as month,
    count(*) filter (where source_kind = 'settlement') as settled_entries,
    count(*) filter (where source_kind = 'manual'
                       and entry_type in ('fee_referral','fee_label')) as estimated_entries
  from ledger_entries
  where entry_type in ('fee_referral','fee_label','fee_label_3p','fee_refund','fee_other')
  group by store_id, date_trunc('month', entry_date)
),
unmapped as (
  select store_id, count(*) as types from unmapped_amount_types group by store_id
)
select
  m.store_id,
  m.month,
  coalesce(p.status, 'open')              as status,
  -- Does this month's revenue have real settlement behind it, regardless of
  -- which report carried it.
  coalesce(v.settled_entries, 0) > 0      as settlement_ingested,
  coalesce(u.types, 0) = 0                as fees_all_mapped,
  coalesce(c.unknown_lines, 0) = 0        as costs_known,
  coalesce(v.estimated_entries, 0) = 0    as fees_final,
  coalesce(c.unknown_lines, 0)            as unknown_lines,
  coalesce(c.estimated_lines, 0)          as estimated_lines,
  coalesce(v.settled_entries, 0)          as settled_entries,
  coalesce(v.estimated_entries, 0)        as estimated_entries,
  coalesce(u.types, 0)                    as unmapped_types,
  (coalesce(v.settled_entries, 0) > 0
   and coalesce(u.types, 0) = 0
   and coalesce(c.unknown_lines, 0) = 0
   and coalesce(v.estimated_entries, 0) = 0) as can_close
from months m
left join periods p             on p.store_id = m.store_id and p.month = m.month
left join provenance v          on v.store_id = m.store_id and v.month = m.month
left join period_completeness c on c.store_id = m.store_id and c.month = m.month
left join unmapped u            on u.store_id = m.store_id;

insert into schema_migrations (filename, note)
values ('migration_a15_close_readiness_fix.sql', 'close readiness uses ledger provenance, not report_date')
on conflict (filename) do nothing;
