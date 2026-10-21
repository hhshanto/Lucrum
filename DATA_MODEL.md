# Lucrum — Data Model Design

Status: **draft for review**. Supersedes nothing yet; `sql/schema.sql` is still live.

This document defines the money model. Order flow and UI are downstream of it and
are designed separately, after this is agreed.

---

## 1. The problem this solves

Today the system **cannot close a month**. Costs have no time dimension, so past
profit is recomputed from present state on every read:

```sql
-- schema.sql:257 — sku_landed_cost
sum(quantity * unit_cost + sales_tax + shipping) / sum(quantity)
from purchases group by store_id, sku        -- no date filter
```

Sell 10 units in January at $5. Buy 100 more in March at $8. **January's profit
changes in March.** `schema.sql:501` concedes the point: *"unit_cost / cost_source
are current values, identical across months."*

Returns compound it. A shipped line counts toward profit; when it is returned,
`status` flips and `counts_in_profit` (`schema.sql:480`) goes false — the revenue
does not get *reversed*, it **vanishes from history**.

The numbers aren't merely wrong. They are **unstable**: the same query over the
same month returns different answers on different days, and nothing says so.

---

## 2. Principles

1. **Record immutable facts. Derive reports from facts.**
   Nothing that has been posted is ever updated or deleted. Corrections are new,
   dated, reversing entries.

2. **COGS is bound to the sale at the moment it becomes knowable**, then frozen.

3. **Unknown is not zero.** Today an uncosted SKU contributes `$0` profit, which
   reads identically to a genuinely break-even SKU. These must be distinguishable.

4. **A reported number carries its own completeness.** "Profit was $1,240" is
   useless without "across 169 lines, 41 of which have no cost and 166 no fees."

5. **Fail loud on unclassified input.** An unmapped settlement `amount_type`
   currently defaults to `extra_service_fee` (`schema.sql:371`), so an unrecognised
   *revenue* row silently becomes a fee and double-counts. The default must be
   quarantine, not guess.

---

## 3. Three layers

```
┌─ Layer 1: SOURCE MIRRORS ──────────────── mutable, owned by Walmart ─┐
│  orders · order_lines · products · settlement_lines                  │
│  Re-synced freely. Never read directly for money.                    │
└──────────────────────────────────────────────────────────────────────┘
                              │ projection
┌─ Layer 2: SELLER FACTS ───────────────── immutable, owned by you ────┐
│  purchases · damaged_goods · inventory_adjustments · manual fees      │
└──────────────────────────────────────────────────────────────────────┘
                              │ posting
┌─ Layer 3: LEDGER ──────────────────────── append-only, the spine ────┐
│  ledger_entries                                                       │
│  Every number the app reports is sum(amount) over a date range.       │
└──────────────────────────────────────────────────────────────────────┘
```

Layer 1 is allowed to change — Walmart flips an order from `Shipped` to
`Delivered` to `Returned`. Layer 3 never changes; a status transition *emits* a
new entry.

---

## 4. The ledger

```sql
create table ledger_entries (
  entry_id      uuid primary key default gen_random_uuid(),
  store_id      uuid not null references stores(store_id) on delete cascade,

  entry_date    date not null,          -- the date this hits the P&L
  entry_type    text not null,
  sku           text,
  quantity      numeric,
  amount        numeric not null,       -- SIGNED: income +, cost -

  source_kind   text not null,          -- order_line | settlement | purchase | manual | adjustment | correction
  source_id     text not null,          -- natural key back to the origin row
  reverses      uuid references ledger_entries(entry_id),
  effective_for date,                   -- set when entry_date <> the economic date
  note          text,
  posted_at     timestamptz not null default now(),

  unique (store_id, source_kind, source_id, entry_type)
);
```

`entry_type` is closed:

| type | sign | source |
|---|---|---|
| `revenue_product` | + | order line, on realization |
| `revenue_shipping` | + | order line, on realization |
| `cogs` | − | computed, frozen at realization |
| `warehouse` | − | `product_costs.warehouse_cost` |
| `fee_referral` | − | settlement, else manual |
| `fee_label` | − | settlement, else manual |
| `fee_refund` | − | settlement |
| `fee_other` | − | settlement, **only when explicitly mapped** |
| `return_revenue` | − | reverses `revenue_*` |
| `return_cogs` | + | reverses `cogs` |
| `damage` | − | `damaged_goods` at moving-average cost |
| `correction` | ± | late-arriving fact against a closed period |

**Profit for any period is `sum(amount)` filtered by `entry_date`.** Nothing is
recomputed. This is the whole point.

The `unique` constraint makes posting idempotent — re-running a sync cannot
double-post.

### No UPDATE or DELETE

```sql
create policy "read own ledger"  on ledger_entries for select using (user_has_store(store_id));
create policy "post own ledger"  on ledger_entries for insert with check (user_is_store_admin(store_id));
-- deliberately no update / delete policy, for anyone, ever
```

---

## 5. Costing: moving average at time of sale

Per `(store_id, sku)`, walk every inventory event in chronological order,
carrying running `qty_on_hand` and `value_on_hand`:

| event | effect |
|---|---|
| purchase `q` at landed total `c` | `qty += q` ; `value += c` |
| sale `q` | `unit = value / qty` ; post `cogs = -(q × unit)` ; `qty -= q` ; `value -= q × unit` |
| return `q` | reverse that line's original frozen `cogs` ; `qty += q` ; `value += cogs` |
| damage `q` | `unit = value / qty` ; post `damage` ; `qty -= q` ; `value -= q × unit` |

Landed total per purchase is unchanged from today:
`quantity × unit_cost + sales_tax + shipping`.

**Ordering** is `(event_date, created_at, id)`. The `id` tiebreaker matters: it
makes the walk deterministic, so the same inputs always produce the same COGS.

### Sale with no cost basis

`qty_on_hand = 0` and no prior purchase — currently **28 of your 36 sold SKUs**.

Post **no `cogs` entry at all**, and record the gap:

```sql
create table uncosted_lines (
  store_id   uuid not null,
  source_id  text not null,          -- order line natural key
  sku        text not null,
  quantity   numeric not null,
  sale_date  date not null,
  reason     text not null,          -- no_purchase_history | insufficient_qty
  resolved_by uuid references ledger_entries(entry_id),
  primary key (store_id, source_id)
);
```

This is principle 3 made concrete. The month's profit is then reported as
*"$X across 169 lines, 41 uncosted"* — not a confident, wrong number.

### Backdated purchases

Your purchases stop `2026-07-08`; sales run to `2026-10-21`. Backfilling is
guaranteed, so the model must answer it explicitly:

> **A backdated purchase never rewrites posted COGS.**
> It is replayed forward. For each affected sale:
> - sale's period still **open** → post `cogs` dated to the sale
> - sale's period **closed** → post `correction` dated to the current open period,
>   with `effective_for` = original sale date

History stays reproducible; the books still end up right. This is how prior-period
adjustments work in real accounting, and it is why `effective_for` exists.

---

## 6. Returns

Decided: **reverse in the return's month.**

When `order_lines.status` transitions into a return/refund state, post
`return_revenue` and `return_cogs` dated to the transition, each pointing at the
original entry via `reverses`.

January keeps the profit January reported. March carries the reversal.

Three reasons this beats restating:
1. Walmart books refunds on the refund date. Restating would make the books
   permanently irreconcilable with settlement — destroying the only independent
   oracle.
2. Reversing is lossless. It knows both dates, so a restated view is derivable
   from it. The converse is false.
3. A number that changes after you quote it was never trustworthy.

The question restating answers better — *"was this SKU profitable over its
life?"* — is served by a separate lifetime-per-SKU view over the same entries.

---

## 7. Settlement lag and period close

Observed recon dates: `07/28, 08/11, 09/08, 09/22, 10/20` — **biweekly batches,
lagging the sale**. So fees for a late-October sale may not exist until November.

```sql
create table periods (
  store_id   uuid not null references stores(store_id) on delete cascade,
  month      date not null,            -- first of month
  status     text not null default 'open' check (status in ('open','closed')),
  closed_at  timestamptz,
  closed_by  uuid references auth.users(id),
  primary key (store_id, month)
);
```

Rules:
- A month may be closed only once settlement covering it has been ingested.
- Once closed, **no entry may be inserted with `entry_date` in that month**
  (enforced by trigger). Late facts post to the open period as `correction`.
- Before close, a month is explicitly labelled *provisional* in the UI.

**Fees are not accrued or estimated.** An unsettled sale shows revenue and COGS
with fees flagged pending. Guessing a referral percentage would reintroduce
exactly the invented-number problem we are removing.

---

## 8. Fee mapping: fail loud

Replace the silent `coalesce(fcm.category, 'extra_service_fee')` default.

```sql
alter table fee_category_map
  add constraint fee_category_map_category_check
  check (category in ('fee_referral','fee_label','fee_refund','fee_other','revenue','ignore'));
```

An `amount_type` with no mapping is **not posted**. It lands in a quarantine view:

```sql
create view unmapped_amount_types with (security_invoker = on) as
select sl.store_id, sl.amount_type, count(*) as rows, sum(sl.amount) as total
from settlement_lines sl
left join fee_category_map m
  on m.store_id = sl.store_id and m.amount_type = sl.amount_type
where m.amount_type is null
group by sl.store_id, sl.amount_type;
```

Anything in this view blocks period close. Unmapped money is visible and loud
instead of silently becoming a fee.

Current map is only 4 rows (`Sale`, `Shipping`, `Tax` → ignore; `Service Fee` →
walmart_fee), which will need expanding once real recon rows land.

---

## 9. Completeness as a first-class output

```sql
create view period_completeness with (security_invoker = on) as
select
  store_id, month,
  count(*)                                        as lines,
  count(*) filter (where has_cost)                as lines_costed,
  count(*) filter (where has_fees)                as lines_with_fees,
  round(100.0 * count(*) filter (where has_cost) / nullif(count(*),0), 1) as pct_costed,
  sum(revenue) filter (where not has_cost)        as revenue_uncosted
from ...;
```

On today's data this reports: **22% of sold SKUs costed, 2% of realized lines
with fees** — which is the honest headline, and the one the current dashboard
cannot say.

---

## 10. What survives from the current schema

Unchanged: `stores`, `store_access`, `store_credentials`, `orders`, `order_lines`,
`products`, `purchases`, `damaged_goods`, `inventory_adjustments`,
`settlement_lines`.

Rewritten to read the ledger: `order_details`, `profit_by_sku`, `monthly_profit`.

Retired: `sku_landed_cost` as a *profit* input. It stays as an informational
"current replacement cost" display only — never as COGS.

Added: `ledger_entries`, `periods`, `uncosted_lines`, `schema_migrations`,
`unmapped_amount_types`, `period_completeness`.

Deliberately **not** changed: `sku` remains the join key. Walmart's own settlement
data keys on SKU, so matching that is a feature. SKU reuse is logged as a known
limitation with a detection query, not solved with surrogate product IDs you do
not need at 36 SKUs.

---

## 11. Staged landing

Agreed approach: design the ledger, arrive via frozen COGS.

| Stage | Delivers | Unlocks |
|---|---|---|
| **A1** | `schema_migrations`; fix the `MMDDYYYY` parse bug | Know what is deployed; settlement can finally load |
| **A2** | `ledger_entries` + posting for `revenue_*`, `cogs`; backfill 186 lines | **Months stop mutating** |
| **A3** | `uncosted_lines` + `period_completeness` | Numbers state their own confidence |
| **B1** | Fee posting from settlement; quarantine view | Largest error source closed |
| **B2** | Return reversal entries | History stops disappearing |
| **B3** | `periods` + close enforcement | A month can be final |

A2 is the point at which the headline complaint is fixed.

---

## 12. Open questions

1. **Warehouse cost** is currently a flat per-unit figure on `product_costs`, with
   no date dimension — same retroactive-mutation flaw as landed cost. Freeze it
   per sale like COGS, or treat it as a period expense rather than per-unit?
2. **Cancelled lines** (12 in your data) never realize. Confirmed they should
   post nothing at all, rather than posting and reversing?
3. **Negative shipping** appears in your purchase data (`-3.20`, a supplier
   credit). It currently reduces landed cost, which seems right — confirm.
4. **Who may post?** The DDL above restricts inserts to store admins. With one
   user this is moot, but it is a deliberate choice worth confirming.
