"""Client attribution and pricing rules loaded from clients.toml."""
import re, tomllib

UNASSIGNED = "(unassigned)"

def load_config(path):
    with open(path, "rb") as f:
        cfg = tomllib.load(f)
    rules = []
    for c in cfg.get("client", []):
        rules.append({
            "name": c["name"],
            "cwd": [re.compile(p) for p in c.get("cwd", [])],
            "branch": [re.compile(p) for p in c.get("branch", [])],
            "markup": float(c.get("markup", cfg.get("default_markup", 1.0))),
        })
    return cfg, rules

def attribute(rules, cwd, branch):
    # First matching rule wins; branch rules are checked before cwd rules
    # so a shared monorepo can still be split by branch naming (e.g. acme/*).
    for r in rules:
        if branch and any(p.search(branch) for p in r["branch"]):
            return r["name"]
    for r in rules:
        if cwd and any(p.search(cwd) for p in r["cwd"]):
            return r["name"]
    return UNASSIGNED

def price_for(prices, model):
    # Longest matching prefix, so "claude-sonnet" covers dated variants.
    best = None
    for prefix in prices:
        if model.startswith(prefix) and (best is None or len(prefix) > len(best)):
            best = prefix
    return prices.get(best) if best else None

def turn_cost(turn, price):
    """Cost of one turn in the price list's currency (prices are per million tokens)."""
    return (turn["in"] * price["input"] + turn["out"] * price["output"]
            + turn["cw"] * price["cache_write"] + turn["cr"] * price["cache_read"]) / 1e6
