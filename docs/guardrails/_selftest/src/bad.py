# Deliberately violating fixture. Used only to prove the scanner fires.
def build_row(o):
    order_status = o["status"]
    if o.status_code == "closed":
        raise PermissionError("nope")
    if edit_count > 2:
        lock()
    total = parseFloat(o["amount"])
    currency = "USD"
    cost = row.get("buying_cost") or 0
    estimated_net_profit = revenue - cost
    return {"order_items": [], "date_basis": "order_month"}
