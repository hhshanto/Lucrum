-- Migration: store-scope the fee map + fix order_details fee allocation.
--
-- Two DB fixes, safe to run once on the existing database:
--
--   1. fee_category_map becomes per-store: primary key (store_id, amount_type)
--      instead of a single global amount_type. Consistent with every other
--      table, and lets different stores map Walmart's fee strings differently.
--
--   2. order_details is rebuilt as the settlement-aware (net) view -- your live
--      copy is still the older gross version, missing the fee columns -- and it
--      now ALLOCATES each (customer order, SKU) fee bucket across the order
--      lines that share it, proportional to quantity. This prevents the old
--      double-count where the same SKU on two lines of one order got charged
--      the full fee twice.
--
-- Run in the Supabase SQL Editor (after schema.sql / migration_order_details.sql).
-- With no settlement data yet, the displayed numbers do not change -- fees are 0,
-- so net_profit / total_cost stay exactly as they are today.

begin;

-- ============ 1. Store-scope fee_category_map ============
alter table fee_category_map
  add column if not exists store_id uuid references stores(store_id) on delete cascade;

-- Safe because the table is empty on first run. If you already have rows,
-- backfill store_id (or delete them) before running this line.
alter table fee_category_map alter column store_id set not null;

alter table fee_category_map drop constraint if exists fee_category_map_pkey;
do $$
begin
  if not exists (
    select 1 from pg_constraint
    where conrelid = 'fee_category_map'::regclass and contype = 'p'
  ) then
    alter table fee_category_map add primary key (store_id, amount_type);
  end if;
end $$;

-- Reads are now store-scoped like every other table; owners can manage their map.
drop policy if exists "read fee category map" on fee_category_map;
drop policy if exists "read own fee map"      on fee_category_map;
drop policy if exists "add own fee map"       on fee_category_map;
drop policy if exists "edit own fee map"      on fee_category_map;
create policy "read own fee map" on fee_category_map for select using (user_has_store(store_id));
create policy "add own fee map"  on fee_category_map for insert with check (user_has_store(store_id));
create policy "edit own fee map" on fee_category_map for update
  using (user_has_store(store_id)) with check (user_has_store(store_id));

-- Best-guess seed for every existing store. IMPORTANT: after your first
-- settlement sync, run `select distinct amount_type from settlement_lines;`
-- and map every real value to one of walmart_fee / wfs_label_fee / refund_fee /
-- extra_service_fee / ignore (anything unmapped defaults to extra_service_fee).
insert into fee_category_map (store_id, amount_type, category)
select s.store_id, v.amount_type, v.category
from stores s
cross join (values
  ('Sale', 'ignore'),
  ('Shipping', 'ignore'),
  ('Tax', 'ignore'),
  ('Service Fee', 'walmart_fee')
) as v(amount_type, category)
on conflict (store_id, amount_type) do nothing;

-- ============ 2. Rebuild order_details (net + fee allocation) ============
-- Dropped, not replaced, because the column layout changes.
drop view if exists order_details;
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
-- One row per order line, carrying its share of the (order, SKU) fee bucket:
-- proportional to quantity, with an equal split fallback when the group's total
-- quantity is 0. Guarantees fees are counted once across lines that share a SKU.
lines as (
  select
    ol.store_id, ol.sku, ol.product_name, ol.quantity,
    ol.product_revenue, ol.shipping_revenue, ol.status,
    o.order_date, o.customer_name, o.customer_city, o.customer_state, o.customer_order_id,
    pc.unit_cost, pc.warehouse_cost,
    sp.walmart_fees, sp.wfs_label_fees, sp.refund_fees, sp.extra_service_fees,
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
  left join settlement_pivot sp
    on sp.store_id = ol.store_id
   and sp.customer_order_id = o.customer_order_id
   and sp.sku = ol.sku
  window w as (partition by ol.store_id, o.customer_order_id, ol.sku)
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
  round(coalesce(walmart_fees, 0) * fee_share, 2) as walmart_fees,
  round(coalesce(wfs_label_fees, 0) * fee_share, 2) as wfs_label_fees,
  coalesce(warehouse_cost, 0) as warehouse_cost,
  round(
    quantity * coalesce(unit_cost, 0)
    + shipping_revenue
    + quantity * coalesce(warehouse_cost, 0)
    - coalesce(walmart_fees, 0) * fee_share
    - coalesce(wfs_label_fees, 0) * fee_share
    - coalesce(refund_fees, 0) * fee_share
    - coalesce(extra_service_fees, 0) * fee_share
  , 2) as total_cost,
  round(
    product_revenue
    - (quantity * coalesce(unit_cost, 0) + shipping_revenue + quantity * coalesce(warehouse_cost, 0))
    + coalesce(walmart_fees, 0) * fee_share
    + coalesce(wfs_label_fees, 0) * fee_share
    + coalesce(refund_fees, 0) * fee_share
    + coalesce(extra_service_fees, 0) * fee_share
  , 2) as net_profit,
  case when product_revenue > 0 then round(
    100 * (
      product_revenue
      - (quantity * coalesce(unit_cost, 0) + shipping_revenue + quantity * coalesce(warehouse_cost, 0))
      + coalesce(walmart_fees, 0) * fee_share
      + coalesce(wfs_label_fees, 0) * fee_share
      + coalesce(refund_fees, 0) * fee_share
      + coalesce(extra_service_fees, 0) * fee_share
    ) / product_revenue
  , 2) else 0 end as margin_pct,
  status as order_status,
  round(coalesce(refund_fees, 0) * fee_share, 2) as refund_fees,
  round(coalesce(extra_service_fees, 0) * fee_share, 2) as extra_service_fees
from lines;

commit;
