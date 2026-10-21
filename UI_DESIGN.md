# Lucrum — UI Design

Status: **draft for review**. Depends on `DATA_MODEL.md` and `ORDER_FLOW.md`.

Decisions carried in: stay vanilla with **no build step**, split into native ES
modules; **orders-first** (the daily job); restructure navigation around the new
model.

---

## 1. Design principles

1. **Orders first.** Monitoring incoming orders is the daily task, so that is the
   landing view — not a dashboard of aggregates.

2. **Every number carries its confidence.** A figure built on partial data must
   never render as a plain confident number. This is the whole point of the
   rebuild expressed in pixels.

3. **Fix the data where you already are.** Purchases are 3.5 months stale while
   orders get watched daily. The cost-entry prompt belongs *inside* the orders
   view, not behind a tab that is never opened.

4. **Any number can be opened.** Click a figure, see the ledger entries that
   compose it. "Why is this number this?" becomes a click, not an investigation.

5. **Aggregate in Postgres, never in JavaScript.** See §6.

---

## 2. Navigation

```
  ORDERS            ← default landing
  MONEY        ▸ P&L · SKU Profit
  INVENTORY    ▸ Stock · Purchases · Returns · Adjustments
  ADMIN        ▸ Sync · Fee Mapping · Periods · Team · Stores
```

Changes from today's ten flat tabs:

| Today | Becomes | Why |
|---|---|---|
| Dashboard | **removed** | Aggregates move into P&L. A separate dashboard duplicated Profit and was the view that disagreed with the others |
| Order Details | **Orders** (landing) | Promoted to the primary surface |
| Profit | Money ▸ P&L | |
| — | Money ▸ SKU Profit | Was folded into Dashboard |
| Products + Inventory | Inventory ▸ Stock | Catalog and stock are one question: what do I have |
| Purchases | Inventory ▸ Purchases | |
| Returns | Inventory ▸ Returns | |
| Damaged | Inventory ▸ Adjustments | Damaged and adjustments both hold **0 rows**; one surface is enough |
| Team, Stores | Admin | unchanged |
| — | Admin ▸ **Fee Mapping** | **No UI exists today**, yet this is the most likely cause of wrong fee numbers |
| — | Admin ▸ **Sync** | `sync_runs` health; "when did settlement last land?" is currently unanswerable |
| — | Admin ▸ **Periods** | Close a month; see what blocks close |

---

## 3. Persistent header

Visible on every view:

```
┌────────────────────────────────────────────────────────────────────────┐
│  Swiftzar ▾     October 2026 ▾  (provisional)                          │
│  Orders 4m ago · Settlement 12d ago, next ~Nov 3   ⚠ 22% of SKUs costed│
└────────────────────────────────────────────────────────────────────────┘
```

- **Period badge** reads `provisional` or `closed`, always.
- **Sync freshness** per feed, from `sync_state`. Stale beyond a threshold turns
  amber. Settlement shows the expected next recon date, since it arrives biweekly.
- **Completeness chip** is a live link to whatever is blocking confidence. On your
  data today it reads `22% of SKUs costed`.

---

## 4. Orders (landing)

Default range: **last 7 days**. This is an operational feed, not an archive.

```
┌─ Orders ──────────────────────── last 7 days ▾ ── [Sync now] ─┐
│                                                                │
│  12 orders · $284.10 revenue · net ≥ $61.20                   │
│  ⚠ 4 lines have no cost   ⏳ 9 lines awaiting settlement       │
│                                                                │
│  Date   SKU          Qty  Revenue   Cost    Fees    Net        │
│  ─────────────────────────────────────────────────────────────│
│  10-21  SWIFTZAR107   1    $38.84  $35.00   ⏳      ≥ $3.84  ⏳ │
│  10-20  SWIFTZAR112   2    $24.50   —       ⏳      —        ⚠  │
│                                    └ Add purchase              │
│  10-19  SWIFTZAR105   1    $31.20  $29.97  $4.21   $-2.98   ✓ │
└────────────────────────────────────────────────────────────────┘
```

Badges:

| | Meaning |
|---|---|
| `✓` | Complete — cost known, fees settled. The number is final |
| `⏳` | Fees pending — sale is not yet in a recon file |
| `⚠` | No cost basis — this line's profit is **unknown, not zero** |
| `↩` | Returned; reversal posted in a later period |

**`≥` prefix.** A total that excludes unknowns renders as `≥ $61.20`, never
`$61.20`. Today's app shows uncosted lines as `$0` profit, which is
indistinguishable from genuinely break-even. The `≥` is the smallest honest
change to that.

### Inline cost entry

Where a line shows `⚠`, an **Add purchase** action opens a drawer prefilled with
SKU and product name, needing only quantity, unit cost, tax, shipping and date.

This is the highest-leverage element in the design. Purchases stopped
`2026-07-08` while sales ran to `2026-10-21`, so 28 of 36 sold SKUs have no cost.
A separate Purchases tab has demonstrably not got this done. Putting entry at the
moment of noticing is the intervention most likely to work.

---

## 5. Drill-down drawer

Clicking any money figure anywhere opens the entries behind it:

```
┌─ SWIFTZAR105 · order 108...4471 ────────────────────── ✕ ─┐
│  2026-10-19  revenue_product                     +$31.20  │
│  2026-10-19  cogs           @ $29.97 moving avg  −$29.97  │
│  2026-10-19  warehouse                            −$1.50  │
│  2026-11-03  fee_referral   settlement 11032026   −$2.71  │
│              effective for 2026-10-19                     │
│  ─────────────────────────────────────────────────────── │
│  Net                                              −$2.98  │
│                                                            │
│  Cost basis: 3 purchases before 2026-10-19 ▸              │
└────────────────────────────────────────────────────────────┘
```

Every entry shows its source and, where it differs, the `effective_for` date. The
moving-average cost basis expands to the purchases that produced it.

This is what makes the system auditable rather than merely plausible.

---

## 6. Aggregate in Postgres, never in JavaScript

Today `runQuery` (`app.js:634`) issues `.select("*")` with no `.range()`, so
PostgREST silently caps at 1000 rows and the browser sums a truncated page. At 186
rows this is currently harmless — but the fix is not pagination.

**The fix is not fetching rows to sum at all.** With the ledger, `monthly_pnl`
returns ~12 pre-aggregated rows instead of thousands of lines. The browser
displays; Postgres computes.

Rule: a view feeding a *total* must return that total already summed. Raw-row
views (Orders) stay paginated with explicit `.range()` and a visible "showing N
of M".

---

## 7. Module structure

Native ES modules via `<script type="module">`. No bundler, no npm, no build.
Per-view `import()` preserves the existing lazy-load behaviour using the platform
instead of a hand-rolled registry.

```
web/static/
  index.html
  css/   base.css · layout.css · components.css
  js/
    main.js            bootstrap, auth, routing
    supabase.js        client; every query here, with explicit .range()
    state.js           store + period selection, cache invalidation
    format.js          money, dates, the ≥ / badge rules
    components/
      table.js  drawer.js  badge.js  form.js
    views/
      orders.js  pnl.js  skus.js  stock.js  purchases.js
      returns.js  adjustments.js
      admin/  sync.js  fees.js  periods.js  team.js  stores.js
```

`format.js` owns badge and `≥` logic so confidence rendering stays consistent —
one place to change, impossible to forget in a new view.

Splitting 2,060 lines is also what makes the frontend reviewable; no single file
should hold ten views again.

---

## 8. New admin surfaces

**Sync** — per feed: last success, watermark, rows written, last error, manual
trigger. Reads `sync_runs` / `sync_state`.

**Fee Mapping** — every distinct `amount_type` seen in settlement with its
mapping. Unmapped types appear at the top in red with their row count and dollar
total, and **block period close**. Today the map holds 4 rows with no UI at all,
and an unmapped type silently becomes a fee.

**Periods** — month list with status, and for open months a close-readiness
checklist:

```
  October 2026 — provisional
    ✓  Settlement ingested through 10-20
    ✗  3 unmapped amount types          → Fee Mapping
    ✗  41 uncosted lines ($612 revenue) → Orders
    ✓  No lines still in return window
                                              [ Close month ] (disabled)
```

Close is blocked, with each blocker a link to its fix. This converts "I don't
trust the numbers" into a finite checklist.

---

## 9. What this changes for you today

On current data the app would report:

> **October 2026 — provisional**
> Revenue $2,309 · net **≥ $1,240** across 169 lines
> ⚠ 41 lines uncosted ($612 revenue) · ⏳ 166 lines awaiting settlement

Versus today's confident, wrong single figure. Same data — the difference is that
the new version tells you it is incomplete, and exactly how.

---

## 10. Open questions

1. **Default Orders range** — 7 days, or since-last-visit?
2. **Dashboard removal.** Aggregates fold into P&L. Confirm you will not miss a
   single landing summary.
3. **Mobile.** Is this ever used on a phone? Currently desktop-table-shaped, and
   an orders feed is the one view that would plausibly be checked on mobile.
4. **`≥` notation** — correct for you, or prefer an explicit "41 lines excluded"
   line with no symbol?
5. Still open from `DATA_MODEL.md` §12 and `ORDER_FLOW.md` §10.
