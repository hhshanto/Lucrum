# Walmart Multi-Store Analytics

A small team dashboard: each user logs in and sees only the Walmart stores
they're authorized for, with orders and gross profit synced automatically.
Access control is enforced in Postgres via Row Level Security (RLS), not in
the app — see `sql/schema.sql`.

> **New here / need the full picture?** This README covers the original three
> phases only. For the accurate, current map of the whole project — all tables,
> views, the syncer, the dashboard, and the known gotchas — read
> [ARCHITECTURE.md](ARCHITECTURE.md).

## Project layout

```
sql/schema.sql      Phase 1 — tables, RLS policies, profit_by_sku view
syncer/              Phase 2 — pulls Walmart orders into Supabase
  secrets.py         encrypt/decrypt Walmart client secrets (Fernet)
  walmart_client.py  Walmart Marketplace API client (OAuth2 + pagination)
  add_store.py       CLI to register a new store + its credentials
  sync.py            scheduled job: syncs orders for all active stores
web/                 Phase 3 — login + profit dashboard
  app.py             FastAPI server (serves the SPA + /config)
  static/            HTML/CSS/JS (supabase-js handles auth + data)
```

## Setup

1. **Supabase project** — create one at supabase.com. From
   Project Settings → API, copy the Project URL, `anon` key, and
   `service_role` key.
2. **Run the schema** — paste `sql/schema.sql` into the Supabase SQL Editor
   and run it.
3. **Generate the Walmart secret key**:
   ```
   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
   ```
4. Copy `.env.example` to `.env` and fill in the four values.
5. Install dependencies:
   ```
   pip install -r requirements.txt
   ```

## Running locally

Register a store (requires `SUPABASE_URL`, `SUPABASE_SERVICE_KEY`,
`WALMART_SECRET_KEY`):

```
cd syncer
python add_store.py --name "Store A" --client-id YOUR_ID --client-secret YOUR_SECRET
```

Run a sync (pulls the last N days of orders for every active store):

```
cd syncer
python sync.py --days 30
```

Run the web app (requires `SUPABASE_URL`, `SUPABASE_ANON_KEY`):

```
uvicorn web.app:app --reload
```

Then open http://localhost:8000. Create a user in Supabase Auth, grant them
access by inserting a row into `store_access (store_id, user_id)`, log in,
and confirm the dashboard shows only that user's store(s).

## Environment variables

| Variable | Used by | Exposed to browser? |
|---|---|---|
| `SUPABASE_URL` | both | yes (safe) |
| `SUPABASE_ANON_KEY` | web app | yes (safe — RLS protects data) |
| `SUPABASE_SERVICE_KEY` | syncer only | **never** |
| `WALMART_SECRET_KEY` | syncer only | **never** |

## What's deferred

- **Net profit** — ✅ now implemented. `sync_settlement.py` pulls the recon
  (settlement) report into `settlement_lines`, and the `order_details` view
  folds Walmart/WFS/refund/service fees into `net_profit` and `margin_pct`
  (see [ARCHITECTURE.md](ARCHITECTURE.md) §6–7). `profit_by_sku` remains a
  gross (revenue − COGS) view. Requires tuning `fee_category_map` to your
  account — see the gotcha in ARCHITECTURE.md §7.
- **Role tiers** — access is currently binary (in `store_access` or not).
- **Deployment** — Railway config (Procfile + cron job for `sync.py`) is not
  set up yet.

## License

Proprietary. Copyright (c) 2026 Mohammad Hasan. All rights reserved.
No permission to use, copy, modify, or distribute is granted — see
[LICENSE](LICENSE).
