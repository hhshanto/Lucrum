# Lucrum — Architecture & Orientation

How the project actually fits together. [README.md](README.md) covers setup and
running; this covers the design and the parts that will bite you.

---

## 1. What Lucrum is

A small multi-tenant analytics dashboard for Walmart Marketplace sellers.

> **Walmart Marketplace API → a Python syncer → Supabase Postgres → a browser dashboard**,
> where each logged-in user sees only the stores they're authorized for.

The defining design choice: **access control lives in the database, not the app.**
Postgres Row Level Security (RLS) scopes every query to the stores a user is
granted in `store_access`. The browser talks to Supabase directly and the database
returns only permitted rows — a bug in the dashboard can't leak another owner's
books.

Stores genuinely belong to **different owners**. That's why there is no "all my
stores" view: aggregating across them would mix separate businesses into one
number.

---

## 2. Data flow

```
                    ┌─────────────────────────┐
                    │   Walmart Marketplace   │
                    │         API v3          │
                    └────────────┬────────────┘
                                 │  OAuth2 client-credentials
                                 │  (per store, secret decrypted at run time)
                                 ▼
        ┌───────────────────────────────────────────────┐
        │  syncer/  (Python jobs, cron or the UI button) │
        │  · sync.py             orders + catalog        │
        │  · sync_settlement.py  fees / recon report     │
        │  writes with the SERVICE key → bypasses RLS    │
        └────────────────────────┬──────────────────────┘
                                 │  upsert
                                 ▼
        ┌───────────────────────────────────────────────┐
        │  Supabase Postgres                            │
        │  · synced tables   orders, order_lines,       │
        │    products, settlement_lines                 │
        │  · entered tables  purchases, product_costs,  │
        │    damaged_goods, inventory_adjustments,      │
        │    manual_order_fees, fee_category_map        │
        │  · views  sku_landed_cost, order_details,     │
        │    profit_by_sku, monthly_profit,             │
        │    inventory_levels, returns                  │
        │  · RLS + user_has_store() / user_is_store_admin() │
        └────────────────────────┬──────────────────────┘
                                 │  reads with the ANON key
                                 │  (RLS filters to the user's stores)
                                 ▼
        ┌───────────────────────────────────────────────┐
        │  web/  FastAPI serves the SPA and the sync +  │
        │  admin endpoints. All *data reads* go from    │
        │  the browser straight to Supabase.            │
        └───────────────────────────────────────────────┘
```

Two Supabase keys, two trust levels:

- **service key** — used by `syncer/` and by the server's sync/admin endpoints.
  Bypasses RLS. **Never** sent to the browser.
- **anon key** — handed to the browser via `GET /config`. Safe to expose *because*
  RLS restricts what any authenticated user can read.

---

## 3. Database (`sql/`)

Almost all the logic lives here.

**`schema.sql` is the source of truth.** Run it alone on a fresh Supabase project
and you get every table, view, and policy. The `migration_*.sql` files are the
incremental path for a database built before a feature existed — apply them in
filename order to an existing database; skip them entirely for a new one.

> ⚠️ There is **no migration tracking table**. Nothing records which migrations
> have run, so the only way to know is to inspect the database. If you add
> another, consider fixing this first.

### Tables

**Synced from Walmart** (syncer writes, UI reads):
`orders`, `order_lines` (revenue split into `product_revenue` + `shipping_revenue`),
`products`, `settlement_lines` (raw recon rows).

**Entered by users** (written from the dashboard):
`purchases` (what you paid — drives landed cost), `product_costs` (a manual
fallback cost), `damaged_goods`, `inventory_adjustments`, `manual_order_fees`
(pending fees typed in before settlement lands), `fee_category_map`.

**Plumbing:** `stores`, `store_credentials` (encrypted secret; RLS-enabled with
**no policies**, so only the service key can reach it), `store_access` (the
user↔store↔role grants that drive RLS).

### Views

All are declared `with (security_invoker = on)` so they run with the *querying
user's* privileges and RLS still applies.

| View | Answers | Grain |
|---|---|---|
| `sku_landed_cost` | true unit cost incl. tax + shipping, weighted by qty | store × sku |
| `order_details` | the big one: per-line spreadsheet → `net_profit`, `margin_pct` | order line |
| `profit_by_sku` | net profit per product per month | store × sku × month |
| `monthly_profit` | net profit per month | store × month |
| `inventory_levels` | on-hand = purchased − sold + returned − damaged + adjustments | store × sku |
| `returns` | returns/refunds derived from order-line status | order line |

### RLS in two functions

```sql
user_has_store(store_id)       -- a store_access row links this store to auth.uid()
user_is_store_admin(store_id)  -- ...and that row's role is 'admin'
```

Read/insert policies use the first; update/delete use the second. Roles are
`admin` (can fix and delete entered rows, manage people and stores) or `member`
(read and add only). Grants are managed from the **Team** tab — SQL is only needed
to create the very first admin.

---

## 4. Syncer (`syncer/`)

Python jobs. Each loops over every `active` store — or just one, with
`--store-id` — decrypts that store's Walmart secret, and pulls.

| File | Role |
|---|---|
| `walmart_client.py` | Walmart API client — OAuth2 token (cached ~15 min), retry on 401/429/5xx, pagination. `/orders` (cursor), `/items` (offset), recon `availableReconFiles` + `reconFileJson`. |
| `secrets.py` | Fernet encrypt/decrypt using `WALMART_SECRET_KEY`. |
| `add_store.py` | One-time onboarding. Superseded by the **Stores** tab for most uses. |
| `sync.py` | Orders + catalog → `orders`, `order_lines`, `products`. Default `--days 2`, max 180. |
| `sync_settlement.py` | Recon report → `settlement_lines`. Default `--days 7`, max 90. Re-syncing a date replaces its rows. |

`--store-id` matters for more than convenience: stores have different owners, so
the web app always passes it. One owner must never trigger API calls billed to
another's Walmart account. Omit it only for a trusted cron.

---

## 5. Web (`web/`)

- **`app.py`** — FastAPI. Serves the SPA, exposes `GET /config` (public URL + anon
  key), and owns the few operations that *need* the service key and therefore
  cannot happen in the browser:
  - `POST /sync/orders`, `POST /sync/settlement` — run the syncer as a subprocess
  - `/admin/users*`, `/admin/stores*` — create logins, grant roles, add/delete stores

  Every admin route re-checks that the caller administers the store in question.
  Data *reads* still bypass the server entirely.

- **`static/`** — the SPA. Plain JS, no build step; `supabase-js` handles login and
  queries. Tabs: **Dashboard, Orders, Profit** (insights) · **Products,
  Inventory** (catalog) · **Purchases, Returns, Damaged Goods** (operations) ·
  **Team, Stores** (admin only). A module registry lazy-loads each tab and
  invalidates them by name.

---

## 6. How one sale becomes a profit number

1. `sync.py` pulls the order → `orders` + `order_lines` (`product_revenue`,
   `shipping_revenue`, `status`).
2. You log what you paid on the **Purchases** tab → `sku_landed_cost` gives a
   weighted-average unit cost including tax and shipping.
3. Fees arrive one of two ways:
   - **settlement** — `sync_settlement.py` writes `settlement_lines`, and
     `fee_category_map` sorts each `amount_type` into a bucket; or
   - **manual** — you type the pending referral/label fee onto the order, into
     `manual_order_fees`.

   Settlement wins when both exist. `order_details.fee_source` tells you which was
   used (`settlement`, `manual`, or `none`).
4. `order_details` computes the line:

   ```
   net_profit = (product_revenue + shipping_revenue)
              − (qty × unit_cost + qty × warehouse_cost)
              + fees                        ← already negative
   ```

   `unit_cost` follows a precedence: **landed cost → manual `product_costs` → none**.

5. `profit_by_sku` and `monthly_profit` roll those lines up.

### Two rules worth internalising

**Only shipped/delivered lines count.** `order_details.counts_in_profit` is true
only for those statuses. Created, Acknowledged and Cancelled orders show in the UI
but never reach a profit number — you haven't earned them yet.

**No known cost means no profit — not free profit.** A SKU with no purchase and no
manual cost would otherwise look 100% profitable. Both `profit_by_sku` and
`monthly_profit` contribute **0** for it instead, and the Dashboard warns you which
SKUs are affected.

**Sign convention:** settlement `amount` values are signed — Walmart reports fees
as negatives — which is why the view *adds* the fee buckets rather than
subtracting them.

---

## 7. Known gotchas

- ⚠️ **`fee_category_map` must be tuned to your account.** `schema.sql` creates it
  empty. After your first settlement sync:
  ```sql
  select distinct amount_type from settlement_lines;
  ```
  Map every value to `walmart_fee`, `wfs_label_fee`, `refund_fee`,
  `extra_service_fee`, or `ignore`. Unmapped types default to `extra_service_fee`,
  and any revenue-like row (Sale, Shipping) left unmapped will **double-count
  revenue**. This is the most likely cause of wrong numbers.

- **Fees are only as complete as your data.** Settlement reports don't exist until
  a payout cycle closes (new sellers face a ~21-day hold). Until then, profit is
  overstated on every order whose fees you haven't typed in. Check `fee_source` —
  `none` means that line's fees are missing, not zero.

- **Inventory assumes you logged every purchase.** A negative `on_hand` means you
  sold more than you recorded buying, which also means `sku_landed_cost` is
  averaging incomplete data.

- **The Period filter covers events, not state.** Orders, purchases, returns,
  damaged goods and the Dashboard filter by month. Products and Inventory don't —
  stock on hand is a running total, not something that happened in July. The
  Profit tab deliberately ignores the filter; it's the compare-months view.

- **No backups, no version history.** Worth fixing before it matters.
