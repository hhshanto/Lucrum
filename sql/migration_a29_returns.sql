-- ============================================================================
-- A29: returns, from the Returns API
-- ============================================================================
--
-- WHY
-- Walmart's order API never marks a return. Across 553 live lines the status is
-- only ever Delivered, Shipped, Acknowledged or Cancelled -- a refunded line
-- reads Delivered forever. A19 recorded that and fell back to detecting refunds
-- from settlement, which works for the money but tells you nothing about what
-- happened.
--
-- The Returns API does, and it is live on this account. Probed directly:
--   200015272104174  LOST_IN_TRANSIT            $133.67  COMPLETED  31 Aug
--   200014946144888  BOUGHT_ANOTHER_SIZE_COLOR   $24.99  COMPLETED  11 Aug
--   200015102201639  no return record
-- Worth noting the third: settlement has no refund for it either, so two
-- independent sources agree it was never returned through Walmart.
--
-- Each return line carries purchaseOrderId and purchaseOrderLineNumber, which
-- compose exactly the source_id the ledger, uncosted_lines and order_line_costs
-- already key on. No fuzzy matching.
--
-- DISPLAY ONLY -- THIS POSTS NO MONEY
-- settlement remains the sole source of refund amounts. The ledger already
-- derives return_revenue from it; letting returns post as well would give every
-- refund two origins and double-count it. What this adds is the reason, the
-- date and the lifecycle status.
--
-- That split also makes a reconciliation possible that neither source could do
-- alone: a return marked COMPLETED with no settlement refund is either lagging
-- or lost. AP-20154414536 is in exactly that state today.
--
-- Safe to re-run.

create table if not exists return_lines (
  store_id            uuid not null references stores(store_id) on delete cascade,
  return_order_id     text not null,
  return_line_number  integer not null,
  customer_order_id   text,
  purchase_order_id   text,
  order_line_number   text,
  -- "{purchase_order_id}:{order_line_number}", stored rather than derived at
  -- read time. Recovering it with split_part is what timed out the Profit tab
  -- in 6b35746; an expression join cannot use an index.
  source_id           text,
  sku                 text,
  return_reason       text,
  return_description  text,
  line_status         text,
  return_order_date   timestamptz,
  return_by_date      timestamptz,
  -- The API reports a refund total per return ORDER, not per line. Stored as it
  -- arrives and named so that is obvious: summing it across the lines of a
  -- multi-line return would multiply it.
  order_refund_total  numeric,
  refund_mode         text,
  raw                 jsonb,
  synced_at           timestamptz not null default now(),
  primary key (store_id, return_order_id, return_line_number)
);

create index if not exists return_lines_source on return_lines (store_id, source_id);
create index if not exists return_lines_order  on return_lines (store_id, customer_order_id);

comment on table return_lines is
  'One row per returned order line, from the Walmart Returns API. Carries the '
  'reason and lifecycle status only -- refund amounts come from settlement, so '
  'that a refund can never be counted twice.';

alter table return_lines enable row level security;

drop policy if exists "read own returns" on return_lines;
create policy "read own returns" on return_lines
  for select using (user_has_store(store_id));
-- No write policy: the syncer holds the service key. Same stance as A13.

-- ----------------------------------------------------------------------------
-- sync_state has a CHECK listing the feeds, so 'returns' has to be admitted
-- before the syncer can record a watermark for it. The constraint is unnamed in
-- A13, hence the lookup rather than a hardcoded name.
-- ----------------------------------------------------------------------------
do $$
declare c text;
begin
  select conname into c
  from pg_constraint
  where conrelid = 'sync_state'::regclass
    and contype = 'c'
    and pg_get_constraintdef(oid) like '%feed%';
  if c is not null then
    execute format('alter table sync_state drop constraint %I', c);
  end if;
end $$;

alter table sync_state
  add constraint sync_state_feed_check
  check (feed in ('orders', 'products', 'settlement', 'returns'));

-- ----------------------------------------------------------------------------
-- A returned line is not outstanding work. Dropping it from the queue.
-- ----------------------------------------------------------------------------
drop view if exists new_orders;

create view new_orders with (security_invoker = on) as
select
  l.store_id,
  l.purchase_order_id || ':' || l.line_number as source_id,
  l.purchase_order_id,
  l.line_number,
  o.customer_order_id,
  o.order_date,
  o.customer_name,
  o.customer_city,
  o.customer_state,
  l.sku,
  l.product_name,
  l.quantity,
  l.product_revenue,
  l.shipping_revenue,
  round(coalesce(l.product_revenue, 0) + coalesce(l.shipping_revenue, 0), 2) as total_revenue,
  l.status,
  floor(extract(epoch from (now() - o.order_date)) / 86400)::int as days_waiting,
  (select count(*) from order_lines l2
    where l2.store_id = l.store_id
      and l2.purchase_order_id = l.purchase_order_id) as lines_in_order
from order_lines l
join orders o
  on  o.store_id          = l.store_id
  and o.purchase_order_id = l.purchase_order_id
left join order_line_costs c
  on  c.store_id  = l.store_id
  and c.source_id = l.purchase_order_id || ':' || l.line_number
where c.source_id is null
  and lower(trim(coalesce(l.status, ''))) not in ('cancelled', 'canceled')
  -- Refunded, so there is no outstanding work to cost. The loss is real, but it
  -- is a closed event rather than a queue item.
  and not exists (
    select 1 from return_lines r
    where r.store_id  = l.store_id
      and r.source_id = l.purchase_order_id || ':' || l.line_number
  );

comment on view new_orders is
  'Order lines with no order_line_costs row, not cancelled and not returned: '
  'the queue of sales whose supplier cost has not been recorded yet. Entering '
  'a cost removes the line from this view.';

-- ----------------------------------------------------------------------------
-- The sheet gains the reason, so "Returned" can say WHY.
-- ----------------------------------------------------------------------------
drop view if exists monthly_sheet;

create view monthly_sheet with (security_invoker = on) as
select
  d.store_id,
  d.source_id,
  d.purchase_order_id,
  d.line_number,
  d.order_date,
  rl.image_url,
  d.sku,
  d.product_name,
  d.customer_order_id,
  d.customer_name,
  d.customer_city,
  d.customer_state,
  d.order_qty,
  d.unit_selling_price,
  d.extra_shipping,
  d.total_extra_shipping,
  round(coalesce(d.total_selling_price, 0)
        + coalesce(d.total_extra_shipping, 0), 2)   as total_sold,
  d.total_selling_price,
  d.unit_purchase_price,
  d.total_purchase_price,
  d.walmart_fees,
  d.label_walmart,
  d.label_third_party,
  d.warehouse_cost,
  d.net_profit,
  case when d.total_cost > 0
       then round(100 * d.net_profit / d.total_cost, 2)
       else null end                   as roi_pct,
  d.order_status,
  tk.tracking_number,
  tk.tracking_url,
  -- From the Returns API. Money still comes from settlement via d.returns; this
  -- is the explanation beside it.
  ret.return_reason,
  ret.return_description,
  ret.line_status                                   as return_status,
  ret.return_order_date,
  d.total_cost,
  d.margin_pct,
  d.refund_fees,
  d.extra_service_fees,
  d.returns,
  d.fee_source,
  d.is_uncertain,
  d.uncertain_reason,
  d.counts_in_profit
from ledger_order_details d
left join orders o
  on  o.store_id          = d.store_id
  and o.purchase_order_id = d.purchase_order_id
-- A line can in principle be returned more than once (partial, then the rest),
-- so take the most recent rather than letting the join multiply the row.
left join lateral (
  select r.return_reason, r.return_description, r.line_status, r.return_order_date
  from return_lines r
  where r.store_id = d.store_id and r.source_id = d.source_id
  order by r.return_order_date desc nulls last
  limit 1
) ret on true
left join lateral (
  select ol -> 'item' ->> 'imageUrl' as image_url
  from jsonb_array_elements(
         case when jsonb_typeof(o.raw -> 'orderLines' -> 'orderLine') = 'array'
              then o.raw -> 'orderLines' -> 'orderLine'
              else '[]'::jsonb end) as ol
  where ol ->> 'lineNumber' = d.line_number
  limit 1
) rl on true
left join lateral (
  select
    st -> 'trackingInfo' ->> 'trackingNumber' as tracking_number,
    st -> 'trackingInfo' ->> 'trackingURL'    as tracking_url
  from jsonb_array_elements(
         case when jsonb_typeof(o.raw -> 'orderLines' -> 'orderLine') = 'array'
              then o.raw -> 'orderLines' -> 'orderLine'
              else '[]'::jsonb end) as ol2
  cross join lateral jsonb_array_elements(
         case when jsonb_typeof(ol2 -> 'orderLineStatuses' -> 'orderLineStatus') = 'array'
              then ol2 -> 'orderLineStatuses' -> 'orderLineStatus'
              else '[]'::jsonb end) as st
  where ol2 ->> 'lineNumber' = d.line_number
    and coalesce(st -> 'trackingInfo', 'null'::jsonb) <> 'null'::jsonb
  limit 1
) tk on true;

comment on view monthly_sheet is
  'One row per order line in the column order of Swiftzar Llc.xlsx, plus ROI, '
  'the Walmart/3rd-party label split, the item image, carrier tracking, the '
  'destination and the return reason where one exists.';

-- ----------------------------------------------------------------------------
-- Returns Walmart says are finished but settlement has not paid out. Neither
-- source can see this alone, which is the point of keeping them separate.
-- ----------------------------------------------------------------------------
drop view if exists returns_awaiting_settlement;

create view returns_awaiting_settlement with (security_invoker = on) as
select
  r.store_id,
  r.return_order_id,
  r.customer_order_id,
  r.source_id,
  r.sku,
  r.return_reason,
  r.return_description,
  r.line_status,
  r.return_order_date,
  r.order_refund_total,
  floor(extract(epoch from (now() - r.return_order_date)) / 86400)::int as days_since_return
from return_lines r
where not exists (
  select 1
  from settlement_lines s
  where s.store_id          = r.store_id
    and s.customer_order_id = r.customer_order_id
    and s.transaction_type ilike 'refund%'
);

comment on view returns_awaiting_settlement is
  'Returns recorded by Walmart with no matching refund in settlement. Either '
  'the payout is lagging or it has been missed; the money is still counted as '
  'earned until it lands.';

insert into schema_migrations (filename, note)
values ('migration_a29_returns.sql', 'returns from the Returns API, display only')
on conflict (filename) do nothing;
