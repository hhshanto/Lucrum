-- Rebuild the ledger from scratch.
--
-- WHEN TO USE THIS
-- The ledger is append-only and posting is idempotent on
-- (store_id, source_kind, source_id, entry_type). That is normally exactly what
-- you want -- re-running a sync cannot double-post. But it also means a COGS
-- entry that was posted from a WRONG cost will never be replaced: the second
-- post sees the key already exists and skips it.
--
-- So after changing how a cost is derived, re-posting alone is not enough.
--
-- WHY A REBUILD RATHER THAN CORRECTIONS
-- In normal operation a wrong figure is fixed by posting a dated reversing
-- entry, never by editing history -- that is the whole point of the ledger, and
-- it is what you should do once real reporting has started.
--
-- This is the exception: the entries being discarded were computed from
-- placeholder costs that were openly fabricated (73.23% of selling price).
-- There is no accounting history worth preserving in a guess, and a pile of
-- corrections against numbers nobody ever relied on would be noise, not an
-- audit trail.
--
-- Nothing outside the ledger is touched. Every input -- orders, order_lines,
-- purchases, settlement_lines, order_line_costs -- is untouched, so
-- post_ledger.py reproduces the ledger exactly and deterministically.
--
-- USAGE
--   1. run this in the Supabase SQL Editor
--   2. python post_ledger.py

begin;

-- The trigger from migration_a2_ledger.sql blocks UPDATE and DELETE even for
-- the service key. This is the documented, deliberately awkward way past it,
-- and it lasts only for this transaction.
set local lucrum.allow_ledger_rewrite = 'on';

delete from ledger_entries;
delete from uncosted_lines;

commit;

-- Expected after re-posting with order_line_costs in place:
--   ~2,397 ledger entries   (unchanged -- same inputs, same revenue and fees)
--   ~44 uncosted lines      (41 sku_imputed + 3 with no cost data at all),
--                            down from 470
