"""Monthly billing terms per client: fixed fee, minimum fee, cap.

Usage-based billing (cost × markup) is computed per turn and stays unchanged in
the web view. Terms apply to a whole calendar month, so they are settled only
in the monthly report: `settle()` turns the month's usage value into the amount
to invoice and says why it differs.
"""
TERM_KEYS = ("fixed_fee", "min_fee", "cap")

REASONS = {
    "fixed_fee": "Fixed monthly fee",
    "min_fee": "Minimum monthly fee",
    "cap": "Monthly cap",
}

def settle(usage, terms):
    """Returns {"usage", "amount", "adjustment", "rule"}; rule is None if unchanged.

    fixed_fee replaces usage-based billing; otherwise min_fee raises and cap
    lowers the month's usage value (all amounts in the display currency).
    """
    terms = terms or {}
    amount, rule = usage, None
    if terms.get("fixed_fee") is not None:
        amount, rule = terms["fixed_fee"], "fixed_fee"
    else:
        if terms.get("min_fee") is not None and amount < terms["min_fee"]:
            amount, rule = terms["min_fee"], "min_fee"
        if terms.get("cap") is not None and amount > terms["cap"]:
            amount, rule = terms["cap"], "cap"
    if abs(amount - usage) < 0.005:
        rule = None
    return {"usage": usage, "amount": amount, "adjustment": amount - usage, "rule": rule}

def has_retainer(terms):
    """True if the client is billed even in a month without usage."""
    terms = terms or {}
    return bool(terms.get("fixed_fee") or terms.get("min_fee"))
