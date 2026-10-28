-- 004_import_tracking
--
-- US-005b-1 -- import run visibility, backend half. Module: Orders.
--
-- Two tables, full documented shape (database-schema.md:965-998):
--
--   import_batches   one row per import run (any import_type, not just
--                    orders -- store_id is nullable because 'settlements'
--                    and 'stores' imports are not store-scoped).
--   import_errors    reviewable errors for a batch, one row per rejected
--                    unit of work (this story: one row per rejected ORDER,
--                    not per submitted line -- see src/orders/service.py's
--                    own docstring for why "order" is the chosen
--                    granularity, an implementer judgment, not a spec
--                    reading).
--
-- raw_import_records (database-schema.md:1000, marked optional) is
-- deliberately NOT built here -- out of scope for this story, per the
-- test file's own module docstring.
--
-- No BEGIN/COMMIT here. The runner wraps every migration in a transaction.

-- ---------------------------------------------------------------------------
-- import_batches
-- ---------------------------------------------------------------------------

create table import_batches (
  id             uuid primary key default gen_random_uuid(),

  import_type    text        not null,
  store_id       uuid        references stores(id),
  source_name    text,

  status_code    text        not null,

  imported_by    uuid        references users(id),

  started_at     timestamptz not null,
  finished_at    timestamptz,

  row_count      integer     not null default 0,
  success_count  integer     not null default 0,
  error_count    integer     not null default 0,

  constraint import_batches_import_type_check
    check (import_type in ('orders', 'settlements', 'stores')),
  constraint import_batches_status_check
    check (status_code in ('pending', 'processing', 'completed', 'failed', 'partial'))
);

create index import_batches_store_type_started_idx
  on import_batches (store_id, import_type, started_at desc);

comment on table import_batches is
  'Tracks every import run (database-schema.md:967-983). US-005b-1 writes orders rows only.';
comment on column import_batches.store_id is
  'Nullable -- not every import_type is store-scoped (settlements/stores imports today are out of scope; nothing writes them yet).';

-- ---------------------------------------------------------------------------
-- import_errors
-- ---------------------------------------------------------------------------

create table import_errors (
  id                  uuid primary key default gen_random_uuid(),

  import_batch_id     uuid        not null references import_batches(id),

  row_number          integer,
  external_reference  text,
  error_code          text        not null,
  error_message       text        not null,
  raw_row             jsonb,

  created_at          timestamptz not null default now()
);

create index import_errors_batch_idx on import_errors (import_batch_id);

comment on table import_errors is
  'Reviewable import errors (database-schema.md:985-998). One row per rejected order, not per submitted line -- see src/orders/service.py.';
comment on column import_errors.import_batch_id is
  'Required -- an error row with no traceable parent batch would defeat the point of this table.';
