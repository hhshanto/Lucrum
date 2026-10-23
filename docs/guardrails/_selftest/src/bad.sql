-- This file is a guardrail fixture. It must trip SC-15 and SC-01.
create table bad_orders (
  id uuid primary key,
  order_status text,
  amount double precision,
  other real
);
