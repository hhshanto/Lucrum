-- 006_order_cost_profiles
--
-- US-008 -- Enter buying cost per unit (AC-01..AC-04 only). Module: Costing.
--
-- Full documented shape (database-schema.md:544-576), including columns
-- this story never populates -- the same retrofit-avoidance discipline
-- already applied to orders/accounting_months in 003_orders.sql. This
-- story owns exactly six columns: id, order_id, currency_code,
-- buying_cost_per_unit, buying_cost_per_unit_is_known_zero,
-- quantity_snapshot, buying_cost_total. Everything else (the other five
-- `profit-v1` cost components, cost_completeness_code, record_revision,
-- notes, created_by/updated_by) exists only so a later story does not
-- have to ALTER this table to add it, and stays NULLABLE (or carries a
-- non-monetary bookkeeping default) because nothing in this story ever
-- writes to it.
--
-- No BEGIN/COMMIT here. The runner wraps every migration in a transaction.

create table order_cost_profiles (
  id                                   uuid primary key default gen_random_uuid(),

  -- database-schema.md:551, "One active cost profile per order" -- the
  -- unique constraint below, plus a required FK. BR-02: an order carries
  -- exactly one SKU, and B-18 already rejects any multi-SKU order whole at
  -- import (DECISIONS.md, 2026-10-28), so no multi-SKU order can ever
  -- reach this table -- no branch here accounts for one, by design.
  order_id                             uuid        not null references orders(id),

  -- DECISIONS.md 2026-10-28, "US-008 currency provenance decided by
  -- default": always inherited from the parent order's own currency_code
  -- at write time, never the store's reporting_currency_code, never
  -- independently set, never defaulted. text + CHECK (not char(3)): same
  -- reasoning as orders.currency_code (003_orders.sql) -- a fixed-length
  -- char(n) column raises StringDataRightTruncation for an over-length
  -- value BEFORE the CHECK constraint is evaluated, which would make
  -- enforcement two different mechanisms instead of one.
  --
  -- No column default, ever (BR-12 / database-schema.md:1098): a currency
  -- that cannot be determined is rejected at the application edge, never
  -- assumed.
  currency_code                        text        not null,

  -- Manual, per-unit. NULL means unknown, never zero (US-008 AC-02).
  -- numeric(12,2): manually entered costs are stored at 2dp (profit-v1).
  -- No column default -- a never-touched row must read back as unknown,
  -- not a fabricated zero (database-schema.md:1098, AGENTS.md #4).
  buying_cost_per_unit                 numeric(12,2),

  -- Distinguishes an explicit, known zero from a blank input (US-008
  -- AC-03). This is a FLAG, not itself an unknown value, so it is the one
  -- column on this table that legitimately carries a default -- "false"
  -- is not a guess about a cost, it is the true initial state of a row
  -- nobody has entered a cost against yet.
  buying_cost_per_unit_is_known_zero   boolean     not null default false,

  -- Quantity used for the total, captured at the time a cost is actually
  -- computed -- never re-derived from the order's own (possibly later
  -- corrected) quantity. integer, not null: every row this story writes
  -- carries a real snapshot from its own order.
  quantity_snapshot                    integer     not null,

  -- buying_cost_per_unit * quantity_snapshot, exact decimal, rounded ONCE
  -- at the end (profit-v1; src/money.py). NULL means unknown -- never
  -- coalesced to zero for display or calculation (database-schema.md:575).
  -- No column default, for the same reason as buying_cost_per_unit above.
  buying_cost_total                    numeric(12,2),

  -- -- Out of scope for US-008 (US-009/US-010/US-011/US-014). Columns
  -- -- exist at full shape; nothing below is populated or read by this
  -- -- story's own code.
  store_settings_version_applied       integer,
  warehouse_cost_default_snapshot      numeric(12,2),
  warehouse_cost_override              numeric(12,2),
  warehouse_cost_effective             numeric(12,2),
  third_party_label_cost               numeric(12,2),
  other_cost                           numeric(12,2),

  -- "incomplete"/"complete"; derived, never user-set. NULL means "not yet
  -- derived" -- this story computes completeness for buying cost alone
  -- (src/costing/service.py:get_cost_completeness) and does not write this
  -- column; it is reserved for whichever later story derives it across
  -- all six profit-v1 components.
  cost_completeness_code               text,

  -- Optimistic concurrency token (US-014 AC-01, out of scope here). A
  -- non-monetary bookkeeping counter, not a policy value, so a starting
  -- default of 1 is a sane initial state rather than an invented business
  -- rule -- the edit-allowance check itself (cost-edit-v1) is US-011's.
  record_revision                      integer     not null default 1,

  notes                                text,

  created_by                           uuid        references users(id),
  updated_by                           uuid        references users(id),

  created_at                           timestamptz not null default now(),
  updated_at                           timestamptz not null default now(),

  constraint order_cost_profiles_order_id_unique
    unique (order_id),

  constraint order_cost_profiles_currency_fmt
    check (currency_code ~ '^[A-Z]{3}$'),

  constraint order_cost_profiles_quantity_snapshot_positive
    check (quantity_snapshot > 0),

  constraint order_cost_profiles_completeness_check
    check (cost_completeness_code is null or cost_completeness_code in ('incomplete', 'complete'))
);

comment on column order_cost_profiles.currency_code is
  'Inherited from the parent order, always (DECISIONS.md 2026-10-28). Never the store''s reporting currency, never independently set.';
comment on column order_cost_profiles.buying_cost_per_unit is
  'NULL means unknown, never zero (US-008 AC-02). No column default.';
comment on column order_cost_profiles.buying_cost_per_unit_is_known_zero is
  'Distinguishes an explicit saved zero from a blank input (US-008 AC-03).';
comment on column order_cost_profiles.buying_cost_total is
  'buying_cost_per_unit * quantity_snapshot, single half-up rounding at the end. NULL means unknown.';
