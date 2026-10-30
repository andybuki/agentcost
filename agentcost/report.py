"""agentcost — per-client cost report for Claude Code sessions.

Reads Claude Code session logs (~/.claude/projects/**/*.jsonl), attributes every
assistant turn to a client via rules in a TOML config, prices tokens, and prints
a monthly report. Only metadata is read (usage, model, cwd, branch, time) —
prompt text and file contents are never touched. No LLM calls.

Usage:
  python -m agentcost --config clients.toml [--logs DIR] [--month 2026-10] [--csv out.csv]
"""
import argparse, csv, sys
from collections import defaultdict

from .parse import iter_turns
from .rules import UNASSIGNED, attribute, load_config, price_for, turn_cost

def aggregate(turns, cfg, rules, month=None):
    """Return (rows, header, unpriced_models); rows sorted by cost, descending."""
    prices = cfg["prices"]  # USD per million tokens
    markups = {r["name"]: r["markup"] for r in rules}
    agg = defaultdict(lambda: {"sessions": set(), "in": 0, "out": 0, "cw": 0, "cr": 0, "cost": 0.0})
    unpriced = set()

    for t in turns:
        if month and not t["ts"].startswith(month):
            continue
        client = attribute(rules, t["cwd"], t["branch"])
        p = price_for(prices, t["model"])
        if p is None:
            unpriced.add(t["model"]); continue
        g = agg[client]
        g["sessions"].add(t["session"])
        for k in ("in", "out", "cw", "cr"):
            g[k] += t[k]
        g["cost"] += turn_cost(t, p)

    cur = cfg.get("currency_rate", 1.0)
    sym = cfg.get("currency_symbol", "USD")
    rows = []
    for client, g in sorted(agg.items(), key=lambda kv: -kv[1]["cost"]):
        cost = g["cost"] * cur
        bill = cost * markups.get(client, 0 if client == UNASSIGNED else 1.0)
        rows.append([client, len(g["sessions"]), g["in"], g["out"], g["cw"], g["cr"],
                     round(cost, 2), round(bill, 2)])

    hdr = ["client", "sessions", "input", "output", "cache_write", "cache_read",
           f"cost_{sym}", f"billable_{sym}"]
    return rows, hdr, unpriced

def print_table(rows, hdr, title):
    w = [max(len(str(x)) for x in col) for col in zip(hdr, *rows)] if rows else [len(h) for h in hdr]
    print(f"Agent cost report {title}")
    print("  ".join(h.ljust(n) for h, n in zip(hdr, w)))
    for r in rows:
        print("  ".join(str(x).ljust(n) for x, n in zip(r, w)))
    total = sum(r[6] for r in rows)
    print(f"\nTotal cost: {total:.2f} {hdr[6].removeprefix('cost_')}")

def main(argv=None):
    ap = argparse.ArgumentParser(prog="agentcost")
    ap.add_argument("--config", required=True)
    ap.add_argument("--logs", default="~/.claude/projects")
    ap.add_argument("--month", help="YYYY-MM filter")
    ap.add_argument("--csv", help="write per-client rows to CSV")
    a = ap.parse_args(argv)

    cfg, rules = load_config(a.config)
    rows, hdr, unpriced = aggregate(iter_turns(a.logs), cfg, rules, a.month)
    print_table(rows, hdr, a.month or "(all time)")
    if unpriced:
        print(f"WARNING: no price for models: {', '.join(sorted(unpriced))}", file=sys.stderr)

    if a.csv:
        with open(a.csv, "w", newline="") as f:
            csv.writer(f).writerows([hdr] + rows)
