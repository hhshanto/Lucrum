-- Walmart Multi-Store Analytics — Phase 1 schema
-- Paste this into the Supabase SQL Editor and run it.

-- ============ TABLES ============
create table stores (
  store_id  uuid primary key default gen_random_uuid(),
  name      text not null,
  active    boolean not null default true,
  added_at  timestamptz not null default now()
);

create table store_credentials (
  store_id                uuid primary key references stores(store_id) on delete cascade,
  client_id               text not null,
  encrypted_client_secret text not null
);

create table store_access (
  store_id uuid references stores(store_id) on delete cascade,
  user_id  uuid references auth.users(id)   on delete cascade,
  -- 'admin' can fix/remove hand-entered rows; 'member' can only read + add.
  role     text not null default 'member' check (role in ('admin', 'member')),
  primary key (store_id, user_id)
);

create table orders (
  store_id          uuid references stores(store_id) on delete cascade,
  purchase_order_id text,
  customer_order_id text,
  order_date        timestamptz,
  customer_name     text,
  customer_city     text,
  customer_state    text,
  raw               jsonb,
  synced_at         timestamptz default now(),
  primary key (store_id, purchase_order_id)
);

create table order_lines (
  store_id          uuid references stores(store_id) on delete cascade,
  purchase_order_id text,
  line_number       text,
  sku               text,
  product_name      text,
  quantity          numeric,
  product_revenue   numeric,
  shipping_revenue  numeric not null default 0,
  status            text,
  primary key (store_id, purchase_order_id, line_number)
);

create table product_costs (
  store_id       uuid references stores(store_id) on delete cascade,
  sku            text,
  unit_cost      numeric not null,
  warehouse_cost numeric not null default 0,
  primary key (store_id, sku)
);

create table products (
  store_id         uuid references stores(store_id) on delete cascade,
  sku              text,
  product_name     text,
  price            numeric,
  published_status text,
  lifecycle_status text,
  synced_at        timestamptz default now(),
  primary key (store_id, sku)
);

create table purchases (
  id            uuid primary key default gen_random_uuid(),
  store_id      uuid references stores(store_id) on delete cascade,
  sku           text not null,
  product_name  text,
  quantity      numeric not null,
  unit_cost     numeric not null,
  sales_tax     numeric not null default 0,
  shipping      numeric not null default 0,
  source        text,
  product_link  text,
  order_number  text,
  status        text,
  purchase_date date not null default current_date,
  notes         text,
  created_at    timestamptz not null default now()
);

create table damaged_goods (
  id           uuid primary key default gen_random_uuid(),
  store_id     uuid references stores(store_id) on delete cascade,
  sku          text not null,
  quantity     numeric not null,
  reason       text,
  damaged_date date not null default current_date,
  notes        text,
  created_at   timestamptz not null default now()
);

create table inventory_adjustments (
  id             uuid primary key default gen_random_uuid(),
  store_id       uuid references stores(store_id) on delete cascade,
  sku            text not null,
  quantity_delta numeric not null,
  reason         text,
  adjusted_date  date not null default current_date,
  notes          text,
  created_at     timestamptz not null default now()
);

-- Raw rows pulled from Walmart's recon (settlement) report. One row per
-- transaction line (sale, referral fee, WFS fee, refund, etc).
create table settlement_lines (
  id                     uuid primary key default gen_random_uuid(),
  store_id               uuid references stores(store_id) on delete cascade,
  report_date            date not null,
  customer_order_id      text,
  order_line_number      text,
  sku                    text,
  transaction_type       text,
  amount_type            text,
  amount                 numeric,
  quantity               numeric,
  transaction_posted_at  timestamptz,
  synced_at              timestamptz default now()
);

create index settlement_lines_lookup on settlement_lines (store_id, customer_order_id, sku);

-- Maps Walmart's recon "Amount Type" strings to the 4 fee columns shown in
-- Order Details, per store. Populate after the first settlement sync, once you
-- can see the real Amount Type values for your account.
create table fee_category_map (
  store_id    uuid references stores(store_id) on delete cascade,
  amount_type text,
  category    text not null check (category in ('walmart_fee', 'wfs_label_fee', 'refund_fee', 'extra_service_fee', 'ignore')),
  primary key (store_id, amount_type)
);

-- Referral / label fees typed in from Walmart's PENDING transaction page, used
-- until the recon report settles and supersedes them. Stored as positive amounts
-- (what Walmart took); order_details negates them to match settlement's signs.
create table manual_order_fees (
  store_id          uuid references stores(store_id) on delete cascade,
  customer_order_id text not null,
  sku               text not null,
  referral_fee      numeric not null default 0,
  label_fee         numeric not null default 0,
  updated_at        timestamptz not null default now(),
  primary key (store_id, customer_order_id, sku)
);

-- ============ HELPER ============
-- Is the logged-in user authorized for this store?
create or replace function user_has_store(s uuid)
returns boolean language sql security definer stable
set search_path = public as $$
  select exists (
    select 1 from store_access
    where store_access.store_id = s and store_access.user_id = auth.uid()
  );
$$;

-- Is the logged-in user an admin of this store? (may fix/remove entered rows)
create or replace function user_is_store_admin(s uuid)
returns boolean language sql security definer stable
set search_path = public as $$
  select exists (
    select 1 from store_access
    where store_access.store_id = s
      and store_access.user_id = auth.uid()
      and store_access.role = 'admin'
  );
$$;

-- ============ TURN ON ROW LEVEL SECURITY ============
alter table stores            enable row level security;
alter table store_credentials enable row level security;  -- no policies => locked to service key
alter table store_access      enable row level security;
alter table orders            enable row level security;
alter table order_lines       enable row level security;
alter table product_costs     enable row level security;
alter table products          enable row level security;
alter table purchases             enable row level security;
alter table damaged_goods         enable row level security;
alter table inventory_adjustments enable row level security;
alter table settlement_lines      enable row level security;
alter table fee_category_map      enable row level security;
alter table manual_order_fees     enable row level security;

-- ============ POLICIES (what authenticated users may do) ============
create policy "read own stores"  on stores       for select using (user_has_store(store_id));
create policy "read own access"  on store_access for select using (user_id = auth.uid());
create policy "read own orders"  on orders       for select using (user_has_store(store_id));
create policy "read own lines"   on order_lines  for select using (user_has_store(store_id));

create policy "read own costs"   on product_costs for select using (user_has_store(store_id));
create policy "add own costs"    on product_costs for insert with check (user_has_store(store_id));
create policy "edit own costs"   on product_costs for update
  using (user_has_store(store_id)) with check (user_has_store(store_id));

create policy "read own products" on products for select using (user_has_store(store_id));

create policy "read own purchases" on purchases for select using (user_has_store(store_id));
create policy "add own purchases"  on purchases for insert with check (user_has_store(store_id));

create policy "read own damaged" on damaged_goods for select using (user_has_store(store_id));
create policy "add own damaged"  on damaged_goods for insert with check (user_has_store(store_id));

create policy "read own adjustments" on inventory_adjustments for select using (user_has_store(store_id));
create policy "add own adjustments"  on inventory_adjustments for insert with check (user_has_store(store_id));

create policy "read own settlement lines" on settlement_lines for select using (user_has_store(store_id));

-- Store-scoped like every other table; owners can read and manage their own map.
create policy "read own fee map" on fee_category_map for select using (user_has_store(store_id));
create policy "add own fee map"  on fee_category_map for insert with check (user_has_store(store_id));
create policy "edit own fee map" on fee_category_map for update
  using (user_has_store(store_id)) with check (user_has_store(store_id));

create policy "read own manual fees" on manual_order_fees for select using (user_has_store(store_id));
create policy "add own manual fees"  on manual_order_fees for insert with check (user_has_store(store_id));
create policy "edit own manual fees" on manual_order_fees for update
  using (user_has_store(store_id)) with check (user_has_store(store_id));

-- ============ ADMIN FIX-UPS ============
-- Admins can correct or remove the rows you enter by hand. Synced tables
-- (orders, order_lines, products, settlement_lines) stay read-only on purpose:
-- a manual edit there would be overwritten by the next sync.
create policy "admin edit purchases" on purchases for update
  using (user_is_store_admin(store_id)) with check (user_is_store_admin(store_id));
create policy "admin delete purchases" on purchases for delete
  using (user_is_store_admin(store_id));

create policy "admin edit damaged" on damaged_goods for update
  using (user_is_store_admin(store_id)) with check (user_is_store_admin(store_id));
create policy "admin delete damaged" on damaged_goods for delete
  using (user_is_store_admin(store_id));

create policy "admin edit adjustments" on inventory_adjustments for update
  using (user_is_store_admin(store_id)) with check (user_is_store_admin(store_id));
create policy "admin delete adjustments" on inventory_adjustments for delete
  using (user_is_store_admin(store_id));

create policy "admin delete costs" on product_costs for delete
  using (user_is_store_admin(store_id));

create policy "admin delete fee map" on fee_category_map for delete
  using (user_is_store_admin(store_id));

create policy "admin delete manual fees" on manual_order_fees for delete
  using (user_is_store_admin(store_id));

-- ============ LANDED COST VIEW (RLS-aware) ============
-- Weighted-average all-in cost per SKU from what you actually bought:
-- (qty * unit_cost + sales_tax + shipping) / qty across all its purchases.
create view sku_landed_cost with (security_invoker = on) as
select
  store_id,
  sku,
  round(
    sum(quantity * unit_cost + coalesce(sales_tax, 0) + coalesce(shipping, 0))
    / nullif(sum(quantity), 0)
  , 4) as landed_unit_cost
from purchases
group by store_id, sku;

-- NOTE: profit_by_sku is defined further down, after order_details -- it now
-- aggregates that view so the Dashboard reports the same NET profit as the
-- Orders and Profit tabs (fees and warehouse included), rather than a gross
-- revenue-minus-COGS number that ignored every deduction.

-- ============ INVENTORY VIEW (RLS-aware) ============
-- on_hand = purchased - sold + returned (customer returns) - damaged + manual adjustments
create view inventory_levels with (security_invoker = on) as
with purchased as (
  select store_id, sku, sum(quantity) as qty
  from purchases
  group by store_id, sku
),
sold as (
  select store_id, sku, sum(quantity) as qty
  from order_lines
  where status is null
     or (status not ilike '%return%' and status not ilike '%refund%' and status not ilike '%cancel%')
  group by store_id, sku
),
returned as (
  select store_id, sku, sum(quantity) as qty
  from order_lines
  where status ilike '%return%' or status ilike '%refund%'
  group by store_id, sku
),
damaged as (
  select store_id, sku, sum(quantity) as qty
  from damaged_goods
  group by store_id, sku
),
adjusted as (
  select store_id, sku, sum(quantity_delta) as qty
  from inventory_adjustments
  group by store_id, sku
),
combined as (
  select store_id, sku from purchased
  union
  select store_id, sku from sold
  union
  select store_id, sku from returned
  union
  select store_id, sku from damaged
  union
  select store_id, sku from adjusted
)
select
  c.store_id,
  c.sku,
  max(p.product_name)        as product_name,
  coalesce(pu.qty, 0)         as purchased,
  coalesce(so.qty, 0)         as sold,
  coalesce(re.qty, 0)         as returned,
  coalesce(da.qty, 0)         as damaged,
  coalesce(aj.qty, 0)         as adjusted,
  coalesce(pu.qty, 0) - coalesce(so.qty, 0) + coalesce(re.qty, 0)
    - coalesce(da.qty, 0) + coalesce(aj.qty, 0) as on_hand
from combined c
left join purchased pu on pu.store_id = c.store_id and pu.sku = c.sku
left join sold       so on so.store_id = c.store_id and so.sku = c.sku
left join returned   re on re.store_id = c.store_id and re.sku = c.sku
left join damaged    da on da.store_id = c.store_id and da.sku = c.sku
left join adjusted   aj on aj.store_id = c.store_id and aj.sku = c.sku
left join products   p  on p.store_id = c.store_id and p.sku = c.sku
group by c.store_id, c.sku, pu.qty, so.qty, re.qty, da.qty, aj.qty;

-- ============ RETURNS VIEW (RLS-aware) ============
-- Customer returns/refunds, derived from order line status.
create view returns with (security_invoker = on) as
select
  ol.store_id,
  o.purchase_order_id,
  o.order_date,
  ol.sku,
  ol.product_name,
  ol.quantity,
  ol.product_revenue,
  ol.status
from order_lines ol
join orders o
  on o.store_id = ol.store_id and o.purchase_order_id = ol.purchase_order_id
where ol.status ilike '%return%' or ol.status ilike '%refund%';

-- ============ ORDER DETAILS VIEW (RLS-aware) ============
-- Spreadsheet-style, per order line. Walmart Fees / WFS-Label Fees / Refund Fees /
-- Extra Walmart Service Fees come from settlement_lines, bucketed via the
-- per-store fee_category_map. Any amount_type not yet mapped falls back to
-- "extra_service_fee" so it's never silently dropped from net_profit -- but that
-- also means revenue-like settlement rows (e.g. "Sale", "Shipping") MUST be
-- mapped to 'ignore' or they'll double-count revenue already in order_lines.
-- Check `select distinct amount_type from settlement_lines` once real data lands
-- and update fee_category_map accordingly.
--
-- Fees are aggregated per (customer order, SKU) then ALLOCATED across the order
-- lines that share that key, proportional to quantity, so a SKU appearing on
-- more than one line of an order is never charged the fee twice.
create view order_details with (security_invoker = on) as
with settlement_agg as (
  select
    sl.store_id,
    sl.customer_order_id,
    sl.sku,
    coalesce(fcm.category, 'extra_service_fee') as category,
    sum(sl.amount) as amount
  from settlement_lines sl
  left join fee_category_map fcm
    on fcm.store_id = sl.store_id
   and fcm.amount_type = sl.amount_type
  where coalesce(fcm.category, 'extra_service_fee') <> 'ignore'
  group by sl.store_id, sl.customer_order_id, sl.sku, coalesce(fcm.category, 'extra_service_fee')
),
settlement_pivot as (
  select
    store_id,
    customer_order_id,
    sku,
    sum(amount) filter (where category = 'walmart_fee')      as walmart_fees,
    sum(amount) filter (where category = 'wfs_label_fee')     as wfs_label_fees,
    sum(amount) filter (where category = 'refund_fee')        as refund_fees,
    sum(amount) filter (where category = 'extra_service_fee') as extra_service_fees
  from settlement_agg
  group by store_id, customer_order_id, sku
),
lines as (
  select
    ol.store_id, ol.sku, ol.product_name, ol.quantity,
    ol.product_revenue, ol.shipping_revenue, ol.status,
    o.order_date, o.customer_name, o.customer_city, o.customer_state, o.customer_order_id,
    coalesce(lc.landed_unit_cost, pc.unit_cost) as unit_cost,
    pc.warehouse_cost,
    sp.walmart_fees, sp.wfs_label_fees, sp.refund_fees, sp.extra_service_fees,
    (sp.store_id is not null) as has_settlement,
    mf.referral_fee as manual_referral,
    mf.label_fee    as manual_label,
    (mf.store_id is not null) as has_manual,
    case
      when sum(coalesce(ol.quantity, 0)) over w > 0
        then coalesce(ol.quantity, 0) / sum(coalesce(ol.quantity, 0)) over w
      else 1.0 / count(*) over w
    end as fee_share
  from order_lines ol
  join orders o
    on o.store_id = ol.store_id and o.purchase_order_id = ol.purchase_order_id
  left join product_costs pc
    on pc.store_id = ol.store_id and pc.sku = ol.sku
  left join sku_landed_cost lc
    on lc.store_id = ol.store_id and lc.sku = ol.sku
  left join settlement_pivot sp
    on sp.store_id = ol.store_id
   and sp.customer_order_id = o.customer_order_id
   and sp.sku = ol.sku
  left join manual_order_fees mf
    on mf.store_id = ol.store_id
   and mf.customer_order_id = o.customer_order_id
   and mf.sku = ol.sku
  window w as (partition by ol.store_id, o.customer_order_id, ol.sku)
),
alloc as (
  select
    l.*,
    round((case when l.has_settlement then coalesce(l.walmart_fees, 0)
                else -coalesce(l.manual_referral, 0) end) * l.fee_share, 2) as eff_walmart,
    round((case when l.has_settlement then coalesce(l.wfs_label_fees, 0)
                else -coalesce(l.manual_label, 0) end) * l.fee_share, 2) as eff_label,
    round(coalesce(l.refund_fees, 0) * l.fee_share, 2)        as eff_refund,
    round(coalesce(l.extra_service_fees, 0) * l.fee_share, 2) as eff_extra
  from lines l
)
select
  store_id,
  order_date,
  sku,
  product_name,
  customer_name,
  customer_city,
  customer_state,
  customer_order_id,
  quantity as order_qty,
  case when quantity > 0 then round(product_revenue / quantity, 2) else 0 end as unit_selling_price,
  case when quantity > 0 then round(shipping_revenue / quantity, 2) else 0 end as extra_shipping,
  coalesce(unit_cost, 0) as unit_purchase_price,
  round(product_revenue, 2) as total_selling_price,
  round(shipping_revenue, 2) as total_extra_shipping,
  round(quantity * coalesce(unit_cost, 0), 2) as total_purchase_price,
  eff_walmart as walmart_fees,
  eff_label   as wfs_label_fees,
  coalesce(warehouse_cost, 0) as warehouse_cost,
  round(
    quantity * coalesce(unit_cost, 0)
    + quantity * coalesce(warehouse_cost, 0)
    - eff_walmart - eff_label - eff_refund - eff_extra
  , 2) as total_cost,
  round(
    product_revenue + shipping_revenue
    - (quantity * coalesce(unit_cost, 0) + quantity * coalesce(warehouse_cost, 0))
    + eff_walmart + eff_label + eff_refund + eff_extra
  , 2) as net_profit,
  case when (product_revenue + shipping_revenue) > 0 then round(
    100 * (
      product_revenue + shipping_revenue
      - (quantity * coalesce(unit_cost, 0) + quantity * coalesce(warehouse_cost, 0))
      + eff_walmart + eff_label + eff_refund + eff_extra
    ) / (product_revenue + shipping_revenue)
  , 2) else 0 end as margin_pct,
  status as order_status,
  eff_refund as refund_fees,
  eff_extra  as extra_service_fees,
  case when has_settlement then 'settlement'
       when has_manual     then 'manual'
       else 'none' end as fee_source,
  -- Realized only: the same set Walmart actually pays you for.
  (lower(trim(coalesce(status, ''))) in ('shipped', 'delivered')) as counts_in_profit
from alloc;

-- ============ PROFIT BY SKU VIEW (RLS-aware) ============
-- Rolls order_details up per SKU, so the Dashboard shows the same NET profit as
-- Orders/Profit: revenue (product + shipping) - COGS - warehouse - Walmart fees.
-- Defined after order_details because it reads from it.
create view profit_by_sku with (security_invoker = on) as
select
  od.store_id,
  od.sku,
  max(od.product_name) as product_name,
  sum(od.order_qty)    as units,
  round(sum(od.total_selling_price + od.total_extra_shipping), 2) as revenue,
  round(sum(od.total_purchase_price), 2) as cogs,
  round(sum(coalesce(od.walmart_fees, 0) + coalesce(od.wfs_label_fees, 0)
          + coalesce(od.refund_fees, 0) + coalesce(od.extra_service_fees, 0)), 2) as fees,
  case
    when coalesce(max(lc.landed_unit_cost), max(pc.unit_cost)) is null then 0::numeric
    else round(sum(od.net_profit), 2)
  end as net_profit,
  -- unit_cost / cost_source are current values, identical across months.
  round(coalesce(max(lc.landed_unit_cost), max(pc.unit_cost)), 4) as unit_cost,
  case when max(lc.landed_unit_cost) is not null then 'landed'
       when max(pc.unit_cost) is not null then 'manual'
       else null end as cost_source,
  date_trunc('month', od.order_date)::date as month
from order_details od
left join sku_landed_cost lc
  on lc.store_id = od.store_id and lc.sku = od.sku
left join product_costs pc
  on pc.store_id = od.store_id and pc.sku = od.sku
where od.counts_in_profit
  and od.order_date is not null
group by od.store_id, od.sku, date_trunc('month', od.order_date);

-- ============ MONTHLY PROFIT VIEW (RLS-aware) ============
-- Rolls order_details up to one row per store per calendar month for the
-- Profit tab's P&L. Net profit already includes fees per line, so this is a
-- straight sum.
create view monthly_profit with (security_invoker = on) as
with priced as (
  select
    od.*,
    coalesce(lc.landed_unit_cost, pc.unit_cost) is not null as has_cost
  from order_details od
  left join sku_landed_cost lc
    on lc.store_id = od.store_id and lc.sku = od.sku
  left join product_costs pc
    on pc.store_id = od.store_id and pc.sku = od.sku
  where od.order_date is not null
    and od.counts_in_profit
)
select
  store_id,
  date_trunc('month', order_date)::date as month,
  count(distinct customer_order_id) as orders,
  sum(order_qty) as units,
  round(sum(total_selling_price + total_extra_shipping), 2) as revenue,
  round(sum(total_purchase_price), 2) as cogs,
  round(sum(coalesce(walmart_fees, 0) + coalesce(wfs_label_fees, 0)
          + coalesce(refund_fees, 0) + coalesce(extra_service_fees, 0)), 2) as fees,
  round(sum(case when has_cost then net_profit else 0 end), 2) as net_profit,
  case when sum(total_selling_price + total_extra_shipping) > 0
       then round(100 * sum(case when has_cost then net_profit else 0 end)
                  / sum(total_selling_price + total_extra_shipping), 2)
       else 0 end as margin_pct
from priced
group by store_id, date_trunc('month', order_date);
