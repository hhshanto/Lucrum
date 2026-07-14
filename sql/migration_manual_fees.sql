-- Migration: manually-entered referral / label fees until Walmart settles.
--
-- WHY: a new seller has a 21-day payment hold, so every transaction sits
-- "Pending" and no recon file exists -- settlement_lines stays empty and the app
-- reports $0 fees, overstating profit. But Walmart's pending transaction page
-- already shows the REAL referral fee and label fee per order. This lets you type
-- those in so the numbers are right today.
--
-- PRECEDENCE per (store, customer order, sku):
--   1. settlement_lines  -- authoritative, from the API
--   2. manual_order_fees -- what you copied from Walmart's pending view
--   3. nothing -> 0
-- The moment real settlement arrives for an order, it wins automatically and the
-- manual row is ignored (kept, not deleted, so nothing is lost).
--
-- Fees are stored here as POSITIVE amounts (what Walmart took, as displayed);
-- the view negates them to match the settlement sign convention.
--
-- order_details gains a `fee_source` column: 'settlement' | 'manual' | 'none'.
--
-- Run in the Supabase SQL Editor AFTER migration_shipping_fix.sql.

begin;

create table if not exists manual_order_fees (
  store_id          uuid references stores(store_id) on delete cascade,
  customer_order_id text not null,
  sku               text not null,
  referral_fee      numeric not null default 0,
  label_fee         numeric not null default 0,
  updated_at        timestamptz not null default now(),
  primary key (store_id, customer_order_id, sku)
);

alter table manual_order_fees enable row level security;

drop policy if exists "read own manual fees"     on manual_order_fees;
drop policy if exists "add own manual fees"      on manual_order_fees;
drop policy if exists "edit own manual fees"     on manual_order_fees;
drop policy if exists "admin delete manual fees" on manual_order_fees;
create policy "read own manual fees" on manual_order_fees for select using (user_has_store(store_id));
create policy "add own manual fees"  on manual_order_fees for insert with check (user_has_store(store_id));
create policy "edit own manual fees" on manual_order_fees for update
  using (user_has_store(store_id)) with check (user_has_store(store_id));
create policy "admin delete manual fees" on manual_order_fees for delete
  using (user_is_store_admin(store_id));

create or replace view order_details with (security_invoker = on) as
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
    -- Real settlement wins; otherwise fall back to what you typed in (negated
    -- to match settlement's sign convention, where fees arrive negative).
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
       else 'none' end as fee_source
from alloc;

commit;
