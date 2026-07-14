# Lucrum

A dashboard for Walmart Marketplace sellers. It pulls your orders from Walmart,
matches them against what you paid for the goods, and shows what you actually
made — per product, per month, after fees.

Stores can belong to different owners, so who sees what matters. A user only ever
sees the stores they've been granted, and Postgres enforces that itself through
Row Level Security — not the app. A bug in the dashboard can't leak someone
else's books.

For how it fits together — the tables, the views, the profit math, the known
gotchas — see [ARCHITECTURE.md](ARCHITECTURE.md).

## What you need

- A [Supabase](https://supabase.com) project (free tier is fine)
- Python 3.11+
- Walmart Marketplace API credentials for each store

## Setup

1. Create the Supabase project. From **Project Settings → API**, copy the project
   URL, the `anon` key, and the `service_role` key.

2. Paste `sql/schema.sql` into the Supabase SQL Editor and run it. That builds
   every table, view, and access policy.

3. Generate a key to encrypt Walmart secrets with:

   ```
   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
   ```

4. Copy `.env.example` to `.env` and fill in the four values.

5. `pip install -r requirements.txt`

## Running it

```
uvicorn web.app:app --reload
```

Open http://localhost:8000.

You need an account before you can log in. Create the first one in Supabase
(**Authentication → Users**), then make it an admin by adding a row to
`store_access (store_id, user_id, role)` with role `admin`. That's the only time
you should need SQL — after that, the **Team** and **Stores** tabs handle people
and stores.

Orders sync from the **Refresh orders** button. The same job runs from the command
line, which is what a cron would do:

```
cd syncer
python sync.py --days 30                      # every active store
python sync.py --days 30 --store-id <uuid>    # just one
```

`sync_settlement.py` takes the same arguments and pulls Walmart's fee report —
though that report only exists once a payout cycle closes. Until then you can type
pending fees straight onto an order.

## Environment

| Variable | Used by | Safe in the browser? |
|---|---|---|
| `SUPABASE_URL` | web + syncer | yes |
| `SUPABASE_ANON_KEY` | web | yes — RLS protects the data |
| `SUPABASE_SERVICE_KEY` | web + syncer | **never** — it bypasses RLS |
| `WALMART_SECRET_KEY` | syncer | **never** — it decrypts store secrets |

## Layout

```
sql/          schema.sql is the source of truth; migration_*.sql apply in order
syncer/       talks to Walmart, writes to Supabase
web/app.py    serves the app, plus the sync and admin endpoints
web/static/   the dashboard — plain JS, no build step
```

## License

Proprietary — © 2026 Mohammad Hasan. All rights reserved. See [LICENSE](LICENSE).
