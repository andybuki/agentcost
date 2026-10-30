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

Common options: --logs DIR (default ~/.claude/projects),
                --db FILE (default ~/.agentcost/agentcost.db)
"""
import argparse, csv, sys
from datetime import date

from . import analytics as A
from .rules import load_config
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

def cmd_serve(a):
    from .web import serve
    serve(a.config, a.db, a.logs, a.port, a.open)

def main(argv=None):
    argv = sys.argv[1:] if argv is None else list(argv)
    if not argv or argv[0].startswith("-") and argv[0] not in ("-h", "--help"):
        argv = ["report"] + argv  # old CLI: flags only

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--logs", default="~/.claude/projects")
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

    a = ap.parse_args(argv)
    a.fn(a)
