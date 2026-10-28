-- 003_orders
--
-- US-005a -- Import and refresh orders (AC-01, AC-02 only). Module: Orders.
--
-- Two tables, both built to their full documented shape even though this
-- story only exercises part of each:
--
--   accounting_months   empty and unpopulated here. Built at full shape
--                        purely to satisfy orders.accounting_month_id's
--                        foreign key -- no code in this story writes to it.
--                        Month provisioning is an unowned gap (B-17,
--                        PROGRESS.md) and close/reopen workflow is slices
--                        6+. Retrofitting this table's shape later is
--                        exactly the cost slice 0 exists to avoid paying.
--
--   orders               full documented column shape
--                        (database-schema.md:429-456), including columns
--                        this story never populates with anything but NULL
--                        (accounting_month_id, posting_reason_code,
--                        posted_at, fulfilled_quantity, acknowledged_at,
--                        shipped_at, delivered_at, cancelled_at,
--                        source_revision).
--
-- No BEGIN/COMMIT here. The runner wraps every migration in a transaction.

-- ---------------------------------------------------------------------------
-- accounting_months
--
-- database-schema.md:280-309. DB-06: accounting_month_id must be a real,
-- nullable foreign key to this table -- never a generated column, never a
-- NOT NULL. This table is created now, empty, only so that constraint has
-- somewhere to point; nothing in this story inserts a row here.
-- ---------------------------------------------------------------------------

create table accounting_months (
  id                          uuid primary key default gen_random_uuid(),
  store_id                    uuid        not null references stores(id),
  year                        integer     not null,
  month                       integer     not null,
  period_start                date        not null,
  period_end                  date        not null,

  status_code                 text        not null,

  -- BR-08: protection follows closure HISTORY, never current status.
  -- has_ever_been_closed is set true on first close and never reset --
  -- no code in this story (or any story before month close exists) sets
  -- it at all; it stays false on every row this migration could ever
  -- produce, which is none.
  has_ever_been_closed        boolean     not null default false,
  close_count                 integer     not null default 0,

  ready_marked_by             uuid        references users(id),
  ready_marked_at             timestamptz,
  ready_basis_revision        text,

  closed_by                   uuid        references users(id),
  closed_at                   timestamptz,
  reopened_by                 uuid        references users(id),
  reopened_at                 timestamptz,
  close_note                  text,

  unresolved_exception_count  integer     not null default 0,

  created_at                  timestamptz not null default now(),
  updated_at                  timestamptz not null default now(),

  constraint accounting_months_month_range
    check (month between 1 and 12),
  constraint accounting_months_status_check
    check (status_code in ('open', 'ready_for_review', 'closing_review', 'closed', 'reopened')),
  constraint accounting_months_store_year_month_unique
    unique (store_id, year, month)
);

comment on table accounting_months is
  'Empty in this slice. Provisioning is an unowned gap (B-17, PROGRESS.md). '
  'Built at full documented shape to avoid a later retrofit.';
comment on column accounting_months.has_ever_been_closed is
  'BR-08: protection follows closure history, never current status_code. Never reset to false once true.';

-- ---------------------------------------------------------------------------
-- orders
--
-- database-schema.md:429-456. One SKU per order (BR-02) -- there is no line
-- item table (SC-09).
-- ---------------------------------------------------------------------------

create table orders (
  id                          uuid primary key default gen_random_uuid(),
  store_id                    uuid        not null references stores(id),

  walmart_order_id            text        not null,
  walmart_purchase_order_id   text,
  sku                         text        not null,
  item_description            text,

  -- Source purchase instant, stored verbatim (UTC; src/clock.py's one rule).
  order_placed_at_utc         timestamptz not null,

  -- Transaction date: order_placed_at_utc converted to the store's own
  -- timezone, reduced to a calendar date. Always follows the source --
  -- never derived from accounting_month_id, never the reverse (period-v1).
  order_date                  date        not null,

  -- Posting period. DB-06: nullable, never generated, never derived from
  -- order_date by any trigger or FK cascade -- posting is a decision
  -- (US-005c), not a derivation. Stays NULL for every row this story writes.
  accounting_month_id         uuid        references accounting_months(id),

  -- BR-14 scope boundary (pre_onboarding vs managed), evaluated from
  -- order_date against the store's managed_from month -- independent of
  -- accounting_month_id and of whether any period has ever been
  -- provisioned.
  scope_status_code           text        not null,

  -- Null until an actual posting decision has been made (US-005c). This
  -- story never sets it to anything but NULL.
  posting_reason_code         text,

  -- 'out_of_scope' for a pre_onboarding order (BR-14); 'unpostable_no_period'
  -- for a managed order, which is honestly true for every row this story
  -- writes, because no accounting_months row is ever provisioned here.
  posting_status_code         text        not null,
  posted_at                   timestamptz,

  quantity                    integer     not null,
  selling_amount               numeric(12,2) not null,

  -- BR-12: no column default, ever. A currency that cannot be determined is
  -- rejected at the application edge, never assumed.
  --
  -- text, not char(3): a fixed-length char(n) column raises
  -- StringDataRightTruncation for an over-length value BEFORE any CHECK
  -- constraint is even evaluated, which would make DB-11's enforcement
  -- mechanism the column's own length limit instead of the CHECK below --
  -- a different, untyped exception a caller would have to special-case
  -- separately from every other format violation. text keeps DB-11's
  -- enforcement uniform: every malformed value, short or long, is rejected
  -- by the same named constraint.
  currency_code               text        not null,

  -- Fulfillment dimension ONLY (SC-01/SC-12/BR-10). refunded,
  -- settlement_pending and reconciled belong to other state dimensions and
  -- are rejected by the CHECK constraint below, not merely at the
  -- application layer.
  fulfillment_status_code     text        not null,
  fulfilled_quantity          integer,

  acknowledged_at             timestamptz,
  shipped_at                  timestamptz,
  delivered_at                timestamptz,
  cancelled_at                timestamptz,

  source_revision              text,

  -- Freshness (US-005 AC-03, not this story -- but the column must exist at
  -- full shape per the retrofit discipline, and this story does populate it
  -- on every import).
  last_imported_at            timestamptz not null,

  created_at                  timestamptz not null default now(),
  updated_at                  timestamptz not null default now(),

  -- DB-12: per-store uniqueness, never global. Two different stores may
  -- legitimately carry the same external order id (AC-02's second sentence).
  constraint orders_store_walmart_order_id_unique
    unique (store_id, walmart_order_id),

  constraint orders_quantity_positive
    check (quantity > 0),

  constraint orders_currency_fmt
    check (currency_code ~ '^[A-Z]{3}$'),

  -- DB-01: the fulfillment dimension's complete, exhaustive value set.
  -- refunded/settlement_pending/reconciled belong to a DIFFERENT dimension
  -- and must never reach this column -- enforced here, not only in
  -- application code, because collapsing dimensions is AGENTS.md's
  -- non-negotiable #1 and a database constraint cannot be bypassed by a
  -- future importer that forgets the application-layer check.
  constraint orders_fulfillment_status_check
    check (fulfillment_status_code in ('acknowledged', 'shipped_in_transit', 'delivered', 'cancelled')),

  constraint orders_scope_status_check
    check (scope_status_code in ('managed', 'pre_onboarding')),

  constraint orders_posting_status_check
    check (posting_status_code in ('posted', 'out_of_scope', 'unpostable_no_period', 'unpostable_no_open_period'))
);

create index orders_store_order_date_idx on orders (store_id, order_date);

comment on column orders.fulfillment_status_code is
  'Fulfillment dimension ONLY -- exhaustive four-value set. refunded/settlement_pending/reconciled belong elsewhere (BR-10).';
comment on column orders.accounting_month_id is
  'DB-06: nullable, never generated/derived from order_date. NULL in every row this slice writes.';
comment on column orders.scope_status_code is
  'BR-14: managed vs pre_onboarding, evaluated from order_date against stores.managed_from. Independent of posting.';
comment on constraint orders_store_walmart_order_id_unique on orders is
  'DB-12: per-store uniqueness, never global (US-005 AC-02).';
