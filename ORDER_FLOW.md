# Lucrum — Order & Sync Flow Design

Status: **draft for review**. Depends on `DATA_MODEL.md`.

---

## 1. The central change: ingest and post are separate

Today one function fetches from Walmart *and* decides what it means. The money
math then lives in SQL views recomputed on every read.

```
          now:   Walmart ──► upsert mirrors ──► views recompute on read
      proposed:  Walmart ──► ingest ──► mirrors ──► post ──► ledger ──► views read
                            (I/O)                  (pure)
```

**Ingest** talks to the network. It is failure-prone, slow, and external. It only
ever writes mirror tables, and it is fully idempotent.

**Post** is a pure function over local data. No network. Deterministic. Same
inputs always produce the same ledger entries.

Three things this buys:

1. **Posting is unit-testable without Walmart.** Feed it fixture rows, assert the
   ledger. This is the single biggest step toward a test suite, and the project
   currently has zero tests.
2. **You can re-post without re-fetching.** Fix a costing bug, replay from
   mirrors, compare. No API quota, no rate limits.
3. **A network failure can never leave half-computed money.** Worst case is a
   stale mirror, which the next ingest repairs.

---

## 2. Order lifecycle

```
  Created ──► Acknowledged ──► Shipped ──► Delivered ──┬──► (return window closes) ──► FINAL
     │              │                                   │
     └──────────────┴──► Cancelled ──► FINAL            └──► Returned ──► FINAL
```

| Status | In mirror | Posts to ledger |
|---|---|---|
| `Created` | yes | nothing |
| `Acknowledged` | yes | nothing |
| `Shipped` | yes | **revenue + COGS + warehouse** |
| `Delivered` | yes | nothing further (already posted at Shipped) |
| `Cancelled` | yes | nothing, **if never shipped** |
| `Returned` | yes | `return_revenue` + `return_cogs`, dated to the return |

Current data: 88 Delivered, 81 Shipped, 12 Cancelled, 4 Acknowledged, 1 Created.

### A line is not final when it is Delivered

Delivered lines can still be returned. A line stays **watched** — eligible for
re-fetch — until either it is Cancelled, or it has been Delivered longer than the
return window. Only then does it stop costing API calls.

This matters for close: a month cannot be truly final while its lines are still
returnable. See §7.

---

## 3. Revenue recognition date

**Decision: recognize on ship date, not order date.**

Today everything groups by `orders.order_date` (`schema.sql:506`, `535`). An order
placed Sep 30 and shipped Oct 2 is booked to September.

Ship date is correct because:
- Revenue is earned when control transfers, i.e. on shipment.
- Walmart settles on shipment. Using order date guarantees a permanent one-to-two
  day skew against the only oracle we have.
- `counts_in_profit` already keys realization off `Shipped` — so the system
  already believes shipping is the trigger, then dates it by something else.

`order_lines` has no ship date column today. **It does not need a re-sync**:
`orders.raw` is a full `jsonb` payload, so ship timestamps are already on disk and
can be backfilled by extracting from `orderLineStatuses`.

```sql
alter table order_lines add column if not exists shipped_at timestamptz;
```

Fallback when absent: order date, flagged in `uncosted_lines`-style fashion so the
estimate is visible rather than assumed.

---

## 4. Ingestion

### 4.1 Orders — fixing the staleness bug

`sync.py:65` fetches by `createdStartDate` with `--days 2` by default. An order
created Monday and shipped Friday is **never re-fetched**, so its status is frozen
at `Acknowledged` and its revenue never realizes. Your data has 5 such lines.

Walmart's `/orders` endpoint supports `lastModifiedStartDate`. That is the fix.

```
pass 1  incremental:  lastModifiedStartDate = watermark − overlap
pass 2  repair:       re-fetch every still-watched line (§2) not seen in pass 1
```

Pass 1 catches new orders *and* status transitions. Pass 2 is the safety net for
anything Walmart fails to report as modified.

```sql
create table sync_state (
  store_id    uuid not null references stores(store_id) on delete cascade,
  feed        text not null check (feed in ('orders','products','settlement')),
  watermark   timestamptz,
  last_run_at timestamptz,
  last_status text check (last_status in ('ok','failed','partial')),
  last_error  text,
  primary key (store_id, feed)
);
```

**The watermark advances only on success.** A crash or timeout mid-run simply
means the next run redoes that window — safe, because every write is an idempotent
upsert. This removes the danger of the 300-second subprocess timeout
(`web/app.py:104`) entirely.

`overlap` defaults to 24h to absorb clock skew and late-reported modifications.

### 4.2 Settlement — three fixes

**Date format.** Walmart returns `MMDDYYYY`; the code parses it as ISO:

```python
# sync_settlement.py:69  — raises ValueError: month must be in 1..12
dt.date.fromisoformat(str(d)[:10])
# correct
dt.datetime.strptime(str(d).strip(), "%m%d%Y").date()
```

The `except Exception` at line 130 swallows this and prints `FAILED` while the
process still **exits 0**, so `web/app.py:110` falls back to string-matching the
word "FAILED" in stdout. This is the sole reason `settlement_lines` is empty.

**Key guessing.** `get_available_recon_dates` (`walmart_client.py:115`) tries five
possible key names and returns `[]` if none match — indistinguishable from "no
reports exist". The live response uses `availableApReportDates`. Match it
explicitly and raise on an unexpected shape.

**Non-transactional replace.** `delete()` then `insert()` (`sync_settlement.py:89`)
can lose a date if interrupted. Replace with a content hash and an idempotent
insert — no delete, no transaction needed:

```sql
alter table settlement_lines
  add column line_hash text
    generated always as (
      md5(coalesce(customer_order_id,'') || '|' || coalesce(order_line_number,'') || '|' ||
          coalesce(sku,'') || '|' || coalesce(transaction_type,'') || '|' ||
          coalesce(amount_type,'') || '|' || coalesce(amount::text,'') || '|' ||
          coalesce(quantity::text,''))
    ) stored;

create unique index settlement_lines_natural
  on settlement_lines (store_id, report_date, line_hash);
```

Re-syncing a date becomes `insert ... on conflict do nothing`. Fully idempotent,
nothing is ever destroyed.

> Note: a genuine duplicate line in Walmart's report would collapse. If that is
> observed, add a per-date ordinal to the hash.

### 4.3 Products

Two defects, both one-liners:

```python
# sync.py:107 — synced_at has `default now()` but upsert never retriggers it,
# so it records first-seen, not last-seen. Set it explicitly.
"synced_at": dt.datetime.now(dt.timezone.utc).isoformat(),
```

And delisted items: `get_items` returns the **complete** catalog, so anything
absent from a successful full pull is gone from Walmart.

```sql
alter table products add column if not exists delisted_at timestamptz;
```

Set `delisted_at = now()` for rows not seen in a full successful sync; clear it if
they reappear. Only on *full* success — a partial pull must never mass-delist.

---

## 5. Posting rules

`post(store_id)` reads mirrors + seller facts and inserts ledger entries.
Idempotency comes free from `unique (store_id, source_kind, source_id, entry_type)`.

```
source_id for an order line = "{purchase_order_id}:{line_number}"
```

```python
for line in realized_lines(store_id):          # status in (Shipped, Delivered)
    d = line.shipped_at or line.order_date
    post(d, 'revenue_product',  +line.product_revenue)
    post(d, 'revenue_shipping', +line.shipping_revenue)
    cost = moving_average_cost(line.sku, d)    # DATA_MODEL.md §5
    if cost is None:
        record_uncosted(line, 'no_purchase_history')
    else:
        post(d, 'cogs',      -(line.quantity * cost))
        post(d, 'warehouse', -(line.quantity * warehouse_cost(line.sku)))
```

Fees post from `settlement_lines`, bucketed through `fee_category_map`.
**Unmapped `amount_type` is not posted** — it goes to the quarantine view and
blocks period close (`DATA_MODEL.md` §8).

---

## 6. The rule for late-arriving facts

Two cases that look similar and must be treated differently:

| | Example | Date used |
|---|---|---|
| **New information about a past event** | fee for an October sale arrives in November settlement; purchase backdated to July entered today | Economic date **if period open**, else `correction` in the open period with `effective_for` = economic date |
| **A genuinely new event** | customer returns an item in March | **Always** the date it happened. Never a correction |

A return is not a correction to January. January was right. March is when the
return occurred.

This is one rule covering backdated purchases, late settlement fees, and
re-mapped fee categories alike.

---

## 7. Close readiness

A month may be closed when all hold:

1. Settlement covering the month has been ingested.
2. `unmapped_amount_types` is empty for the period.
3. No still-watched lines remain (§2) — the return window has closed.
4. `uncosted_lines` for the period is either empty or explicitly accepted.

Until then the month is labelled **provisional**, not wrong. Given settlement
arrives biweekly (`07/28, 08/11, 09/08, 09/22, 10/20`), expect roughly a two-to-six
week lag between month end and close readiness.

---

## 8. Failure handling and orchestration

Current behaviour swallows everything:

```python
except Exception as exc:          # sync.py:156, sync_settlement.py:130
    print(f"  FAILED: {exc}")     # process still exits 0
```

So orders succeeding while products fail reports the whole run as failed, and a
real crash is detected by grepping stdout for a word.

Replace with per-feed isolation and an audit row:

```sql
create table sync_runs (
  run_id      uuid primary key default gen_random_uuid(),
  store_id    uuid not null,
  feed        text not null,
  started_at  timestamptz not null default now(),
  finished_at timestamptz,
  status      text not null check (status in ('running','ok','failed')),
  rows_seen   integer,
  rows_written integer,
  error       text
);
```

- Each feed succeeds or fails independently; one failure does not mask another.
- Non-zero exit on failure, so `returncode` means something.
- A `(store_id, feed)` advisory lock prevents two concurrent syncs racing.
- `sync_runs` answers "when did we last successfully sync?", which is currently
  unanswerable.

**Not changing yet:** the subprocess-per-request model in `web/app.py:99`. At 186
orders a sync takes seconds, and once the watermark only advances on success, a
timeout is harmless. A job queue is the right answer later, not now.

---

## 9. Summary of changes

| Area | Change | Why |
|---|---|---|
| Orders | `lastModifiedStartDate` + repair pass | Status never refreshes today |
| Orders | `shipped_at`, backfilled from `orders.raw` | Recognize on ship date |
| Settlement | `%m%d%Y` parse | **Sole cause of empty settlement data** |
| Settlement | Explicit response key, raise on surprise | Silent `[]` looks like "no data" |
| Settlement | Content hash + `on conflict do nothing` | Removes delete-then-insert data loss |
| Products | Set `synced_at` explicitly | Records first-seen, not last-seen |
| Products | `delisted_at` on full sync | Delisted items keep stale status forever |
| All feeds | `sync_state` watermark, advance on success only | Incremental, crash-safe |
| All feeds | `sync_runs` + non-zero exit + per-feed isolation | Failures are currently invisible |
| New | `post()` as a pure, testable function | Where the test suite starts |

---

## 10. Open questions

1. **Ship date vs order date** changes which month revenue lands in, and will move
   some of your existing numbers. Confirm before backfill.
2. **Return window length** — Walmart's varies by category. A flat 30/60/90 days,
   or per-category?
3. **Repair-pass frequency.** Re-fetching every watched line costs API calls.
   Daily, or only before close?
4. **Shipped vs Delivered as the posting trigger.** I chose Shipped to match
   settlement. Delivered is more conservative but lags, and your data is split
   81/88 across the two.
5. Still open from `DATA_MODEL.md` §12: warehouse cost dating, cancelled-line
   handling, negative shipping, posting rights.
