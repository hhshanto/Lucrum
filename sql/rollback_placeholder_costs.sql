-- Rollback: remove the 52 PLACEHOLDER unit costs inserted on 2026-10-21.
--
-- THESE COSTS ARE FABRICATED. Each was computed as 73.23% of that SKU's average
-- unit selling price. The 73.23% came from the eight SKUs that genuinely had
-- costs -- an unrepresentative 23% of revenue, weighted toward higher-value
-- SWIFTZAR items. It is scaffolding so the system can be exercised end to end.
-- It is NOT a basis for any financial decision.
--
-- Everything else currently in the database is real and verified:
--   revenue  $11,378.70   matches settlement on 146 of 152 spot-checked orders
--   fees     -$1,669.16   matches Walmart's settlement total exactly
--   COGS      $1,751.11   from 8 SKUs with genuine purchase history
--   COGS      $2,114.49   FABRICATED, from the 52 SKUs below
--
-- YOU DO NOT NEED TO RUN THIS BEFORE ENTERING REAL COSTS.
-- product_costs is the FALLBACK in the cost precedence chain (schema.sql:397):
--     sku_landed_cost (from purchases)  ->  product_costs  ->  none
-- As soon as a SKU has real purchases logged, landed cost takes precedence and
-- the placeholder is ignored automatically. Running this deletes them outright,
-- returning those SKUs to an honest "no cost known" state.
--
-- The warehouse_cost = 0 guard below is deliberate: every genuine product_costs
-- row in this database has a non-zero warehouse_cost, so the guard prevents this
-- from ever deleting real data even if a SKU name is reused later.

delete from product_costs
where warehouse_cost = 0
  and sku in (
  ('AP-17768920448'),
  ('AP-20154414536'),
  ('Ap-00612461403322'),
  ('Ap-17699066821'),
  ('Ap-18254251314'),
  ('Ap-19637015708'),
  ('Ap-20254364812'),
  ('Ap-20370658309'),
  ('Ap-20569862487'),
  ('Ap-20611304375'),
  ('Ap-20789621052'),
  ('Ap-20836754193'),
  ('SWIFTZAR110'),
  ('SWIFTZAR117'),
  ('SWIFTZAR118'),
  ('SWIFTZAR119'),
  ('SWIFTZAR127'),
  ('SWIFTZAR136'),
  ('SWIFTZAR137'),
  ('SWIFTZAR141'),
  ('SWIFTZAR142'),
  ('SWIFTZAR151'),
  ('SWIFTZAR152'),
  ('SWIFTZAR158'),
  ('SWIFTZAR160'),
  ('SWIFTZAR182'),
  ('SWIFTZAR234'),
  ('SWIFTZAR292'),
  ('SWT-634707742484'),
  ('SWT-634707798764'),
  ('SWT-634707833564'),
  ('SWT-634707876004'),
  ('SWT-634707895852'),
  ('Sty-15624861392'),
  ('Sty-15646602723'),
  ('Sty-17331473824'),
  ('Sty-18056801993'),
  ('Sty-1987311721'),
  ('Sty-20666424737'),
  ('Sty-20824761202'),
  ('Sty-21054666326'),
  ('Sty-7512572904'),
  ('WA-634707765599'),
  ('WA-634707972508'),
  ('ZAR-634707674488'),
  ('ZAR-634707720741'),
  ('ZAR-634707734861'),
  ('ZAR-634707764974'),
  ('ZAR-634707894299'),
  ('ZAR-634707943034'),
  ('ZAR-634707981616'),
  ('swt-634707836411')
  );
