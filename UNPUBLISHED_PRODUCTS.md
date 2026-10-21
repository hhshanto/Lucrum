# Unpublished Products — Edge Cases & Tests

What happens when a product stops being sellable, and how to check that Lucrum
does the right thing. Companion to [ARCHITECTURE.md](ARCHITECTURE.md).

---

## 1. Why "should I delete it?" is the wrong question

`products` is **not** the spine of this system. The SKU string is.

- Nothing has a foreign key to `products`. `purchases.sku` and `order_lines.sku`
  are plain `text`.
- `inventory_levels` builds its SKU universe from the `combined` CTE — a UNION of
  purchases, order_lines, damaged_goods, inventory_adjustments
  ([schema.sql:304-314](sql/schema.sql#L304-L314)). The catalog is **left-joined**
  afterwards ([schema.sql:332](sql/schema.sql#L332)) purely to fetch
  `max(p.product_name)` for display.
- `sku_landed_cost` reads `purchases`. `profit_by_sku` and `order_details` read
  `order_lines`. Neither touches `products`.
- Both `order_lines` ([schema.sql:44](sql/schema.sql#L44)) and `purchases`
  ([schema.sql:76](sql/schema.sql#L76)) **snapshot `product_name`** at event time.

So deleting a catalog row destroys no sales, no costs, no quantities. It nulls a
display name in one view. The row is worth keeping because it is the only record
of *current* catalog state — not because history depends on it.

**Therefore: no archive table.** A separate `archived_products` would be `products`
with a sadder name and two places to look up a SKU. The whole question is
**visibility per tab**, not storage.

The rule that falls out:

| Surface | Unpublished products |
|---|---|
| Products tab (catalog) | Hidden by default, toggle to reveal |
| Dashboard KPI count | Excluded — it means "live SKUs" |
| Orders, Profit, Purchases, Returns | **Always included** — history is history |
| Inventory | **Always included, and flagged** — this is dead capital |
| Name lookups (`buildNameMap`) | **Always included** — else old rows lose labels |
| SKU pickers for new entries | Excluded, but overridable |

---

## 2. Establish the status vocabulary first

Everything below depends on knowing what values you actually store. **Do not
write `where published_status = 'UNPUBLISHED'` until you've run this.**

```sql
select published_status, lifecycle_status, count(*)
from products
where store_id = '<store-uuid>'
group by 1, 2
order by 3 desc;
```

Walmart's `publishedStatus` is not a boolean — it has historically included
`PUBLISHED`, `UNPUBLISHED`, `IN_PROGRESS`, `STAGE`, `READY_TO_PUBLISH` and
`SYSTEM_PROBLEM`. `lifecycleStatus` is a separate axis (`ACTIVE`, `ARCHIVED`,
`RETIRED`). Confirm your own account's values before filtering.

> ⚠️ **`lifecycle_status` is synced and never used.** [sync.py:113](syncer/sync.py#L113)
> writes it; it appears nowhere in `app.js`. `ARCHIVED`/`RETIRED` is arguably a
> *better* "this product is gone" signal than `published_status`, and you already
> have it. Consider it before building anything new.

Two axes means "unpublished" is a predicate you should define **once**, in SQL,
not re-derive in three places in JS.

---

## 3. Edge cases

### A. Sync layer

**U1 — Unpublished but still returned by `/items`.** *Works today.*
`sync_products` upserts ([sync.py:117](syncer/sync.py#L117)); the row's
`published_status` flips to the new value and the row persists. This is the happy
path and needs no change.

**U2 — Product vanishes from `/items` entirely.** ⚠️ *Broken.*
If Walmart stops returning a retired item, the upsert never touches the row.
`published_status` **freezes at its last known value — possibly `PUBLISHED`,
forever.** A delisted product sits in your catalog looking live. There is
currently no way to detect this.

**U3 — `synced_at` is first-seen, not last-seen.** ⚠️ *Broken, and it's why U2 is
undetectable.*
The column has `default now()` ([schema.sql:67](sql/schema.sql#L67)), but
[sync.py:107-114](syncer/sync.py#L107-L114) omits `synced_at` from the payload.
Defaults fire on INSERT only; on the UPDATE half of an upsert, absent columns are
left untouched. So the timestamp records when a SKU was *first* seen and never
moves again.

Fix — one line in the row dict:

```python
"synced_at": datetime.now(timezone.utc).isoformat(),
```

That turns `synced_at` into a real freshness signal: **any product whose
`synced_at` predates the last successful catalog sync is one Walmart no longer
lists.** Derived, not trusted — strictly better than believing `published_status`.

**U4 — Unpublish → republish.** Must be non-destructive. The upsert handles it
(status flips back, costs in `product_costs` are a separate table and survive).
Worth a test so nobody "optimises" it into a delete later.

**U5 — SKU reuse.** ⚠️ *Unfixable in schema; know the tripwire.*
`products` is keyed `(store_id, sku)`. If a SKU is reused for a genuinely
different product, the upsert overwrites the row **and `sku_landed_cost` blends
two unrelated products' costs into one weighted average** — which flows straight
into `net_profit`. Snapshotted `product_name` protects the display, not the math.
Seller-side discipline: never reuse a SKU.

**U6 — Null / unexpected status.** An item returned with no `publishedStatus`
field yields `null`. Any filter written as `published_status <> 'UNPUBLISHED'`
**silently drops null rows** (three-valued logic). Use
`is distinct from` or an explicit allowlist.

**U7 — Partial sync failure.** `sync_products` has no transaction — an exception
mid-pagination leaves some rows updated and some stale. Combined with a fixed U3,
this would make good rows look delisted. Any "gone from Walmart" check must key
off a *successful* sync, not wall-clock time.

### B. Money & views

**U8 — Unpublished SKU with sales in the period.** *Must still count.* This is the
fear that started this doc, and it's structurally impossible to break: nothing in
`profit_by_sku` / `order_details` joins `products`. Test it anyway — it's the core
invariant.

**U9 — Unpublished SKU with `on_hand > 0`.** *Invisible today.* Inventory you paid
for that can no longer sell itself. This is the single most valuable thing to
surface, in the same spirit as the existing "no known cost" warning
([app.js:1186-1191](web/static/app.js#L1186-L1191)) — both are quiet distortions
of the picture.

**U10 — Unpublished SKU with `on_hand < 0`.** Already means "sold more than you
logged buying." Unpublished makes it worse: you can no longer fix it by logging
future purchases, so `sku_landed_cost` is permanently averaging incomplete data.

**U11 — SKU in transactions but never in the catalog.** Sold before your first
catalog sync, or bought but never listed. Appears in `inventory_levels` with
`product_name = null` (left join). Not an unpublish case, but the same null-name
code path — test them together.

**U12 — Catalog row with zero transactions.** Appears on the Products tab, absent
from `inventory_levels` entirely (the `combined` union never emits it). Unpublish
it and it vanishes from a filtered Products tab too — it becomes invisible
everywhere. Fine, but know it's the one row type that can disappear completely.

### C. UI

**U13 — `stat-products` counts everything.**
[app.js:1588](web/static/app.js#L1588) is `productRows.length`. Unpublished SKUs
inflate the "Products" KPI. Should count live SKUs only.

**U14 — The cost-warning deadlock.** ⚠️ *This one bites the moment you ship the
filter.*
The Dashboard warning tells you to *"set a cost on the Products tab"*
([app.js:1189](web/static/app.js#L1189)). It fires for any **sold** SKU with no
cost. If a SKU sold in April and was unpublished in May, the warning fires — but a
filtered Products tab **won't show the row**, so there is nowhere to set the cost
and the nag is unfixable.

Whatever the filter default, an unpublished-but-sold SKU must stay reachable.
Options: exempt sold SKUs from the filter, deep-link the warning to the unfiltered
view, or make the toggle sticky. Pick one **before** shipping U13.

**U15 — Name lookups must not be filtered.**
`renderPurchases` / `renderDamaged` build a `nameMap` from `products`
([app.js:1226-1227](web/static/app.js#L1226-L1227)). If you filter unpublished out
of the fetch, **every historical purchase of a retired SKU loses its name.** The
filter belongs in the *catalog render*, not the query.

**U16 — SKU picker for new entries.** Should exclude unpublished by default (you
rarely buy more of a dead SKU) but allow it — a late-arriving purchase for a
just-retired SKU is legitimate, and blocking it corrupts `sku_landed_cost`.

**U17 — Cost CSV export/import round-trip.** `catalogExport` is built inside the
render loop ([app.js:1216-1221](web/static/app.js#L1216-L1221)). Filtering the
loop silently **drops unpublished SKUs from the export** — re-import and you may
wipe their costs. Export should probably ignore the filter.

**U18 — Filter state vs. store switch / period filter.** The toggle must survive
`invalidate()` and store changes, and must **not** interact with the Period filter
(catalog is state, not events — see ARCHITECTURE §7).

---

## 4. Test probes

There is **no test runner in this repo** (no pytest in `requirements.txt`; the
only baseline is `od_baseline.json`, a hand-captured `order_details` snapshot).
These are SQL probes to paste into the Supabase SQL editor — the same style you're
already working in. Run as a user with store access so RLS applies.

Replace `'<store-uuid>'` throughout.

### T1 — Prove `synced_at` is frozen (confirms U3)

```sql
select count(*)                as rows,
       count(distinct synced_at) as distinct_stamps,
       min(synced_at), max(synced_at)
from products
where store_id = '<store-uuid>';
```

**Look for:** stamps clustered at your first-ever sync, not today. `distinct_stamps`
≈ number of syncs you've *added SKUs* in, not number of syncs run. After the U3
fix, re-running a sync should move `max(synced_at)` to now.

### T2 — Candidate delisted products (the U2 detector, post-fix)

```sql
select sku, product_name, published_status, lifecycle_status, synced_at
from products
where store_id = '<store-uuid>'
  and synced_at < (select max(synced_at) from products where store_id = '<store-uuid>')
order by synced_at;
```

**Look for:** empty on a healthy store. Anything listed was not returned by the
last catalog sync. Meaningless until U3 is fixed — it returns nothing today.

### T3 — Unpublished SKU still earns profit (the U8 invariant — **the important one**)

```sql
select p.sku, p.published_status, s.month, s.units, s.revenue, s.net_profit
from profit_by_sku s
join products p on p.store_id = s.store_id and p.sku = s.sku
where s.store_id = '<store-uuid>'
  and p.published_status is distinct from 'PUBLISHED'
order by s.month desc;
```

**Expect:** rows, with non-null `net_profit`. If an unpublished SKU with known
sales returns nothing here, the no-delete invariant is broken and profit is
under-reported.

### T4 — Dead capital (U9)

```sql
select i.sku, i.product_name, p.published_status, p.lifecycle_status,
       i.purchased, i.sold, i.on_hand
from inventory_levels i
join products p on p.store_id = i.store_id and p.sku = i.sku
where i.store_id = '<store-uuid>'
  and p.published_status is distinct from 'PUBLISHED'
  and i.on_hand > 0
order by i.on_hand desc;
```

**Expect:** the dashboard warning candidates. Each row is stock you own and cannot
list. Note `is distinct from` — `<>` would drop null-status rows (U6).

### T5 — Negative on-hand among unpublished (U10)

Same as T4 with `i.on_hand < 0`. **Expect:** empty. Any row means
`sku_landed_cost` for that SKU is permanently wrong.

### T6 — Transactions with no catalog row (U11)

```sql
select ol.sku, count(*) as lines, max(ol.product_name) as name_from_order
from order_lines ol
left join products p on p.store_id = ol.store_id and p.sku = ol.sku
where ol.store_id = '<store-uuid>' and p.sku is null
group by ol.sku;
```

**Expect:** usually empty. Non-empty is fine (name comes from the snapshot) but
tells you the catalog is incomplete — and these SKUs can never be filtered by
status because they have none.

### T7 — Catalog rows with no transactions (U12)

```sql
select p.sku, p.product_name, p.published_status
from products p
left join inventory_levels i on i.store_id = p.store_id and i.sku = p.sku
where p.store_id = '<store-uuid>' and i.sku is null;
```

**Expect:** listings you've never bought or sold. These vanish entirely once the
Products tab filters unpublished.

### T8 — Deletion safety proof (run in a transaction, **roll back**)

Demonstrates the §1 claim empirically before you trust it.

```sql
begin;
  create temp table before_profit as
    select * from profit_by_sku where store_id = '<store-uuid>';
  create temp table before_inv as
    select store_id, sku, on_hand from inventory_levels where store_id = '<store-uuid>';

  delete from products where store_id = '<store-uuid>' and sku = '<some-sku>';

  -- profit must be byte-identical
  select 'profit drift' as check, count(*) from (
    (select * from before_profit except select * from profit_by_sku where store_id = '<store-uuid>')
    union all
    (select * from profit_by_sku where store_id = '<store-uuid>' except select * from before_profit)
  ) d;

  -- quantities must be identical; only the name goes null
  select 'qty drift' as check, count(*) from (
    (select * from before_inv except select store_id, sku, on_hand from inventory_levels where store_id = '<store-uuid>')
    union all
    (select store_id, sku, on_hand from inventory_levels where store_id = '<store-uuid>' except select * from before_inv)
  ) d;
rollback;
```

**Expect:** both counts `0`. That is the proof that history does not depend on the
catalog — and equally, that deleting buys you nothing.

---

## 5. Unit tests for `sync_products`

`sync_products` ([sync.py:104-119](syncer/sync.py#L104-L119)) takes `supabase` and
`client` as arguments, so it's injectable without touching the network. This is the
only piece of the unpublish story that's cheap to test automatically — and the U3
fix is exactly the kind of thing that silently regresses.

Needs `pip install pytest` (not currently a dependency). Sketch — **not yet written
or run**:

```python
# tests/test_sync_products.py
from unittest.mock import MagicMock
from syncer.sync import sync_products

def _fake_supabase():
    sb = MagicMock()
    sb.table.return_value.upsert.return_value.execute.return_value = None
    return sb

def _rows_sent(sb):
    return sb.table.return_value.upsert.call_args[0][0]

def test_unpublished_status_is_written_through():
    client = MagicMock()
    client.get_items.return_value = [
        {"sku": "A1", "productName": "Widget", "price": {"amount": 9.99},
         "publishedStatus": "UNPUBLISHED", "lifecycleStatus": "ARCHIVED"},
    ]
    sb = _fake_supabase()
    sync_products(sb, "store-1", client)
    assert _rows_sent(sb)[0]["published_status"] == "UNPUBLISHED"
    assert _rows_sent(sb)[0]["lifecycle_status"] == "ARCHIVED"

def test_synced_at_is_sent_on_every_upsert():
    """Regression for U3: without this the timestamp is first-seen, not last-seen,
    and delisted products (U2) are undetectable."""
    client = MagicMock()
    client.get_items.return_value = [{"sku": "A1", "productName": "Widget"}]
    sb = _fake_supabase()
    sync_products(sb, "store-1", client)
    assert "synced_at" in _rows_sent(sb)[0]        # FAILS today

def test_missing_price_and_status_do_not_raise():
    """U6: Walmart may omit fields entirely."""
    client = MagicMock()
    client.get_items.return_value = [{"sku": "A1"}]
    sb = _fake_supabase()
    sync_products(sb, "store-1", client)
    row = _rows_sent(sb)[0]
    assert row["price"] is None and row["published_status"] is None

def test_empty_catalog_does_not_upsert():
    """An empty /items response must not wipe or touch the catalog."""
    client = MagicMock()
    client.get_items.return_value = []
    sb = _fake_supabase()
    sync_products(sb, "store-1", client)
    sb.table.return_value.upsert.assert_not_called()
```

`test_synced_at_is_sent_on_every_upsert` is expected to **fail** on current code.
That's the point — it's the U3 regression test.

---

## 6. What's actually broken today

Everything else above is design guidance. These are real defects found by reading
the code:

| # | Defect | Severity | Fix |
|---|---|---|---|
| **U3** | `synced_at` never updates on upsert — it's first-seen, not last-seen | Low impact today, **blocks U2** | One line in [sync.py:107-114](syncer/sync.py#L107-L114) |
| **U2** | Products dropped from `/items` keep a stale `published_status` forever | Silent wrong state | Needs U3 first, then T2 |
| **U14** | Cost warning points at a tab that will hide the row | **Latent** — bites when U13 ships | Decide before shipping the filter |
| **U13** | `stat-products` counts unpublished SKUs | Cosmetic | [app.js:1588](web/static/app.js#L1588) |
| — | `lifecycle_status` synced but never read | Wasted signal | Use it in the predicate |

**Order of work:** U3 (one line, unblocks detection) → T1/T2 to confirm → decide
U14 → then U13/U9. The filter is the *last* step, not the first.

**Not defects:** U8 and the no-delete invariant hold by construction. T3 and T8
exist to keep them that way.
