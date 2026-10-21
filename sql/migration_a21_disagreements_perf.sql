-- Migration A21: the last expression join.
--
-- fee_label_disagreements was defined in A5 and never revisited, so it still
-- reaches order_line_costs through split_part(source_id, ':', 1) joined to
-- order_lines and then orders -- the exact shape that timed out the Profit tab
-- in A20. It has not failed yet only because no tab reads it.
--
-- Worth stating plainly: the bug was fixed where it had already bitten and left
-- in place everywhere else. Grepping for the pattern afterwards found this in
-- seconds; not grepping would have left a view that breaks the first time
-- anybody opens it.
--
-- Also appends a ratio column so the view says how far apart the two are
-- proportionally, not just in dollars -- a 20 cent gap on a 6 dollar label is a
-- different fact from 20 cents on 60.

create or replace view fee_label_disagreements with (security_invoker = on) as
with settled as (
  -- Grouping by a split_part on a single table is a scan, not a nested loop.
  -- It is only dangerous in a JOIN predicate.
  select
    store_id,
    split_part(source_id, ':', 3) as customer_order_id,
    sku,
    round(-sum(amount), 2) as settlement_label
  from ledger_entries
  where entry_type = 'fee_label' and source_kind = 'settlement'
  group by store_id, split_part(source_id, ':', 3), sku
)
select
  s.store_id,
  s.customer_order_id,
  s.sku,
  s.settlement_label,
  round(c.label_walmart, 2)                       as workbook_label,
  round(c.label_walmart - s.settlement_label, 2)  as difference,
  -- Trailing, so create or replace is allowed.
  round(c.label_walmart / nullif(s.settlement_label, 0), 4) as ratio
from settled s
join order_line_costs c
  on c.store_id = s.store_id
 and c.customer_order_id = s.customer_order_id
 and c.sku = s.sku
where c.label_walmart is not null
  and c.label_walmart > 0
  and abs(c.label_walmart - s.settlement_label) > 0.01;

insert into schema_migrations (filename, note)
values ('migration_a21_disagreements_perf.sql', 'last split_part join removed')
on conflict (filename) do nothing;
