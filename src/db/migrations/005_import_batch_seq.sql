-- 005_import_batch_seq
--
-- G-8 -- deterministic selection of "the most recent import attempt".
-- Module: Orders.
--
-- Defect: `get_import_run_state` (src/orders/repository.py) ordered
-- candidate `import_batches` rows with plain `order by started_at desc`.
-- `started_at` is application-supplied (`datetime.now(timezone.utc)` on
-- the real import path), so two rows for the same `(store_id,
-- import_type)` can share a byte-identical instant -- observed on
-- Windows, microsecond-resolution clock. PostgreSQL's row order among
-- ties with no secondary key is UNDEFINED, so which batch read as "most
-- recent" flipped under concurrent full-suite load (PROGRESS.md, G-8).
--
-- Decision (`DECISIONS.md`, 2026-10-28, "G-8 decided under the standing
-- escalation rule"), binding, not re-derived here:
--
--   * `order by started_at desc, id desc` is REJECTED. `import_batches.id`
--     is `uuid primary key default gen_random_uuid()` (migration 004) --
--     a random value. Ordering by it is deterministic but ARBITRARY: it
--     would report the OLDER attempt as "most recent" roughly half the
--     time, trading a visible flake for a silent, permanent wrong answer.
--   * The fix: a monotonic `batch_seq` column, assigned by the database
--     itself in insertion order, used ONLY as a last-resort disambiguator
--     for rows that already share a `started_at` instant.
--   * `started_at` remains the PRIMARY sort key always. A row with a
--     genuinely later `started_at` must still win regardless of
--     insertion order -- `batch_seq` must never become a substitute
--     global ordering.
--
-- No BEGIN/COMMIT here. The runner wraps every migration in a transaction.

-- ---------------------------------------------------------------------------
-- batch_seq -- monotonic, database-assigned, never application-supplied.
-- ---------------------------------------------------------------------------
--
-- `generated always as identity` backfills existing rows automatically as
-- part of `alter table ... add column`: Postgres assigns identity values
-- to pre-existing rows in their current physical/heap scan order as the
-- column is added, inside this same migration transaction. This migration
-- relies only on each existing row ending up with SOME distinct, strictly
-- increasing `batch_seq` -- never on which existing row gets which
-- specific value -- because every pre-existing row was inserted before
-- this migration ran and none of them are mid-tie with each other in any
-- way this migration needs to resolve; the backfill order among
-- already-ambiguous historical ties is exactly as undefined as the
-- defect this migration fixes, and no worse than before it.

alter table import_batches
  add column batch_seq bigint generated always as identity;

comment on column import_batches.batch_seq is
  'Monotonic, database-assigned insertion sequence. Last-resort tiebreaker ONLY for import_batches rows sharing a started_at instant (G-8) -- never a substitute for started_at as the primary ordering key, and never exposed to or asserted on by name in application-facing contracts.';

-- ---------------------------------------------------------------------------
-- Extend the covering index so the tiebreak is index-ordered, not a sort.
-- ---------------------------------------------------------------------------

drop index import_batches_store_type_started_idx;

create index import_batches_store_type_started_idx
  on import_batches (store_id, import_type, started_at desc, batch_seq desc);
