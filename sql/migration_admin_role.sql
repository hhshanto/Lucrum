-- Migration: admin role + CRUD policies for the data you enter by hand.
--
-- Until now RLS only allowed SELECT + INSERT on purchases / damaged_goods /
-- inventory_adjustments, so a wrong row could never be fixed or removed from the
-- app -- which matters now that purchases drive landed cost -> COGS -> profit.
--
-- Adds:
--   * store_access.role -- 'admin' or 'member' (default member)
--   * user_is_store_admin(store_id) helper
--   * UPDATE/DELETE policies for admins on the hand-entered tables
--
-- Deliberately NOT editable: orders, order_lines, products, settlement_lines.
-- Those are synced from Walmart -- a manual edit would be silently overwritten by
-- the next sync and would break reconciliation against Walmart's own numbers.
--
-- Run in the Supabase SQL Editor.

begin;

-- ============ 1. Role on store_access ============
alter table store_access
  add column if not exists role text not null default 'member';

alter table store_access drop constraint if exists store_access_role_check;
alter table store_access add constraint store_access_role_check
  check (role in ('admin', 'member'));

-- Bootstrap: every grant that exists today predates roles and belongs to an
-- owner, so make them admins. New grants default to 'member'.
update store_access set role = 'admin' where role = 'member';

-- ============ 2. Helper ============
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

-- ============ 3. Admin UPDATE / DELETE policies ============
-- purchases
drop policy if exists "admin edit purchases"   on purchases;
drop policy if exists "admin delete purchases" on purchases;
create policy "admin edit purchases" on purchases for update
  using (user_is_store_admin(store_id)) with check (user_is_store_admin(store_id));
create policy "admin delete purchases" on purchases for delete
  using (user_is_store_admin(store_id));

-- damaged_goods
drop policy if exists "admin edit damaged"   on damaged_goods;
drop policy if exists "admin delete damaged" on damaged_goods;
create policy "admin edit damaged" on damaged_goods for update
  using (user_is_store_admin(store_id)) with check (user_is_store_admin(store_id));
create policy "admin delete damaged" on damaged_goods for delete
  using (user_is_store_admin(store_id));

-- inventory_adjustments
drop policy if exists "admin edit adjustments"   on inventory_adjustments;
drop policy if exists "admin delete adjustments" on inventory_adjustments;
create policy "admin edit adjustments" on inventory_adjustments for update
  using (user_is_store_admin(store_id)) with check (user_is_store_admin(store_id));
create policy "admin delete adjustments" on inventory_adjustments for delete
  using (user_is_store_admin(store_id));

-- product_costs / fee_category_map already allow member INSERT+UPDATE; add delete.
drop policy if exists "admin delete costs" on product_costs;
create policy "admin delete costs" on product_costs for delete
  using (user_is_store_admin(store_id));

drop policy if exists "admin delete fee map" on fee_category_map;
create policy "admin delete fee map" on fee_category_map for delete
  using (user_is_store_admin(store_id));

commit;
