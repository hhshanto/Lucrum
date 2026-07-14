# Lucrum — Architecture & Orientation

The accurate, current map of the project. The top-level `README.md` still
describes the first three build phases only; this document reflects what the
code actually does today (roughly six phases in).

---

## 1. What Lucrum is

A small multi-tenant analytics dashboard for Walmart Marketplace sellers.

> **Walmart Marketplace API → a Python syncer → Supabase Postgres → a browser dashboard**,
> where each logged-in user sees only the stores they're authorized for.

The defining design choice: **access control lives in the database, not the
app.** Postgres Row Level Security (RLS) scopes every query to the stores a
user is granted in `store_access`. The web app holds no authorization logic —
it just runs queries and the database returns only permitted rows.

---

## 2. Data flow

```
                    ┌─────────────────────────┐
                    │   Walmart Marketplace    │
                    │          API v3          │
                    └────────────┬────────────┘
                                 │  OAuth2 client-credentials
                                 │  (per store, secret decrypted at run time)
                                 ▼
        ┌───────────────────────────────────────────────┐
        │  syncer/  (Python jobs, run on a schedule)     │
        │  · sync.py             orders + catalog        │
        │  · sync_settlement.py  fees / recon report     │
        │  · add_store.py        one-time onboarding     │
        │  writes with the SERVICE key → bypasses RLS    │
        └────────────────────────┬──────────────────────┘
                                 │  upsert
                                 ▼
        ┌───────────────────────────────────────────────┐
        │  Supabase Postgres                             │
        │  · raw tables (orders, order_lines, products,  │
        │    settlement_lines)                           │
        │  · manual tables (product_costs, purchases,    │
        │    damaged_goods, inventory_adjustments,       │
        │    fee_category_map)                           │
        │  · analytics VIEWS (profit_by_sku,             │
        │    inventory_levels, returns, order_details)   │
        │  · RLS policies + user_has_store()             │
        └────────────────────────┬──────────────────────┘
                                 │  reads with the ANON key
                                 │  (RLS filters to the user's stores)
                                 ▼
        ┌───────────────────────────────────────────────┐
        │  web/  FastAPI serves a static SPA;            │
        │  the browser talks to Supabase directly via    │
        │  supabase-js (auth + queries).                 │
        │  Tabs: Overview · Purchases · Damaged Goods ·  │
        │        Inventory · Returns · Order Details     │
        └───────────────────────────────────────────────┘
```

Two Supabase keys, two trust levels:
- **service key** — used only by `syncer/`. Bypasses RLS, can write anything. **Never** sent to the browser.
- **anon key** — handed to the browser via `GET /config`. Safe to expose *because* RLS restricts what any authenticated user can read.

---

## 3. Layer 1 — Database (`sql/`)

The database is the heart of the project; almost all logic lives here.

### `schema.sql` is the source of truth
`sql/schema.sql` is the **complete, current schema** — run it alone on a fresh
Supabase project and you get every table, view, and policy. The three
`migration_*.sql` files are the **incremental upgrade path** for a database
that was built before these features existed; they are historical and you do
**not** need them for a new setup (one exception noted in §7).

| Migration | Added |
|---|---|
| `migration_order_details.sql` | customer name/city/state, `shipping_revenue`, `warehouse_cost`, first `order_details` view (gross only) |
| `migration_settlement.sql` | `settlement_lines` + `fee_category_map` tables |
| `migration_settlement_fees.sql` | seeds `fee_category_map`, rewrites `order_details` to fold in real fees |

### Tables — two kinds

**Synced from Walmart** (written by the syncer, read-only in the UI):
- `orders`, `order_lines` — one row per order / per line item; revenue split into `product_revenue` and `shipping_revenue`
- `products` — the seller's catalog
- `settlement_lines` — raw rows from the recon (settlement) report: sales, referral fees, WFS fees, refunds, etc.

**Entered by users** (written from the dashboard):
- `product_costs` — `unit_cost` + `warehouse_cost` per SKU (needed to compute profit)
- `purchases`, `damaged_goods`, `inventory_adjustments` — feed the inventory math
- `fee_category_map` — maps Walmart's free-text `amount_type` strings to the four fee buckets

**Plumbing:** `stores`, `store_credentials` (encrypted secret; RLS-enabled with
**no policies**, so it's reachable only by the service key), `store_access`
(the user↔store grants that drive RLS).

### Views — where the analytics happen
All views are declared `with (security_invoker = on)`, meaning they run with
the *querying user's* privileges, so RLS on the underlying tables still applies.

| View | Answers |
|---|---|
| `profit_by_sku` | revenue − COGS per SKU (gross profit) |
| `inventory_levels` | on-hand = purchased − sold + returned − damaged + adjustments |
| `returns` | customer returns/refunds derived from order-line status |
| `order_details` | the big one: per-line spreadsheet with fees folded in → `net_profit`, `margin_pct` |

### The RLS model in one function
```sql
user_has_store(store_id)  -- true if a store_access row links this store to auth.uid()
```
Every policy is essentially `using (user_has_store(store_id))`. Grant a user
access by inserting into `store_access (store_id, user_id)`; revoke by deleting
it. There are no roles yet — access is binary.

---

## 4. Layer 2 — Syncer (`syncer/`)

Python jobs meant to run on a schedule (e.g. daily). Each loops over every
`active` store, decrypts that store's Walmart secret, and pulls data.

| File | Role |
|---|---|
| `walmart_client.py` | Walmart API client — OAuth2 token (cached ~15 min), retry on 401/429/5xx, pagination. Endpoints: `/orders` (cursor), `/items` (offset), recon `availableReconFiles` + `reconFileJson`. |
| `secrets.py` | Fernet encrypt/decrypt using `WALMART_SECRET_KEY`. |
| `add_store.py` | One-time: insert a `stores` row + its encrypted credentials. |
| `sync.py` | Orders + catalog → `orders`, `order_lines`, `products`. Default `--days 2`, max 180. |
| `sync_settlement.py` | Recon report → `settlement_lines`. Default `--days 7`, max 90. Re-syncing a date replaces its rows. |

All four require `SUPABASE_URL`, `SUPABASE_SERVICE_KEY`, and `WALMART_SECRET_KEY`.

---

## 5. Layer 3 — Web (`web/`)

- `app.py` — a deliberately tiny FastAPI server. It serves `index.html`, mounts
  `/static`, and exposes `GET /config` (the public Supabase URL + anon key). It
  does **no** data access itself.
- `static/` — the SPA. `supabase-js` handles login and runs all queries straight
  against Supabase; RLS does the gatekeeping. Six tabs:
  Overview (profit by SKU, catalog, cost entry), Purchases, Damaged Goods,
  Inventory, Returns, Order Details.

---

## 6. End-to-end: how one sale becomes a profit number

1. **Walmart** has an order for SKU `ABC`.
2. `sync.py` pulls it → writes an `orders` row and an `order_lines` row (`product_revenue`, `shipping_revenue`, `status`).
3. `sync_settlement.py` pulls the recon report → writes `settlement_lines` (the referral/WFS/refund fee amounts for that order).
4. A user enters `unit_cost` (and optionally `warehouse_cost`) for SKU `ABC` in the **Overview** tab → `product_costs`.
5. `fee_category_map` tells the `order_details` view which fee lines are `walmart_fee` vs `wfs_label_fee` vs `refund_fee` vs `extra_service_fee` (and which to `ignore`).
6. The **Order Details** view computes `net_profit` = revenue − (cost + shipping + warehouse) + fees, and `margin_pct`.
7. The browser queries `order_details`; RLS returns only rows for the user's stores; the tab renders them.

Sign convention worth knowing: settlement `amount` values are treated as
**signed** — Walmart reports fees as negative numbers — which is why the view
*adds* the fee buckets into net profit rather than subtracting them. Verify this
against your real data (see §7).

---

## 7. Current state & known gotchas

- ⚠️ **`fee_category_map` must be tuned to your account.** `schema.sql` creates
  it **empty**; `migration_settlement_fees.sql` seeds four best-guess rows
  (`Sale`/`Shipping`/`Tax` → `ignore`, `Service Fee` → `walmart_fee`). If you set
  up fresh from `schema.sql`, it stays empty until you seed it. Either way, after
  the first real settlement sync run:
  ```sql
  select distinct amount_type from settlement_lines;
  ```
  and map every value to one of `walmart_fee`, `wfs_label_fee`, `refund_fee`,
  `extra_service_fee`, or `ignore`. Unmapped types default to
  `extra_service_fee` — and any revenue-like row (Sale, Shipping) left unmapped
  will **double-count revenue**. This is the most likely cause of wrong numbers.
- **Net profit is implemented** (contrary to the README's "What's deferred"). What
  remains genuinely deferred: role tiers (access is binary), and deployment
  (no Railway/Procfile/cron yet).
- **Unverified from the repo:** whether `.env` is wired to a live Supabase +
  Walmart account — i.e. whether it runs end-to-end for you today. There's no
  `.env.example` in the tree despite the README referencing one.

---

## 8. Setup & run (condensed)

```bash
# 1. Create a Supabase project; copy Project URL, anon key, service_role key.
# 2. Run sql/schema.sql in the Supabase SQL Editor.
# 3. Generate a Fernet key:
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
# 4. Create .env with SUPABASE_URL, SUPABASE_ANON_KEY, SUPABASE_SERVICE_KEY, WALMART_SECRET_KEY
# 5. Install deps:
pip install -r requirements.txt

# Onboard a store:
cd syncer && python add_store.py --name "Store A" --client-id ID --client-secret SECRET
# Sync data:
python sync.py --days 30
python sync_settlement.py --days 30
# Run the dashboard (from repo root):
uvicorn web.app:app --reload   # → http://localhost:8000
```

Grant a user access by inserting a row into `store_access (store_id, user_id)`
after creating them in Supabase Auth.
