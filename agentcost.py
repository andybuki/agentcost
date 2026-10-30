#!/usr/bin/env python3
"""agentcost — per-client cost report for Claude Code sessions.

Reads Claude Code session logs (~/.claude/projects/**/*.jsonl), attributes every
assistant turn to a client via rules in a TOML config, prices tokens, and prints
a monthly report. Only metadata is read (usage, model, cwd, branch, time) —
prompt text and file contents are never touched. No LLM calls.

Usage:
  python agentcost.py --config clients.toml [--logs DIR] [--month 2026-10] [--csv out.csv]
"""
import argparse, csv, json, re, sys, tomllib
from collections import defaultdict
from pathlib import Path

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
    return "(unassigned)"

def price_for(prices, model):
    # Longest matching prefix, so "claude-sonnet" covers dated variants.
    best = None
    for prefix in prices:
        if model.startswith(prefix) and (best is None or len(prefix) > len(best)):
            best = prefix
    return prices.get(best) if best else None

def iter_turns(log_dir):
    seen = set()
    for f in sorted(Path(log_dir).expanduser().rglob("*.jsonl")):
        with open(f, encoding="utf-8") as fh:
            for line in fh:
                try:
                    e = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if e.get("type") != "assistant":
                    continue
                msg = e.get("message") or {}
                usage = msg.get("usage")
                if not usage:
                    continue
                # Streaming can write the same message on several lines.
                key = (msg.get("id"), e.get("requestId"))
                if key[0] and key in seen:
                    continue
                seen.add(key)
                yield {
                    "ts": e.get("timestamp", ""),
                    "session": e.get("sessionId", ""),
                    "cwd": e.get("cwd", ""),
                    "branch": e.get("gitBranch", ""),
                    "model": msg.get("model", "unknown"),
                    "in": usage.get("input_tokens", 0) or 0,
                    "out": usage.get("output_tokens", 0) or 0,
                    "cw": usage.get("cache_creation_input_tokens", 0) or 0,
                    "cr": usage.get("cache_read_input_tokens", 0) or 0,
                }

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True)
    ap.add_argument("--logs", default="~/.claude/projects")
    ap.add_argument("--month", help="YYYY-MM filter")
    ap.add_argument("--csv", help="write per-client rows to CSV")
    a = ap.parse_args()

    cfg, rules = load_config(a.config)
    prices = cfg["prices"]  # USD per million tokens
    markups = {r["name"]: r["markup"] for r in rules}
    agg = defaultdict(lambda: {"sessions": set(), "in": 0, "out": 0, "cw": 0, "cr": 0, "cost": 0.0})
    unpriced = set()

    for t in iter_turns(a.logs):
        if a.month and not t["ts"].startswith(a.month):
            continue
        client = attribute(rules, t["cwd"], t["branch"])
        p = price_for(prices, t["model"])
        if p is None:
            unpriced.add(t["model"]); continue
        cost = (t["in"] * p["input"] + t["out"] * p["output"]
                + t["cw"] * p["cache_write"] + t["cr"] * p["cache_read"]) / 1e6
        g = agg[client]
        g["sessions"].add(t["session"])
        for k in ("in", "out", "cw", "cr"):
            g[k] += t[k]
        g["cost"] += cost

    cur = cfg.get("currency_rate", 1.0)
    sym = cfg.get("currency_symbol", "USD")
    rows = []
    for client, g in sorted(agg.items(), key=lambda kv: -kv[1]["cost"]):
        cost = g["cost"] * cur
        bill = cost * markups.get(client, 0 if client == "(unassigned)" else 1.0)
        rows.append([client, len(g["sessions"]), g["in"], g["out"], g["cw"], g["cr"],
                     round(cost, 2), round(bill, 2)])

    hdr = ["client", "sessions", "input", "output", "cache_write", "cache_read",
           f"cost_{sym}", f"billable_{sym}"]
    w = [max(len(str(x)) for x in col) for col in zip(hdr, *rows)] if rows else [len(h) for h in hdr]
    print(f"Agent cost report {a.month or '(all time)'}")
    print("  ".join(h.ljust(n) for h, n in zip(hdr, w)))
    for r in rows:
        print("  ".join(str(x).ljust(n) for x, n in zip(r, w)))
    total = sum(r[6] for r in rows)
    print(f"\nTotal cost: {total:.2f} {sym}")
    if unpriced:
        print(f"WARNING: no price for models: {', '.join(sorted(unpriced))}", file=sys.stderr)

    if a.csv:
        with open(a.csv, "w", newline="") as f:
            csv.writer(f).writerows([hdr] + rows)

if __name__ == "__main__":
    main()
