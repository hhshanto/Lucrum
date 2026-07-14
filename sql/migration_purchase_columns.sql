-- Migration: extend purchases to match the seller's buying spreadsheet.
--
-- Adds product link, order number, sales tax, shipping, and status. Total Cost
-- and Landed Unit Cost are COMPUTED in the app (qty*unit_cost + sales_tax +
-- shipping, and that divided by qty), not stored, so there's one source of truth.
--
-- Run in the Supabase SQL Editor. Safe/idempotent; existing rows default tax and
-- shipping to 0.

alter table purchases
  add column if not exists product_link text,
  add column if not exists order_number text,
  add column if not exists sales_tax    numeric not null default 0,
  add column if not exists shipping     numeric not null default 0,
  add column if not exists status       text;
