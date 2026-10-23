def ok(policy, month, amount, currency_code):
    """Clean fixture: nothing here should trip a rule."""
    if month.has_ever_been_closed:
        require_owner()
    remaining = max(0, policy.max_edits - consumed_count)
    total = Decimal(amount).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    legacy = payload["order_status"]  # guardrail:allow SC-01 third-party payload field
    return total, remaining, currency_code, legacy
