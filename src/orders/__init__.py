"""Orders module. US-005a -- Import and refresh orders (AC-01, AC-02 only).

Persists the imported order header: store identity, source order identifier,
SKU, order date, quantity, selling amount, currency, and source fulfillment
state (AC-01), upserted by ``(store_id, walmart_order_id)`` so a refresh
retains the existing row's identity rather than duplicating it (AC-02).

Deliberately does NOT implement: posting-period assignment, deferral,
re-posting, or any of US-005's AC-04 through AC-08 (US-005c); import-run
visibility or staleness (AC-03, US-005b); any cost table (US-008+). See
``service.py``'s module docstring for exactly what this module does and does
not decide.
"""
