-- Managing who can use Lucrum.
--
-- STEP 1 -- create the account (Supabase Dashboard):
--   Authentication -> Users -> "Add user"
--     * enter their email + a temporary password
--     * tick "Auto Confirm User" so they can sign in straight away
--   Do NOT re-enable public signups just to onboard someone -- leave signups off
--   and add people here, or anyone who finds the URL can register.
--
-- STEP 2 -- grant access by running ONE block below in the SQL Editor,
--   editing the email first. Creating the account alone gives them NOTHING:
--   with no store_access row, RLS returns zero rows and they see an empty app.
--
-- ROLES
--   admin  -- read + add, AND fix/remove hand-entered rows (purchases,
--            product costs, damaged goods, inventory adjustments, fee map)
--   member -- read + add only. Good for a VA who logs purchases but must not
--            be able to delete them (deleting a purchase silently changes your
--            landed cost -> COGS -> profit).


-- ============ Grant ADMIN on every store ============
insert into store_access (store_id, user_id, role)
select s.store_id, u.id, 'admin'
from stores s
cross join auth.users u
where u.email = 'teammate@example.com'
on conflict (store_id, user_id) do update set role = excluded.role;


-- ============ Grant MEMBER on every store ============
insert into store_access (store_id, user_id, role)
select s.store_id, u.id, 'member'
from stores s
cross join auth.users u
where u.email = 'teammate@example.com'
on conflict (store_id, user_id) do update set role = excluded.role;


-- ============ Grant on ONE store only ============
insert into store_access (store_id, user_id, role)
select s.store_id, u.id, 'admin'
from stores s
cross join auth.users u
where u.email = 'teammate@example.com'
  and s.name = 'Swiftzar'
on conflict (store_id, user_id) do update set role = excluded.role;


-- ============ Change someone's role ============
update store_access
set role = 'member'
where user_id = (select id from auth.users where email = 'teammate@example.com');


-- ============ Revoke access (keeps the login, removes all data access) ============
delete from store_access
where user_id = (select id from auth.users where email = 'teammate@example.com');


-- ============ Audit: who can see what? ============
select u.email, s.name as store, sa.role
from store_access sa
join auth.users u on u.id = sa.user_id
join stores s      on s.store_id = sa.store_id
order by u.email, s.name;
