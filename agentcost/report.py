"""agentcost — per-client cost report for Claude Code sessions.

Reads Claude Code session logs (~/.claude/projects/**/*.jsonl) into a local
SQLite store, attributes every assistant turn to a client via rules in a TOML
config, prices tokens, and prints a report or serves a local web view. Only
metadata is read (usage, model, cwd, branch, time) — prompt text and file
contents are never touched. No LLM calls.

Usage:
  python -m agentcost init                           # create clients.toml from your data
  python -m agentcost [report] [--month 2026-10] [--csv out.csv]
  python -m agentcost ingest
  python -m agentcost serve [--port 8765] [--open]
  python -m agentcost doctor                         # what was found, what is missing

--config defaults to clients.toml in the current folder.
  python -m agentcost sync [--url URL --token READ_TOKEN]   # cloud sessions, see docs/cloud.md
  python -m agentcost cloud-setup                    # setup script for cloud environments
  python -m agentcost hook-setup --url URL --token INGEST_TOKEN   # another PC / laptop
  python -m agentcost tokens                         # new ingest + read token pair
  python -m agentcost collector [--host H --port P]  # self-hosted collector (instead of Cloudflare)

Common options: --logs DIR (repeatable; default: $CLAUDE_CONFIG_DIR/projects,
                ~/.config/claude/projects and ~/.claude/projects),
                --db FILE (default ~/.agentcost/agentcost.db)
"""
import argparse, csv, sys
from collections import Counter
from datetime import date
from pathlib import Path

from . import analytics as A
from .billing import has_retainer
from .parse import default_log_dirs, log_files
from .rules import UNASSIGNED, ConfigError, load_config, price_for
from .store import DEFAULT_DB, connect, ingest, load_turns
from .sync import CLOUD_CONFIG, SyncError, load_cloud_config, save_cloud_config, sync, sync_if_configured

def collect(con, a):
    """Read new local log lines and, if configured, new cloud turns. Returns (local, cloud)."""
    local = ingest(con, a.logs)
    cloud, err = sync_if_configured(con, a.cloud_config)
    if err:
        print(f"WARNING: cloud sync failed: {err}", file=sys.stderr)
    return local, cloud

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
    collect(con, a)
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
    local, cloud = collect(con, a)
    total = con.execute("SELECT COUNT(*) FROM turns").fetchone()[0]
    con.close()
    print(f"{local} new local and {cloud} new cloud turns, {total} stored in {a.db}")

def cmd_sync(a):
    if a.url or a.token:
        if not (a.url and a.token):
            sys.exit("agentcost: give both --url and --token (the READ token)")
        save_cloud_config(a.url, a.token, a.cloud_config)
        print(f"Saved collector settings to {Path(a.cloud_config).expanduser()}")
    cfg = load_cloud_config(a.cloud_config)
    if not cfg:
        sys.exit("agentcost: no collector configured. Run once with --url URL --token READ_TOKEN "
                 "(see docs/cloud.md).")
    con = connect(a.db)
    if a.full:  # download everything again; stored turns get missing fields filled in
        con.execute("DELETE FROM sync_state WHERE url = ?", (cfg["url"],))
    try:
        added = sync(con, cfg["url"], cfg["read_token"])
    except SyncError as e:
        sys.exit(f"agentcost: sync failed: {e}")
    total = con.execute("SELECT COUNT(*) FROM turns WHERE source = 'cloud'").fetchone()[0]
    con.close()
    print(f"{added} new cloud turns from {cfg['url']} ({total} cloud turns stored)")

SETUP_SCRIPT = """#!/bin/bash
# agentcost: report usage metadata of Claude Code cloud sessions to your collector.
# Paste into the cloud environment's "Setup script". Set AGENTCOST_URL and
# AGENTCOST_INGEST_TOKEN as environment variables of the same environment.
# Sends only time, folder, branch, model and token counts — never prompts or code.
mkdir -p ~/.agentcost-hook
cat > ~/.agentcost-hook/agentcost_hook.py <<'AGENTCOST_HOOK_EOF'
{hook}AGENTCOST_HOOK_EOF
python3 - <<'AGENTCOST_SETTINGS_EOF'
import json, pathlib
p = pathlib.Path.home() / ".claude" / "settings.json"
p.parent.mkdir(parents=True, exist_ok=True)
s = json.loads(p.read_text()) if p.exists() else {{}}
cmd = "python3 ~/.agentcost-hook/agentcost_hook.py"
stop = s.setdefault("hooks", {{}}).setdefault("Stop", [])
if not any(h.get("command") == cmd for e in stop for h in e.get("hooks", [])):
    stop.append({{"hooks": [{{"type": "command", "command": cmd, "timeout": 15}}]}})
p.write_text(json.dumps(s, indent=2))
AGENTCOST_SETTINGS_EOF
"""

def setup_script():
    hook = (Path(__file__).parent / "cloud_hook.py").read_text()
    assert "AGENTCOST_HOOK_EOF" not in hook
    return SETUP_SCRIPT.format(hook=hook if hook.endswith("\n") else hook + "\n")

def cmd_cloud_setup(a):
    print(setup_script(), end="")

def claude_settings_path():
    import os
    base = os.environ.get("CLAUDE_CONFIG_DIR") or "~/.claude"
    return Path(base).expanduser() / "settings.json"

def install_hook(url, token, device=None, settings=None, hook_dir=None):
    """Install the Stop hook on this computer. Returns (hook file, settings file, device)."""
    import json, os
    from . import cloud_hook
    hook_dir = Path(hook_dir or cloud_hook.HOOK_DIR)
    settings = Path(settings or claude_settings_path())
    hook_dir.mkdir(parents=True, exist_ok=True)
    hook = hook_dir / "agentcost_hook.py"
    hook.write_text((Path(__file__).parent / "cloud_hook.py").read_text(encoding="utf-8"), encoding="utf-8")
    device = (device or cloud_hook.device_name()).strip()[:100]
    cfg = hook_dir / "config.json"
    cfg.write_text(json.dumps({"url": url.rstrip("/"), "ingest_token": token.strip(), "device": device},
                              indent=2))
    try:
        os.chmod(cfg, 0o600)  # the token is a secret; no effect on Windows
    except OSError:
        pass
    # Absolute, quoted, forward slashes: works whether Claude Code runs hooks via
    # cmd, PowerShell or Git Bash, and with a Python that is not called "python3".
    py = Path(sys.executable).as_posix()
    cmd = f'"{py}" "{hook.as_posix()}"'
    s = json.loads(settings.read_text(encoding="utf-8")) if settings.is_file() else {}
    stop = s.setdefault("hooks", {}).setdefault("Stop", [])
    # Replace an earlier agentcost hook (other Python, other folder) instead of adding a second.
    for e in stop:
        e["hooks"] = [h for h in e.get("hooks", []) if "agentcost_hook.py" not in h.get("command", "")]
    stop[:] = [e for e in stop if e.get("hooks")]
    stop.append({"hooks": [{"type": "command", "command": cmd, "timeout": 15}]})
    settings.parent.mkdir(parents=True, exist_ok=True)
    settings.write_text(json.dumps(s, indent=2), encoding="utf-8")
    return hook, settings, device

def cmd_hook_setup(a):
    from . import cloud_hook
    hook, settings, device = install_hook(a.url, a.token, a.device)
    print(f"Installed {hook}")
    print(f"Stop hook added to {settings}")
    print(f"This computer reports as device '{device}'.")
    if a.no_backfill:
        return
    print("Sending the transcripts still on this computer (metadata only)...")
    url, token, device = cloud_hook.settings()
    if cloud_hook.send(cloud_hook.transcripts(), url, token, device):
        sys.exit(1)
    print("Done. New Claude Code sessions here are reported after every reply.")

def cmd_init(a):
    from .init import build
    target = Path(a.config)
    if target.exists() and not (a.force or a.print):
        sys.exit(f"agentcost: {target.resolve()} already exists. Use --print to see a "
                 "suggestion, or --force to overwrite it.")
    con = connect(a.db)
    local, cloud = collect(con, a)
    turns = load_turns(con)
    con.close()
    text, n_clients = build(turns)
    if a.print:
        print(text, end="")
        return
    target.write_text(text, encoding="utf-8")
    load_config(target)  # the generated file must load cleanly
    print(f"Created {target.resolve()} with {n_clients} client(s) from {len(turns)} stored turns.")
    print("Next: open it, rename clients, merge folders, set markups. Then:")
    print("  python -m agentcost serve --open")

def cmd_monthly(a):
    import re
    from . import monthly
    cfg, rules = load_config(a.config)
    try:
        monthly.month_range(a.month)
    except ValueError:
        sys.exit("agentcost: --month must look like 2026-10")
    con = connect(a.db)
    collect(con, a)
    recs, _ = A.enrich(load_turns(con), cfg, rules)
    con.close()
    if a.client:
        clients = [a.client]
    else:  # every client with activity in that month
        start, end = monthly.month_range(a.month)
        # Internal work (markup 0) has nothing to bill; it shows up with --internal.
        clients = [r["name"] for r in A.breakdown(A.select(recs, start, end), "client")
                   if r["name"] != UNASSIGNED and (a.internal or r["billable"] > 0)]
        # Retainer clients (fixed or minimum fee) are invoiced even without usage.
        clients += [r["name"] for r in rules if has_retainer(r["terms"]) and r["name"] not in clients]
        if not clients:
            sys.exit(f"agentcost: no client activity in {a.month}")
    out_dir = Path(a.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    for client in clients:
        html = monthly.render(monthly.build(recs, cfg, rules, client, a.month), a.internal)
        slug = re.sub(r"[^A-Za-z0-9]+", "-", client).strip("-").lower() or "client"
        path = out_dir / f"{slug}-{a.month}{'-internal' if a.internal else ''}.html"
        path.write_text(html, encoding="utf-8")
        print(f"{path}  — open it in a browser, then Print / save as PDF")

def cmd_tokens(a):
    import secrets
    print(f"INGEST token (cloud environment, AGENTCOST_INGEST_TOKEN): {secrets.token_urlsafe(32)}")
    print(f"READ token   (your PC, agentcost sync --token):          {secrets.token_urlsafe(32)}")

def cmd_collector(a):
    import os
    from .collector import serve_collector
    ingest_token = os.environ.get("AGENTCOST_INGEST_TOKEN")
    read_token = os.environ.get("AGENTCOST_READ_TOKEN")
    if not (ingest_token and read_token):
        sys.exit("agentcost: set AGENTCOST_INGEST_TOKEN and AGENTCOST_READ_TOKEN "
                 "(create them with: python -m agentcost tokens)")
    try:
        serve_collector(a.db, a.host, a.port, ingest_token, read_token)
    except ValueError as e:
        sys.exit(f"agentcost: {e}")

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
    added, cloud_added = collect(con, a)
    turns = load_turns(con)
    con.close()
    cloud_cfg = load_cloud_config(a.cloud_config)
    print(f"\nCloud sessions: " + (f"collector {cloud_cfg['url']} ({cloud_added} new turns)"
                                   if cloud_cfg else "not set up (see docs/cloud.md)"))
    n_cloud = sum(1 for t in turns if t["source"] == "cloud")
    print(f"\nStore: {a.db} — {len(turns)} turns ({len(turns) - n_cloud} local, {n_cloud} cloud; "
          f"{added + cloud_added} new)")
    if not turns:
        print("\nNo usage found. Is Claude Code writing logs to one of the folders above?"
              "\nPass the right folder with --logs DIR.")
        return
    cfg, rules = load_config(a.config)
    recs, unpriced = A.enrich(turns, cfg, rules)
    print(f"Data from {recs[0]['day']} to {recs[-1]['day']} ({cfg.get('timezone', 'UTC')})")

    # A device that stopped reporting looks like a day without work: show who reported when.
    print("\nDevices (where usage came from):")
    devs = A.devices(recs)
    for d in devs:
        note = (f"  <- quiet: {d['behind_days']} days behind the newest data" if d["quiet"]
                else "  (inactive)" if d["inactive"] else "")
        print(f"  {d['device']:24} {d['source']:5} {d['turns']:>7} turns  "
              f"{d['first_day']} .. {d['last_day']}{note}")
    if any(d["quiet"] for d in devs):
        print("  A quiet device either had no work or stopped reporting. If you worked there,\n"
              "  check its hook: docs/cloud.md, section \"Several devices\".")
    print("  Not listed: regular claude.ai chats (no usage data) and Claude Code on computers\n"
          "  without the hook (python -m agentcost hook-setup, on that computer).")

    sym = cfg.get("currency_symbol", "USD")
    print(f"\nStored per month:          local turns  cloud turns    cost {sym}")
    months = {}
    for r in recs:
        m = months.setdefault(A.bucket_key(r["day"], "month"), {"local": 0, "cloud": 0, "cost": 0.0})
        m[r["source"]] += 1
        m["cost"] += r["cost"]
    for key in sorted(months):
        m = months[key]
        print(f"  {key}                 {m['local']:>11}  {m['cloud']:>11}  {m['cost']:>9.2f}")

    print("\nModels:")
    by_model = {r["name"]: r for r in A.breakdown(recs, "model")}
    for model, n in Counter(r["model"] for r in recs).most_common():
        g = by_model[model]
        price = price_for(cfg["prices"], model)
        source = ("clients.toml" if any(model.startswith(k) for k in cfg["user_prices"])
                  else "built-in")
        note = (f"{g['cost']:.2f} {cfg.get('currency_symbol', 'USD')} ({source} price)" if price
                else "NO PRICE — add it to clients.toml")
        print(f"  {model:32} {n:>7} turns  {fmt_tokens(g['tokens']):>8} tokens  {note}")

    print("\nClients:")
    counts = Counter(r["client"] for r in recs)
    for r in rules:
        print(f"  {r['name']:32} {counts.get(r['name'], 0):>7} turns")
    print(f"  {UNASSIGNED:32} {counts.get(UNASSIGNED, 0):>7} turns")

    # Which folders still need a rule, newest work first.
    folders = {}
    for r in recs:
        if r["client"] == UNASSIGNED:
            f = folders.setdefault(r["cwd"] or "(no folder)", {"turns": 0, "last": r["day"]})
            f["turns"] += 1
            f["last"] = max(f["last"], r["day"])
    if folders:
        print("\nFolders without a client (add a [[client]] rule, see docs/clients.md):")
        for cwd, f in sorted(folders.items(), key=lambda kv: kv[1]["last"], reverse=True)[:15]:
            print(f"  {cwd}  ({f['turns']} turns, last {f['last']})")
    if unpriced:
        print("\nTo price the missing models, add to clients.toml (USD per million tokens):")
        for m in sorted(unpriced):
            print(f'  [prices."{m}"]\n  input = 0.0\n  output = 0.0\n  cache_write = 0.0\n  cache_read = 0.0\n')

def cmd_serve(a):
    from .web import serve
    serve(a.config, a.db, a.logs, a.port, a.open, a.cloud_config)

def main(argv=None):
    argv = sys.argv[1:] if argv is None else list(argv)
    if not argv or argv[0].startswith("-") and argv[0] not in ("-h", "--help"):
        argv = ["report"] + argv  # old CLI: flags only

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--logs", action="append", help="log folder (repeatable)")
    common.add_argument("--db", default=DEFAULT_DB)
    common.add_argument("--cloud-config", default=CLOUD_CONFIG, help=argparse.SUPPRESS)

    ap = argparse.ArgumentParser(prog="agentcost")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("report", parents=[common], help="print a per-client table")
    p.add_argument("--config", default="clients.toml", help="default: clients.toml in this folder")
    p.add_argument("--month", help="YYYY-MM filter")
    p.add_argument("--csv", help="write per-client rows to CSV")
    p.set_defaults(fn=cmd_report)
    p = sub.add_parser("ingest", parents=[common], help="copy new log lines into the store")
    p.set_defaults(fn=cmd_ingest)
    p = sub.add_parser("serve", parents=[common], help="local web view")
    p.add_argument("--config", default="clients.toml", help="default: clients.toml in this folder")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--open", action="store_true", help="open the browser")
    p.set_defaults(fn=cmd_serve)
    p = sub.add_parser("doctor", parents=[common], help="show found logs, models and clients")
    p.add_argument("--config", default="clients.toml", help="default: clients.toml in this folder")
    p.set_defaults(fn=cmd_doctor)
    p = sub.add_parser("sync", parents=[common], help="download cloud-session turns from your collector")
    p.add_argument("--url", help="collector URL (saved for next time)")
    p.add_argument("--token", help="READ token (saved for next time)")
    p.add_argument("--full", action="store_true", help="download everything again (after updates)")
    p.set_defaults(fn=cmd_sync)
    p = sub.add_parser("cloud-setup", parents=[common], help="print the setup script for cloud environments")
    p.set_defaults(fn=cmd_cloud_setup)
    p = sub.add_parser("hook-setup", help="report Claude Code usage of THIS computer to your collector")
    p.add_argument("--url", required=True, help="collector URL")
    p.add_argument("--token", required=True, help="INGEST token")
    p.add_argument("--device", help="name for this computer (default: its host name)")
    p.add_argument("--no-backfill", action="store_true", help="don't send the existing transcripts now")
    p.set_defaults(fn=cmd_hook_setup)
    p = sub.add_parser("init", parents=[common], help="create clients.toml from the folders in your data")
    p.add_argument("--config", default="clients.toml", help="file to write (default: clients.toml)")
    p.add_argument("--force", action="store_true", help="overwrite an existing file")
    p.add_argument("--print", action="store_true", help="print the suggestion instead of writing")
    p.set_defaults(fn=cmd_init)
    p = sub.add_parser("monthly", parents=[common], help="monthly report per client (HTML, print to PDF)")
    p.add_argument("--config", default="clients.toml", help="default: clients.toml in this folder")
    p.add_argument("--month", required=True, help="YYYY-MM")
    p.add_argument("--client", help="client name (default: every client with activity)")
    p.add_argument("--internal", action="store_true", help="include cost, markup and margin")
    p.add_argument("--out-dir", default="reports", help="folder for the HTML files (default: reports)")
    p.set_defaults(fn=cmd_monthly)
    p = sub.add_parser("tokens", help="create a new ingest + read token pair")
    p.set_defaults(fn=cmd_tokens)
    p = sub.add_parser("collector", help="run a self-hosted collector (instead of Cloudflare)")
    p.add_argument("--host", default="127.0.0.1", help="0.0.0.0 to accept connections from outside")
    p.add_argument("--port", type=int, default=8787)
    p.add_argument("--db", default="~/.agentcost/collector.db")
    p.set_defaults(fn=cmd_collector)

    a = ap.parse_args(argv)
    if hasattr(a, "logs"):
        a.logs = a.logs or default_log_dirs()
    if hasattr(a, "config") and a.cmd != "init" and not Path(a.config).is_file():
        ap.exit(2, f"agentcost: config file not found: {Path(a.config).resolve()}\n"
                   "Run the command in the folder that contains clients.toml, pass "
                   "--config PATH, or create one with: python -m agentcost init\n")
    try:
        a.fn(a)
    except ConfigError as e:
        ap.exit(2, f"agentcost: problem in {getattr(a, 'config', 'clients.toml')}: {e}\n")
