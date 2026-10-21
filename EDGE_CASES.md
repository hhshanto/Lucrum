# Lucrum — Project-Wide Edge Cases & Tests

Every failure mode found by reading the code, ranked by whether it can silently
give you a **wrong number**. Companion to [ARCHITECTURE.md](ARCHITECTURE.md); the
catalog/unpublish story has its own deep-dive in
[UNPUBLISHED_PRODUCTS.md](UNPUBLISHED_PRODUCTS.md).

**Severity legend**

| | Meaning |
|---|---|
| 🔴 | Silently wrong money, or data loss. No error shown. |
| 🟠 | Wrong or missing data, usually visible if you look. |
| 🟡 | Breaks loudly, or corrupts only in an unusual case. |
| ⚪ | Latent / cosmetic / worth knowing. |

**Verified vs. suspected.** Everything below is derived from reading the code. Items
marked **(confirm)** depend on Walmart's live API behaviour or your account's data,
which I can't see from here — the test probes tell you which way it goes.

---

## The short list

If you fix nothing else, fix these five. Each one produces a **plausible-looking
wrong number with no error message**.

| # | What | Where |
|---|---|---|
| **Q1** | Every table read is silently capped at 1000 rows | [app.js:633-642](web/static/app.js#L633-L642) |
| **O1** | Orders are pulled by *created* date, so status never updates → revenue never lands | [sync.py:65-70](syncer/sync.py#L65-L70) |
| **S1** | Settlement re-sync deletes before inserting, without a transaction | [sync_settlement.py:89-91](syncer/sync_settlement.py#L89-L91) |
| **M1** | Landed cost is all-time weighted average → past months' profit changes retroactively | [schema.sql:257-266](sql/schema.sql#L257-L266) |
| **E1** | Purchase CSV import has no dedup → re-import doubles your COGS | [app.js:1053](web/static/app.js#L1053) |

---

## A. Query & pagination (the frontend)

### 🔴 Q1 — Every read is capped at 1000 rows, silently

```js
async function runQuery(table) {
  let q = supabaseClient.from(table).select("*");   // no .limit(), no count
```
[app.js:633-642](web/static/app.js#L633-L642)

Supabase/PostgREST enforces a **default `max-rows` of 1000**. There is no `.limit()`,
no `.range()`, no count check, and no pagination loop anywhere in `runQuery`. Once a
store has more than 1000 orders, order lines, purchases, or settlement lines, **the
tab silently renders the first 1000 and every total is computed from that subset.**

It gets worse: all the roll-ups are **client-side** — `rollUpBySku`
([app.js:1159-1168](web/static/app.js#L1159-L1168)) and `updateSummary`
([app.js:1583-1596](web/static/app.js#L1583-L1596)) sum in JS over whatever arrived.
So the Dashboard's revenue, COGS and net profit are sums over a truncated page, with
no warning. `profit_by_sku` is one row **per SKU per month** — a store with 100 SKUs
crosses 1000 rows in under a year.

Nothing errors. The numbers just quietly become wrong and stay wrong.

**Fix direction:** either paginate with `.range()` until exhausted, or push the
aggregation into SQL (an RPC returning one row per store/month) so the browser never
needs 1000+ rows. The second is the real fix — the client shouldn't be summing
ledgers.

**Test:** see [T-Q1](#t-q1--prove-the-1000-row-cap).

### 🟠 Q2 — No error when a store has zero rows vs. failed to load

`loadTab` catches and shows "Couldn't load data" ([app.js:665-672](web/static/app.js#L665-L672)),
but a **successful query returning 0 rows** renders the same "No products yet" empty
state as a store that genuinely has none. RLS denying access also returns 0 rows, not
an error. So "you lost access to this store" and "this store is new" look identical.

### ⚪ Q3 — Period filter dropdown is capped at 120 months

The `guard++ < 120` at [app.js:613](web/static/app.js#L613) stops runaway option lists,
but silently truncates the dropdown at 10 years. Fine today; know it exists.

---

## B. Syncer — orders

### 🔴 O1 — Orders are fetched by *created* date, so status never updates

```python
created_start_date = (now - timedelta(days=days)).date().isoformat()
for order in client.get_orders(created_start_date):
```
[sync.py:65-70](syncer/sync.py#L65-L70), default `--days 2` ([sync.py:124](syncer/sync.py#L124))

Walmart's `createdStartDate` filters on **when the order was created**, not when it
last changed. An order created on the 1st and shipped on the 5th will **never be
re-fetched** by a `--days 2` cron. Its `order_lines.status` stays frozen at
`Acknowledged` forever.

And `counts_in_profit` is `status in ('shipped','delivered')`
([schema.sql:480](sql/schema.sql#L480)). So that order **never enters profit at all**.
Its revenue silently vanishes — not delayed, *permanently absent* — while the order
still shows in the Orders tab looking fine.

This is the bug most likely to be costing you real reported revenue right now, and it
scales with how long your fulfilment lag is.

**Fix direction:** use `lastModifiedStartDate` (Walmart supports it) instead of, or in
addition to, `createdStartDate` — **(confirm)** against your API version. Or widen the
cron window well past your longest fulfilment lag. The `--days 2` default is only safe
if every order ships within 2 days.

**Test:** [T-O1](#t-o1--orders-stuck-in-a-pre-shipped-status).

### 🟠 O2 — Duplicate keys in one upsert batch abort the whole sync

`sync_orders` accumulates **every** order into one list and issues a single
`.upsert(order_rows)` ([sync.py:96-99](syncer/sync.py#L96-L99)). If the cursor
pagination ever returns the same `purchaseOrderId` twice (overlap at a page boundary),
Postgres raises *"ON CONFLICT DO UPDATE command cannot affect row a second time"* and
**the entire sync fails** — not just that order. Same for `order_lines` on
`(purchase_order_id, line_number)`.

Deduplicate by primary key before the upsert.

### 🟠 O3 — Unbounded memory + one giant request

Same code path: `--days 180` builds every order and line in memory, then sends one
upsert. No batching. Large stores will hit request-size limits or the 300s subprocess
timeout ([app.py:104](web/app.py#L104)) — which the UI reports as a generic
"Sync took too long".

### 🟠 O4 — Only the first status of a multi-status line is kept

```python
def _order_line_status(order_line):
    statuses = order_line.get("orderLineStatuses", {}).get("orderLineStatus", [])
    return statuses[0].get("status") if statuses else None
```
[sync.py:50-52](syncer/sync.py#L50-L52)

A Walmart order line can carry **several status entries with quantities** — 2 shipped,
1 cancelled. This takes `[0]` and stores the **full line quantity** against it. A
partially-cancelled line is therefore counted as **fully sold** (revenue + inventory
depletion), or fully cancelled, depending on array order.

`_charge_total` ([sync.py:33-39](sync.py#L33-L39)) sums all charges for the line
regardless, so revenue doesn't get pro-rated either.

### 🟡 O5 — `orderDate` is assumed to be epoch millis

`dt.datetime.fromtimestamp(raw / 1000, ...)` ([sync.py:30](syncer/sync.py#L30)) raises
`TypeError` if Walmart ever returns an ISO string. Caught by the per-store
`except Exception` ([sync.py:156](syncer/sync.py#L156)) → that store prints `FAILED`
and is skipped entirely, including its catalog.

Note the downstream cost: `profit_by_sku` and `monthly_profit` both filter
`order_date is not null` ([schema.sql:513](sql/schema.sql#L513),
[schema.sql:530](sql/schema.sql#L530)), so an order with an unparseable date is
**silently dropped from all profit**, not flagged.

### 🟡 O6 — Null quantity nulls the whole profit line

`quantity` is `float(quantity) if quantity is not None else None`
([sync.py:90](syncer/sync.py#L90)). In `order_details`, `net_profit` is
`round(product_revenue + shipping_revenue - (quantity * ...) + ...)`
([schema.sql:461-465](sql/schema.sql#L461-L465)) — **null quantity propagates to null
net_profit**, which `sum()` then skips. Revenue silently missing from the roll-up.

### 🟡 O7 — `except Exception` per store, exit code always 0

[sync.py:156-157](syncer/sync.py#L156-L157) swallows any per-store failure and
`main()` returns normally, so `returncode == 0`. The web layer only catches this via
a **substring match on the word "FAILED"** in stdout
([app.py:110](web/app.py#L110)). A cron with no log scraping sees success. Reword the
print and the UI silently stops reporting failures.

### ⚪ O8 — `shipNodeType: "SellerFulfilled"` excludes WFS orders **(confirm)**

```python
params = {..., "shipNodeType": "SellerFulfilled"}
```
[walmart_client.py:82](syncer/walmart_client.py#L82)

This filters to seller-fulfilled orders only. But `fee_category_map` has a
`wfs_label_fee` bucket ([schema.sql:136](sql/schema.sql#L136)) and the docs discuss
WFS fees — implying WFS is in scope. **If you use WFS, those orders are never synced
and their revenue is entirely absent**, while their settlement fees still land in
`settlement_lines` (and match no order line, see [S5](#-s5--settlement-rows-that-match-no-order-line-are-silently-dropped)).

Deliberate or leftover? Worth confirming — it's a one-word change with large
consequences either way.

---

## C. Syncer — catalog

Covered in depth in [UNPUBLISHED_PRODUCTS.md](UNPUBLISHED_PRODUCTS.md). Summary:

### 🔴 C1 — `get_items` stops after the first page if `totalItems` is absent

```python
offset += len(items)
if not items or offset >= data.get("totalItems", 0):
    break
```
[walmart_client.py:108-110](syncer/walmart_client.py#L108-L110)

If the response omits `totalItems`, the default is `0`, so `offset >= 0` is **always
true** and the loop breaks after one page. **Your catalog silently truncates to the
first 200 SKUs** and the sync prints a cheerful "synced 200 catalog products".

Default to `float("inf")` instead of `0`, or drive the loop off an empty page only.

**(confirm)** — depends on whether your API version returns `totalItems`. [T-C1](#t-c1--catalog-truncation) tells you.

### 🟠 C2 — `synced_at` never updates on upsert
[sync.py:107-114](syncer/sync.py#L107-L114) omits it from the payload; defaults fire on
INSERT only. It records first-seen, never moves. Blocks all staleness detection.

### 🟠 C3 — Delisted products keep a stale `published_status` forever
Consequence of C2. See UNPUBLISHED_PRODUCTS.md §U2.

### ⚪ C4 — `lifecycle_status` is synced and never read
Written at [sync.py:113](syncer/sync.py#L113), used nowhere in `app.js`.

---

## D. Syncer — settlement

### 🔴 S1 — Delete-then-insert with no transaction

```python
supabase.table("settlement_lines").delete().eq("store_id", store_id).eq("report_date", report_date).execute()
if rows:
    supabase.table("settlement_lines").insert(rows).execute()
```
[sync_settlement.py:89-91](syncer/sync_settlement.py#L89-L91)

Two separate HTTP calls, no transaction. If the insert fails — network blip, request
too large, the 300s subprocess timeout at [app.py:129](web/app.py#L129) firing between
them — **that date's fees are gone**. Permanently, unless you notice and re-sync.

Missing fees don't error. They make `order_details.fee_source` fall back to `manual`
or `none`, and **profit silently jumps up**. A data-loss bug that looks like good news.

The 300s timeout makes this reachable in normal operation, not just theory.

**Fix direction:** an RPC doing both in one transaction, or a real upsert on a unique
key (see S2) instead of delete+insert.

### 🔴 S2 — No unique constraint, so concurrent syncs duplicate fees

`settlement_lines` has a `uuid` PK and **no unique constraint** on the natural key
([schema.sql:113-126](sql/schema.sql#L113-L126)). Delete-then-insert is the only thing
preventing duplicates.

Two syncs for the same store overlapping — the cron and someone hitting the UI button,
which nothing prevents ([app.py:114](web/app.py#L114) has no lock or rate limit) — can
interleave as `delete → delete → insert → insert`. Result: **every fee row duplicated,
every fee counted twice**, profit understated, no error anywhere.

### 🟠 S3 — Report-date parsing is fragile in both directions

```python
report_dates = [d for d in available_dates if dt.date.fromisoformat(str(d)[:10]) >= cutoff]
```
[sync_settlement.py:69](syncer/sync_settlement.py#L69)

`fromisoformat` raises `ValueError` on anything non-ISO (e.g. `01/15/2026`), killing
the store's entire settlement sync via the outer `except`. And
`get_available_recon_dates` ([walmart_client.py:112-118](syncer/walmart_client.py#L112-L118))
already hedges across **five possible key names**, returning `[]` if none match — which
means "no fees available" and **zero rows synced, silently**, printing
"synced 0 settlement rows across 0 report date(s)".

### 🟠 S4 — `report_date` column is `date`, but the API value is passed through raw

`report_date` is `date not null` ([schema.sql:116](sql/schema.sql#L116)) while the row
dict stores `"report_date": report_date` verbatim
([sync_settlement.py:77](syncer/sync_settlement.py#L77)) — whatever Walmart returned.
If that's a timestamp rather than a bare date, Postgres casts (or rejects) it, and the
**`delete().eq("report_date", report_date)` in S1 may then not match what was
inserted** — so re-syncing appends instead of replacing. Silent duplicate fees again.

### 🟠 S5 — Settlement rows that match no order line are silently dropped

`settlement_pivot` joins on `(store_id, customer_order_id, sku)`
([schema.sql:416-419](sql/schema.sql#L416-L419)). Any settlement row with a **null
`customer_order_id` or null `sku`** — account-level charges like storage fees, monthly
fees, adjustments — matches nothing and **never reaches `net_profit`**.

These fees are real money leaving your account and they are invisible to the P&L.
`sync_settlement` stores them faithfully; `order_details` just can't attach them to a
line. There is currently nowhere in the UI that shows them.

**Test:** [T-S5](#t-s5--orphan-settlement-fees).

### 🟡 S6 — `nextOffset` type confusion

`if not rows or next_offset in (None, "", offset)` ([walmart_client.py:134](syncer/walmart_client.py#L134))
compares a possibly-string `nextOffset` against an int `offset`. `"100" != 100`, so the
guard misses and the loop continues one extra round before self-terminating. Harmless
today; brittle.

---

## E. Walmart client

### 🟠 W1 — Retries don't honour `Retry-After`, and 429s burn the budget

```python
if resp.status_code == 429 or resp.status_code >= 500:
    time.sleep(2 ** attempt)
    continue
```
[walmart_client.py:65-67](syncer/walmart_client.py#L65-L67)

Fixed exponential backoff (max 16s), ignoring Walmart's `Retry-After` header. Five
attempts then `raise_for_status()`. On a sustained rate-limit this exhausts retries in
~30s and fails the store.

### 🟠 W2 — 401 is retried only on the very first attempt

`if resp.status_code == 401 and attempt == 0` ([walmart_client.py:61](syncer/walmart_client.py#L61)).
A token expiring mid-pagination on attempt 3 of a long catalog pull is **not** refreshed
— it falls through to `raise_for_status()`. Combined with the hardcoded TTL (W3), this
is reachable on slow syncs.

### 🟡 W3 — Token TTL is hardcoded, `expires_in` ignored

`TOKEN_TTL_SECONDS = 900` ([walmart_client.py:10](syncer/walmart_client.py#L10)); the
response's actual `expires_in` is discarded at [walmart_client.py:41-42](syncer/walmart_client.py#L41-L42).
If Walmart ever issues a shorter-lived token, every request after its real expiry 401s
— and W2 means only the first one recovers.

### 🟡 W4 — Order cursor detection is a substring match

`if not next_cursor or "hasMoreElements=true" not in next_cursor`
([walmart_client.py:93](syncer/walmart_client.py#L93)). Pagination hinges on a
**literal substring of an opaque cursor string**. If Walmart changes casing, spacing,
or param order, this silently stops after page one — same class as C1: partial data,
success message.

### ⚪ W5 — No timeout budget across the whole sync

Each request has `timeout=30` but the job has no overall budget, so it relies entirely
on the 300s subprocess timeout in `app.py` — which, per S1, is a data-loss trigger for
settlement.

---

## F. Money math (the views)

### 🔴 M1 — Landed cost is an all-time average, so history is not stable

```sql
sum(quantity * unit_cost + coalesce(sales_tax,0) + coalesce(shipping,0))
  / nullif(sum(quantity), 0)
from purchases group by store_id, sku;
```
[schema.sql:257-266](sql/schema.sql#L257-L266)

There is **no date filter**. `sku_landed_cost` averages every purchase ever made, and
`order_details` applies today's average to **every historical line**
([schema.sql:397](sql/schema.sql#L397)).

So: buy the same SKU cheaper in July, and **your April profit changes**. Close the
books on a month, log one late purchase, and last month's reported net profit moves.
Nothing indicates this happened.

That's a defensible modelling choice for a live "what's my margin" view and a
**wrong** one for a P&L that people trust across months — and `monthly_profit` is
presented as exactly that P&L. Worth an explicit decision, documented either way.
FIFO/period-weighted costing is the alternative; it's a real piece of work.

### 🔴 M2 — Fee allocation rounds per line, so allocated ≠ actual

```sql
round((case when l.has_settlement then coalesce(l.walmart_fees,0)
            else -coalesce(l.manual_referral,0) end) * l.fee_share, 2) as eff_walmart
```
[schema.sql:429-434](sql/schema.sql#L429-L434)

The proportional split (`fee_share`, [schema.sql:404-408](sql/schema.sql#L404-L408)) is
good design — it's what stops a SKU on two lines being charged twice. But **each share
is rounded to 2dp independently**. A $10.00 fee across 3 equal lines allocates
3 × $3.33 = **$9.99**. A cent evaporates per split, every time, and it never reconciles
against the settlement total.

Pennies — but they accumulate across thousands of lines and make "why doesn't this tie
out to Walmart's report?" unanswerable. Standard fix: give the remainder to the largest
share.

### 🟠 M3 — Unmapped settlement types default to a **fee** and can invert revenue

```sql
coalesce(fcm.category, 'extra_service_fee') as category
```
[schema.sql:371](sql/schema.sql#L371)

Already the documented #1 gotcha (ARCHITECTURE §7), and the code confirms it: an
unmapped `Sale` row (positive amount) becomes `extra_service_fee`, and `net_profit`
**adds** the fee buckets ([schema.sql:464](sql/schema.sql#L464)) — so unmapped revenue
rows inflate profit.

The `fee_category_map` starts **empty**, so a fresh install with a settlement sync and
no mapping is maximally wrong, in the flattering direction. There's no UI warning for
unmapped types. **This should be a Dashboard warning**, exactly like the no-cost one.

### 🟠 M4 — `warehouse_cost` requires a manual cost row to exist

```sql
coalesce(lc.landed_unit_cost, pc.unit_cost) as unit_cost,
pc.warehouse_cost,
```
[schema.sql:397-398](sql/schema.sql#L397-L398)

`unit_cost` falls back landed → manual, but **`warehouse_cost` comes only from
`product_costs`**. A SKU costed via purchases (the normal path) has no
`product_costs` row, so `warehouse_cost` is null → `coalesce(...,0)` → **silently 0**.

You cannot set a warehouse cost for a SKU without also setting a manual unit cost —
which `sku_landed_cost` then overrides. The warehouse cost column is effectively
unreachable for any SKU you've logged purchases for.

### 🟠 M5 — Zero and negative purchase quantities corrupt landed cost

`nullif(sum(quantity), 0)` guards division by zero, but:
- A purchase with **qty 0 and shipping > 0** adds shipping to the numerator with
  nothing in the denominator → **inflates unit cost for every other purchase of that SKU**.
- **Negative quantity** (a return to supplier, which the UI permits —
  `parseFloat(...) || 0` at [app.js:1045](web/static/app.js#L1045) accepts it) can drive
  `sum(quantity)` to zero → landed cost null → silently falls back to the manual cost,
  or to no cost at all.

No constraint stops either: `quantity numeric not null`
([schema.sql:76](sql/schema.sql#L76)) has no `check (quantity > 0)`.

### 🟡 M6 — `margin_pct` is 0 when revenue ≤ 0

`case when (product_revenue + shipping_revenue) > 0 then ... else 0 end`
([schema.sql:466-472](sql/schema.sql#L466-L472)). A full refund line (zero or negative
revenue, real fees) reports **0% margin rather than a loss**. Same pattern in
`monthly_profit` ([schema.sql:543-546](sql/schema.sql#L543-L546)). Losses display as
break-even.

### 🟡 M7 — The Dashboard KPI row doesn't reconcile

`profit_by_sku` zeroes `net_profit` for costless SKUs but still sums their `revenue`
and `cogs` ([schema.sql:497-500](sql/schema.sql#L497-L500)). So on the Dashboard,
`revenue − cogs − fees ≠ net_profit`. This is **intentional and documented**, and the
warning banner explains it ([app.js:1186-1191](web/static/app.js#L1186-L1191)) — but
only when at least one SKU is affected. Anyone reading the four stat tiles as a P&L
will find they don't add up.

### ⚪ M8 — `count(distinct customer_order_id)` counts post-filter

`monthly_profit.orders` ([schema.sql:536](sql/schema.sql#L536)) counts orders **after**
the `counts_in_profit` filter. An order with one shipped and one cancelled line counts
as one order — correct, but it means "orders" here ≠ orders on the Orders tab. Two
numbers with the same name.

### ⚪ M9 — `profit_by_sku.unit_cost` is current, not historical

Explicitly commented ([schema.sql:501](sql/schema.sql#L501)): identical across months
by design. It's the M1 problem surfaced honestly — the doc is right, the number is
still a present-tense value sitting in a historical row.

---

## G. RLS & multi-tenancy

### 🟠 R1 — Members can edit costs, fee map, and manual fees

ARCHITECTURE §3 says: *"Read/insert policies use [`user_has_store`]; update/delete use
[`user_is_store_admin`]."* **That's not what the schema does.**

| Table | Update policy | Uses |
|---|---|---|
| `purchases` | `admin edit purchases` | `user_is_store_admin` ✅ |
| `damaged_goods` | `admin edit damaged` | `user_is_store_admin` ✅ |
| `inventory_adjustments` | `admin edit adjustments` | `user_is_store_admin` ✅ |
| **`product_costs`** | `edit own costs` [schema.sql:199-200](sql/schema.sql#L199-L200) | **`user_has_store`** ❌ |
| **`fee_category_map`** | `edit own fee map` [schema.sql:218-219](sql/schema.sql#L218-L219) | **`user_has_store`** ❌ |
| **`manual_order_fees`** | `edit own manual fees` [schema.sql:223-224](sql/schema.sql#L223-L224) | **`user_has_store`** ❌ |

A **member** can rewrite unit costs, remap fee categories, and edit manual fees — each
of which directly moves everyone's profit numbers. Given `member` is documented as
"read and add only", this is either three missing policies or a stale doc. Either way
the doc and the code disagree about who can change the money.

### 🟡 R2 — A store can be created with no admin if the insert sequence breaks

`add_store` does three **separate, untransacted** inserts:
stores → store_credentials → store_access ([app.py:291-298](web/app.py#L291-L298)).
If the second or third fails, you get a `stores` row with `active` defaulting to
**true** ([schema.sql:8](sql/schema.sql#L8)) and **no `store_access` grant** — so RLS
hides it from everyone including its creator. It cannot be selected, managed, or
deleted from the UI, but the cron syncer will keep picking it up and printing
"no credentials" forever. Only SQL can clean it up.

### ⚪ R3 — `store_credentials` is correctly locked

RLS enabled, **zero policies** ([schema.sql:178](sql/schema.sql#L178)) → service key
only. This one's right, and it's the most important one. Worth a regression test so it
stays that way — [T-R3](#t-r3--credentials-stay-unreachable).

### ⚪ R4 — Self-demotion guards hold

`set_user_role` and `revoke_user` both block acting on yourself
([app.py:236-237](web/app.py#L236-L237), [app.py:252-253](web/app.py#L252-L253)). Since
you can't demote yourself and only admins can demote, a store can't reach zero admins.
Correct — noting it so nobody "simplifies" it away.

---

## H. API layer

### 🟠 A1 — `list_users()` is unpaginated

`svc.auth.admin.list_users()` is called in `_require_admin`-adjacent paths
([app.py:172](web/app.py#L172), [app.py:212](web/app.py#L212)) with **no pagination**.
Supabase pages this (default 50). Past that:
- `/admin/users` shows **"(unknown)"** instead of emails for users beyond page 1.
- `add_user`'s existence check ([app.py:212](web/app.py#L212)) **misses the existing
  user** and calls `create_user` → duplicate-email error, or a second account.

### 🟠 A2 — Nothing prevents concurrent or repeated syncs

`/sync/orders` and `/sync/settlement` have no lock, no rate limit, no in-flight check
([app.py:88](web/app.py#L88), [app.py:114](web/app.py#L114)). Double-clicking Refresh
spawns two subprocesses against the same store. For settlement that's **S2's duplicate
fees**; for orders it's wasted quota on someone else's Walmart account.

### 🟡 A3 — Members can trigger syncs on the owner's quota

`_require_store_access` checks for **any** `store_access` row, not the role
([app.py:75-84](web/app.py#L75-L84)). A `member` can fire `--days 180` repeatedly and
burn the store owner's Walmart API budget. Consistent with "members can add data", but
it's the one member action that spends someone else's resource.

### 🟡 A4 — Failure detection is a substring match

`failed = proc.returncode != 0 or "FAILED" in output` ([app.py:110](web/app.py#L110),
[app.py:134](web/app.py#L134)). Since `sync.py` swallows exceptions and always exits 0
(O7), **the string "FAILED" in stdout is the only failure signal.** Reword that print
and every sync reports success forever. It also false-positives on a SKU or store named
"FAILED".

### 🟡 A5 — Timeout kills the subprocess mid-write

`subprocess.run(timeout=300)` ([app.py:104](web/app.py#L104)) kills the child on
expiry. If it dies between S1's delete and insert, **that settlement date is gone** and
the user just sees "Sync took too long and was stopped." The error message actively
hides the data loss.

### ⚪ A6 — A new Supabase client per request

`_require_user` ([app.py:54](web/app.py#L54)) and `_service_client`
([app.py:152-154](web/app.py#L152-L154)) construct a fresh client on **every call**, and
`get_user(token)` is a network round-trip per request. No caching. Fine at this scale;
it's the first thing to hurt under load.

---

## I. Data entry & CSV

### 🔴 E1 — Purchase import has no dedup: re-importing doubles your COGS

```js
const { error } = await supabaseClient.from("purchases").insert(payload);
```
[app.js:1053](web/static/app.js#L1053)

Plain `insert`. `purchases` has a **uuid PK and no natural key**
([schema.sql:71-87](sql/schema.sql#L71-L87)), so there is nothing to conflict on and
nothing to detect a repeat. Import the same file twice — or double-click the button —
and **every purchase is duplicated**.

Consequences: `sku_landed_cost` re-averages over doubled rows, `inventory_levels`
doubles `purchased`, and profit shifts across every month at once (via M1). And a
**member cannot undo it** — there's no delete policy for non-admins
([schema.sql:232-233](sql/schema.sql#L232-L233)).

Given `SwiftZarProductPurchase.csv` and `xlsx_to_purchases_csv.py` are sitting in the
repo root, bulk import is clearly a live workflow. This is the one I'd fix first after
Q1.

**Fix direction:** a unique key on something like
`(store_id, sku, purchase_date, order_number)`, or an import-batch id with a
preview-and-confirm step.

### 🟠 E2 — CSV parser breaks on quoted newlines

`text.split(/\r?\n/)` ([app.js:871](web/static/app.js#L871),
[app.js:997](web/static/app.js#L997)) splits into lines **before** parsing quotes.
`parseCsvLine` ([app.js:852-868](web/static/app.js#L852-L868)) handles quotes and
escaped `""` correctly — but only within one already-split line.

A quoted field containing a newline (very common in the `notes` column, and Excel
exports them happily) **shatters into malformed rows**. Depending on where it lands you
get skipped rows or garbage values silently written to the ledger.

### 🟠 E3 — `csvDate` parses ambiguous dates in the browser's locale

```js
const d = new Date(s);
```
[app.js:1016](web/static/app.js#L1016)

`new Date("01/02/2026")` is **January 2nd** under US interpretation. A European
supplier CSV meaning **1 February** lands three weeks early, silently. The ISO
fast-path ([app.js:1015](web/static/app.js#L1015)) only helps when the file is already
ISO.

Worse: if the string is unparseable, `csvDate` **returns the raw string**
([app.js:1017](web/static/app.js#L1017)), which sails past `|| today()`
([app.js:1050](web/static/app.js#L1050)) — that only catches null — and hits Postgres
as an invalid `date`, failing the whole import with a raw driver error.

### 🟡 E4 — `today()` uses UTC while `csvDate` deliberately doesn't

```js
function today() { return new Date().toISOString().slice(0, 10); }
```
[app.js:1068-1070](web/static/app.js#L1068-L1070)

`csvDate` carries an explicit comment about avoiding `toISOString` so a timezone
doesn't shift the day ([app.js:1018](web/static/app.js#L1018)) — and then `today()`
does exactly that. Evening in the Americas, `today()` returns **tomorrow's** date. The
same file imported at 6pm and 8pm gets different purchase dates.

### 🟡 E5 — `parseFloat(x) || 0` turns bad input into a silent zero

[app.js:1045-1048](web/static/app.js#L1045-L1048), [app.js:1901](web/static/app.js#L1901),
[app.js:1926](web/static/app.js#L1926). A `unit_cost` of `"12,50"` (European decimal
comma) → `parseFloat` → `12` — **wrong by a factor, not rejected**. `"abc"` → `NaN` →
`|| 0` → **a free product**. Zero is a legitimate cost, so nothing downstream can tell
the difference between "free" and "unparseable".

### 🟡 E6 — No validation that a purchase SKU exists

`purchases.sku` is free text with no constraint. A typo creates a **new phantom SKU**
with its own inventory row (via the `combined` union) and its own landed cost, while
the real SKU silently keeps its old cost. Nothing flags the near-miss.

### ⚪ E7 — Cost CSV export/import round-trip loses unpublished SKUs

Covered as U17 in [UNPUBLISHED_PRODUCTS.md](UNPUBLISHED_PRODUCTS.md) — `catalogExport`
is built inside the render loop ([app.js:1216-1221](web/static/app.js#L1216-L1221)), so
any filter applied to the render silently narrows the export.

---

## J. Ops, secrets, deployment

### 🟠 X1 — `syncer/secrets.py` shadows the stdlib `secrets` module

The file is literally named `secrets.py` and sits next to the scripts, so
`sys.path[0]` makes **`import secrets` resolve to it for the whole process** — including
for any library that imports the standard library's `secrets` (used for token
generation in many packages, and reachable from `supabase`/`requests` dependency
chains).

`web/app.py` **already works around this**, with a comment saying so:

> *"Same Fernet scheme as syncer/secrets.py, inlined so the web app doesn't import a
> module named `secrets` and shadow the stdlib one."* — [app.py:139-140](web/app.py#L139-L140)

So the workaround exists, the duplication exists, and the root cause is still there.
`/sync/*` also runs the syncer as a subprocess partly to keep those imports resolving
([app.py:92-94](web/app.py#L92-L94)). Renaming to `crypto.py` removes the hazard, the
duplicated Fernet code, and one reason for the subprocess.

### 🟠 X2 — Rotating `WALMART_SECRET_KEY` bricks every stored credential

`secrets.py` ([syncer/secrets.py:7-9](syncer/secrets.py#L7-L9)) takes a single Fernet
key from the environment. There's **no key id, no versioning, no `MultiFernet`**. Rotate
the key and every `encrypted_client_secret` becomes permanently undecryptable — every
store fails with an opaque `InvalidToken` caught by the per-store `except`, printing
`FAILED` and nothing else. Re-entering every store's secret by hand is the only
recovery.

`Fernet(key)` also raises on a malformed key, and `os.environ["WALMART_SECRET_KEY"]`
raises a bare `KeyError` at import time if unset.

### 🟡 X3 — No migration tracking

Already flagged in ARCHITECTURE §3, restating because it interacts with everything
here: nothing records which `migration_*.sql` have run. There are **14 migration
files** and the only way to know your database's actual state is to inspect it. Any fix
in this document becomes migration #15 with the same problem.

### 🟡 X4 — No backups

ARCHITECTURE §7 says it: *"No backups, no version history."* Given S1 (settlement
delete/insert), E1 (duplicate imports) and the cascade in
`delete_store` ([app.py:352](web/app.py#L352)), there are at least three ways to
destroy data and no way to get it back.

---

## Test plan

**There is no test runner in this repo** — no pytest in
[requirements.txt](requirements.txt), and `od_baseline.json` is a hand-captured
`order_details` snapshot. The probes below are written to be pasted into the Supabase
SQL editor or the browser console; **none of them have been run.** Replace
`'<store-uuid>'` throughout.

Run the SQL as a normal user (RLS applies) unless noted.

### T-Q1 — Prove the 1000-row cap

Browser console, signed in:

```js
const { data, count } = await supabaseClient
  .from("order_details").select("*", { count: "exact" });
console.log("rows returned:", data.length, "rows that exist:", count);
```

**Expect the bug when** `data.length` is exactly 1000 and `count` is larger. That gap
is the amount of your business currently missing from the Dashboard.

Same check for `profit_by_sku`, `orders`, `order_lines`, `purchases`,
`settlement_lines`.

### T-O1 — Orders stuck in a pre-shipped status

```sql
select ol.status, count(*) as lines,
       min(o.order_date)::date as oldest,
       round(sum(ol.product_revenue + ol.shipping_revenue), 2) as revenue_not_counted
from order_lines ol
join orders o on o.store_id = ol.store_id and o.purchase_order_id = ol.purchase_order_id
where ol.store_id = '<store-uuid>'
  and lower(trim(coalesce(ol.status, ''))) not in ('shipped', 'delivered')
  and o.order_date < now() - interval '14 days'
group by ol.status
order by revenue_not_counted desc;
```

**Expect:** cancelled lines only. Any `Acknowledged`/`Created` line older than two
weeks is O1 — an order that shipped in reality but whose status the syncer never came
back for. `revenue_not_counted` is the money missing from your P&L because of it.

### T-C1 — Catalog truncation

```sql
select count(*) from products where store_id = '<store-uuid>';
```

**Expect the bug when** the count is exactly 200 (or an exact multiple of the `limit`
in `get_items`). Compare against the SKU count in Seller Center. See
[UNPUBLISHED_PRODUCTS.md](UNPUBLISHED_PRODUCTS.md) T1/T2 for the `synced_at` probes.

### T-S2 — Duplicate settlement rows

```sql
select report_date, customer_order_id, sku, amount_type, amount, count(*)
from settlement_lines
where store_id = '<store-uuid>'
group by 1,2,3,4,5
having count(*) > 1
order by count(*) desc;
```

**Expect:** empty. Any row means fees are double-counted and profit is understated.
This is also the query that tells you whether you need the unique constraint.

### T-S5 — Orphan settlement fees

```sql
select sl.amount_type,
       count(*) as rows,
       round(sum(sl.amount), 2) as total_amount
from settlement_lines sl
left join orders o
  on o.store_id = sl.store_id and o.customer_order_id = sl.customer_order_id
where sl.store_id = '<store-uuid>'
  and (sl.customer_order_id is null or sl.sku is null or o.customer_order_id is null)
group by sl.amount_type
order by total_amount;
```

**Expect:** rows, probably. `total_amount` is real money that **never reaches
`net_profit`** because `order_details` can't join it to a line. Anything large here
means your profit is overstated by roughly that much.

### T-M2 — Fee allocation doesn't reconcile

```sql
with allocated as (
  select customer_order_id, sku,
         sum(walmart_fees + wfs_label_fees + refund_fees + extra_service_fees) as alloc
  from order_details
  where store_id = '<store-uuid>' and fee_source = 'settlement'
  group by 1, 2
),
actual as (
  select sl.customer_order_id, sl.sku, sum(sl.amount) as raw
  from settlement_lines sl
  left join fee_category_map fcm
    on fcm.store_id = sl.store_id and fcm.amount_type = sl.amount_type
  where sl.store_id = '<store-uuid>'
    and coalesce(fcm.category, 'extra_service_fee') <> 'ignore'
  group by 1, 2
)
select count(*) as split_orders_off_by_pennies,
       round(sum(abs(a.alloc - b.raw)), 2) as total_drift
from allocated a join actual b using (customer_order_id, sku)
where abs(a.alloc - b.raw) > 0.001;
```

**Expect:** `total_drift` near zero. Non-zero is M2's rounding leak — small, but it's
the reason your numbers won't tie to Walmart's report exactly.

### T-M3 — Unmapped settlement types (the #1 documented gotcha)

```sql
select sl.amount_type, count(*) as rows, round(sum(sl.amount), 2) as total
from settlement_lines sl
left join fee_category_map fcm
  on fcm.store_id = sl.store_id and fcm.amount_type = sl.amount_type
where sl.store_id = '<store-uuid>' and fcm.category is null
group by sl.amount_type
order by abs(sum(sl.amount)) desc;
```

**Expect:** empty. Anything with a **positive** total is revenue-like and is currently
**inflating your profit** by that amount. This query is a good candidate to become a
Dashboard warning.

### T-M5 — Purchases that corrupt landed cost

```sql
select sku, quantity, unit_cost, sales_tax, shipping, purchase_date
from purchases
where store_id = '<store-uuid>' and quantity <= 0;
```

**Expect:** empty. Any row is silently distorting `sku_landed_cost` for that SKU, and
therefore its profit in every month.

### T-E1 — Duplicate purchases

```sql
select sku, purchase_date, quantity, unit_cost, order_number, count(*)
from purchases
where store_id = '<store-uuid>'
group by 1,2,3,4,5
having count(*) > 1
order by count(*) desc;
```

**Expect:** empty. Matches are probable double-imports. Each one inflates inventory and
drags that SKU's landed cost toward the duplicated price.

### T-M1 — Retroactive profit drift

Not a query — a procedure, because it's about *change over time*:

1. Snapshot: `create table profit_snapshot as select * from monthly_profit where store_id = '<store-uuid>';`
2. Log any new purchase for a SKU that sold in a **previous** month.
3. Compare:

```sql
select s.month, s.net_profit as before, m.net_profit as after,
       round(m.net_profit - s.net_profit, 2) as drift
from profit_snapshot s
join monthly_profit m on m.store_id = s.store_id and m.month = s.month
where m.net_profit is distinct from s.net_profit;
```

**Expect (today):** closed months move. That's M1, reproduced. This is the test that
turns "should we do FIFO?" from an opinion into a number.

### T-R3 — Credentials stay unreachable

Browser console, signed in as a **store admin**:

```js
const { data, error } = await supabaseClient.from("store_credentials").select("*");
console.log({ rows: data?.length, error });
```

**Expect:** `rows: 0` (RLS with no policies returns empty, not an error). Anything else
means a policy was added and your Walmart secrets are reachable from the browser. Worth
running after **any** schema change.

### T-R1 — Can a member edit costs?

Signed in as a **member** (not admin):

```js
const { error } = await supabaseClient.from("product_costs")
  .update({ unit_cost: 999 }).eq("store_id", "<store-uuid>").eq("sku", "<some-sku>");
console.log(error ? "blocked (doc is right)" : "ALLOWED (doc is wrong)");
```

**Expect today:** allowed — which contradicts ARCHITECTURE §3. Decide which is true,
then fix the code or the doc.

### Unit tests worth having

`sync_products`, `sync_orders` and `sync_settlement` all take `supabase` and `client`
as arguments, so they're injectable with `MagicMock` — no network needed. Needs
`pip install pytest`. The highest-value cases:

| Test | Guards |
|---|---|
| `get_items` keeps paginating when `totalItems` is absent | **C1** |
| `sync_products` sends `synced_at` on every upsert | **C2** |
| `sync_orders` dedups `purchase_order_id` before upsert | **O2** |
| `_order_line_status` handles multi-status lines | **O4** |
| `_parse_order_date` handles ISO strings and null | **O5** |
| `sync_settlement` doesn't delete when the fetch yielded nothing | **S1** |
| `_get` retries a 401 on any attempt, not just the first | **W2** |

See [UNPUBLISHED_PRODUCTS.md §5](UNPUBLISHED_PRODUCTS.md) for a worked example of the
mocking pattern.

---

## Suggested order of work

Ranked by *(silently wrong money) × (likelihood you're hit today)*:

1. **Q1** — the 1000-row cap. Everything else is arguing about numbers that are already
   being computed on partial data. Run T-Q1 first; if it trips, nothing else on this
   list can be measured honestly until it's fixed.
2. **E1** — purchase-import dedup. Bulk import is clearly live (the CSV is in the repo
   root), and members can't undo it.
3. **S1 + S2** — wrap settlement replace in a transaction, add the unique key. Both are
   data loss / double counting with no signal.
4. **O1** — confirm whether orders ever get their status updated. Run T-O1; if
   two-week-old `Acknowledged` lines exist, revenue is missing right now.
5. **C1** — one-word fix (`totalItems` default), silently truncates the catalog.
6. **M3** — turn T-M3 into a Dashboard warning. Cheap, and it's the documented #1 cause
   of wrong numbers.
7. **R1** — decide whether members may edit costs, then align code and doc.
8. **M1** — the retroactive-cost decision. Not a bug fix; a modelling call that needs a
   deliberate answer and a doc.
9. Everything else.

**Cross-cutting theme.** Nearly every 🔴 here shares one shape: *the system continues
successfully and reports a number that is wrong.* Truncated reads, dropped fees,
missing revenue, doubled purchases — all of them print success. The single highest-
leverage change isn't any individual fix, it's making these conditions **loud**:
Dashboard warnings for unmapped fee types, orphan settlement rows, row-count
truncation, and stale order statuses, in the same style as the existing no-cost banner
([app.js:1186-1191](web/static/app.js#L1186-L1191)) — which is the one place this
codebase already does exactly the right thing.
