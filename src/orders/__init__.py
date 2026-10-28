"""Orders module. US-005a (AC-01, AC-02) and US-005b-1 (AC-03, backend
half) -- Import and refresh orders, plus a durable record of each import
run.

Persists the imported order header: store identity, source order identifier,
SKU, order date, quantity, selling amount, currency, and source fulfillment
state (AC-01), upserted by ``(store_id, walmart_order_id)`` so a refresh
retains the existing row's identity rather than duplicating it (AC-02).
Every import call also opens, classifies and closes an ``import_batches``
row and records any rejected order in ``import_errors`` (AC-03), and
exposes the queryable "never imported / last success / last attempt"
state AC-03 requires (``repository.get_import_run_state``).

Deliberately does NOT implement: posting-period assignment, deferral,
re-posting, or any of US-005's AC-04 through AC-08 (US-005c); the
rendered screen that surfaces import-run state to a user (US-005b-2); any
cost table (US-008+). See ``service.py``'s module docstring for exactly
what this module does and does not decide.
"""
