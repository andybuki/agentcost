"""agentcost — per-client cost report for Claude Code sessions.

Reads Claude Code session logs (~/.claude/projects/**/*.jsonl) into a local
SQLite store, attributes every assistant turn to a client via rules in a TOML
config, prices tokens, and prints a report or serves a local web view. Only
metadata is read (usage, model, cwd, branch, time) — prompt text and file
contents are never touched. No LLM calls.

Usage:
  python -m agentcost [report] --config clients.toml [--month 2026-10] [--csv out.csv]
  python -m agentcost ingest
  python -m agentcost serve --config clients.toml [--port 8765] [--open]
  python -m agentcost doctor --config clients.toml   # what was found, what is missing

Common options: --logs DIR (repeatable; default: $CLAUDE_CONFIG_DIR/projects,
                ~/.config/claude/projects and ~/.claude/projects),
                --db FILE (default ~/.agentcost/agentcost.db)
"""
import argparse, csv, sys
from collections import Counter
from datetime import date
from pathlib import Path

from . import analytics as A
from .parse import default_log_dirs, log_files
from .rules import UNASSIGNED, load_config, price_for
from .store import DEFAULT_DB, connect, ingest, load_turns

def aggregate(turns, cfg, rules, month=None):
    """Return (rows, header, unpriced_models); rows sorted by cost, descending.

    `month` (YYYY-MM) is matched against the local date in cfg["timezone"].
    """
    recs, unpriced = A.enrich(turns, cfg, rules)
    if month:
        recs = [r for r in recs if A.bucket_key(r["day"], "month") == month]
    rows = [[g["name"], g["sessions"], g["in"], g["out"], g["cw"], g["cr"],
             round(g["cost"], 2), round(g["billable"], 2)]
            for g in A.breakdown(recs, "client")]
    sym = cfg.get("currency_symbol", "USD")
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

def print_subscription(cfg, month, total):
    if not (cfg.get("subscription") and month):
        return
    y, m = map(int, month.split("-"))
    start = date(y, m, 1)
    fee = A.subscription_fee(cfg, start, A._month_end(start))
    sym = cfg.get("currency_symbol", "USD")
    ratio = f" ({total / fee:.1f}× the fee)" if fee else ""
    print(f"Subscription {cfg['subscription'].get('name', '')}: {fee:.2f} {sym} "
          f"— API-equivalent {total:.2f} {sym}{ratio}")

def cmd_report(a):
    cfg, rules = load_config(a.config)
    con = connect(a.db)
    ingest(con, a.logs)
    rows, hdr, unpriced = aggregate(load_turns(con), cfg, rules, a.month)
    con.close()
    print_table(rows, hdr, a.month or "(all time)")
    print_subscription(cfg, a.month, sum(r[6] for r in rows))
    if unpriced:
        print(f"WARNING: no price for models: {', '.join(sorted(unpriced))}", file=sys.stderr)
    if a.csv:
        with open(a.csv, "w", newline="") as f:
            csv.writer(f).writerows([hdr] + rows)

def cmd_ingest(a):
    con = connect(a.db)
    added = ingest(con, a.logs)
    total = con.execute("SELECT COUNT(*) FROM turns").fetchone()[0]
    con.close()
    print(f"{added} new turns ingested, {total} stored in {a.db}")

def fmt_tokens(n):
    return f"{n / 1e6:.1f}M" if n >= 1e6 else f"{n / 1e3:.1f}k" if n >= 1e3 else str(n)

def cmd_doctor(a):
    """Explain what agentcost sees. Prints metadata only (paths, models, counts)."""
    print("Log folders:")
    for d in a.logs:
        d = Path(d).expanduser()
        n = len(log_files(d)) if d.is_dir() else 0
        print(f"  {'found  ' if d.is_dir() else 'missing'} {d}" + (f"  ({n} log files)" if d.is_dir() else ""))
    con = connect(a.db)
    added = ingest(con, a.logs)
    turns = load_turns(con)
    con.close()
    print(f"\nStore: {a.db} — {len(turns)} turns ({added} new)")
    if not turns:
        print("\nNo usage found. Is Claude Code writing logs to one of the folders above?"
              "\nPass the right folder with --logs DIR.")
        return
    cfg, rules = load_config(a.config)
    recs, unpriced = A.enrich(turns, cfg, rules)
    print(f"Data from {recs[0]['day']} to {recs[-1]['day']} ({cfg.get('timezone', 'UTC')})")

    print("\nModels:")
    by_model = {r["name"]: r for r in A.breakdown(recs, "model")}
    for model, n in Counter(r["model"] for r in recs).most_common():
        g = by_model[model]
        price = price_for(cfg["prices"], model)
        note = f"{g['cost']:.2f} {cfg.get('currency_symbol', 'USD')}" if price else "NO PRICE in clients.toml"
        print(f"  {model:32} {n:>7} turns  {fmt_tokens(g['tokens']):>8} tokens  {note}")

    print("\nClients:")
    counts = Counter(r["client"] for r in recs)
    for r in rules:
        print(f"  {r['name']:32} {counts.get(r['name'], 0):>7} turns")
    print(f"  {UNASSIGNED:32} {counts.get(UNASSIGNED, 0):>7} turns")
    if unpriced:
        print("\nTo price the missing models, add to clients.toml (USD per million tokens):")
        for m in sorted(unpriced):
            print(f'  [prices."{m}"]\n  input = 0.0\n  output = 0.0\n  cache_write = 0.0\n  cache_read = 0.0\n')

def cmd_serve(a):
    from .web import serve
    serve(a.config, a.db, a.logs, a.port, a.open)

def main(argv=None):
    argv = sys.argv[1:] if argv is None else list(argv)
    if not argv or argv[0].startswith("-") and argv[0] not in ("-h", "--help"):
        argv = ["report"] + argv  # old CLI: flags only

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--logs", action="append", help="log folder (repeatable)")
    common.add_argument("--db", default=DEFAULT_DB)

    ap = argparse.ArgumentParser(prog="agentcost")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("report", parents=[common], help="print a per-client table")
    p.add_argument("--config", required=True)
    p.add_argument("--month", help="YYYY-MM filter")
    p.add_argument("--csv", help="write per-client rows to CSV")
    p.set_defaults(fn=cmd_report)
    p = sub.add_parser("ingest", parents=[common], help="copy new log lines into the store")
    p.set_defaults(fn=cmd_ingest)
    p = sub.add_parser("serve", parents=[common], help="local web view")
    p.add_argument("--config", required=True)
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--open", action="store_true", help="open the browser")
    p.set_defaults(fn=cmd_serve)
    p = sub.add_parser("doctor", parents=[common], help="show found logs, models and clients")
    p.add_argument("--config", required=True)
    p.set_defaults(fn=cmd_doctor)

    a = ap.parse_args(argv)
    a.logs = a.logs or default_log_dirs()
    a.fn(a)
