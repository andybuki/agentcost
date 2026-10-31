"""`agentcost init`: write a starter clients.toml from the folders found in your data."""
from collections import defaultdict
from datetime import date

from .analytics import NO_REPO, project_name
from .rules import cwd_pattern, normalize_path, toml_string

HEADER = """\
# agentcost configuration — created by `agentcost init` on {today}.
# Guide: docs/getting-started.md · Client rules: docs/clients.md

currency_symbol = "EUR"
currency_rate = 0.92       # USD -> EUR, one fixed rate; adjust when needed
default_markup = 1.0       # billable = cost × markup
timezone = "Europe/Berlin" # days and months are counted in this time zone

# Prices: a built-in list covers Claude models (agentcost/prices.toml).
# To override a model or add another one (USD per million tokens):
# [prices."claude-opus-5-5"]
# input = 4.0
# output = 20.0
# cache_write = 5.0
# cache_read = 0.2

# Optional flat-rate plan, shown next to the API-equivalent cost:
# [subscription]
# name = "Claude Max"
# monthly_fee = 180.0      # in currency_symbol
# since = 2026-05-01

# ---------------------------------------------------------------------------
# Clients. One rule per project folder found in your agent logs, most used
# first. Now: rename them, merge folders that belong to the same client into
# one [[client]] (several cwd patterns are fine), set markup = 0 for internal
# work, and delete what you don't need. Work no rule matches shows up as
# "(unassigned)". Changes apply to all history.
# ---------------------------------------------------------------------------
"""

def folders(turns):
    """Per normalized folder: turns, last day seen, sources. Most used first.

    A subfolder (Claude Code moved into e.g. repo/cloud/worker) is counted under
    its parent folder when that parent was also used: the parent's rule covers it.
    """
    seen = {normalize_path(t["cwd"]).rstrip("/") for t in turns} - {""}

    def root(cwd):
        parents = [p for p in seen if cwd.startswith(p + "/") and project_name(p) != NO_REPO]
        return min(parents, key=len) if parents else cwd

    roots = {cwd: root(cwd) for cwd in seen}
    stats = defaultdict(lambda: {"turns": 0, "last": "", "sources": set()})
    for t in turns:
        cwd = normalize_path(t["cwd"]).rstrip("/")
        if not cwd:
            continue
        s = stats[roots[cwd]]
        s["turns"] += 1
        s["last"] = max(s["last"], t["ts"][:10])
        s["sources"].add(t.get("source") or "local")
    return sorted(stats.items(), key=lambda kv: (-kv[1]["turns"], kv[0]))

def build(turns, today=None):
    """Text of a starter clients.toml. Folders with the same name (e.g. a repo used
    locally and in cloud sessions) become one client."""
    today = today or date.today()
    clients, skipped = defaultdict(list), []
    for cwd, s in folders(turns):
        name = project_name(cwd)
        if name == NO_REPO:
            skipped.append((cwd, s))
            continue
        clients[name].append((cwd, s))

    out = [HEADER.format(today=today.isoformat())]
    for name, entries in clients.items():
        total = sum(s["turns"] for _, s in entries)
        out.append(f"\n[[client]]\nname = {toml_string(name)}\ncwd = [\n")
        for cwd, s in entries:
            src = "+".join(sorted(s["sources"]))
            out.append(f"  {toml_string(cwd_pattern(cwd))},  # {s['turns']} turns, last {s['last']}, {src}\n")
        out.append(f"]\nmarkup = 1.0  # {total} turns in total\n")
    if skipped:
        out.append("\n# Sessions without a repository (cloud sessions started without one):\n")
        for cwd, s in skipped:
            out.append(f"#   {cwd}  ({s['turns']} turns) — usually not client work\n")
    if not clients:
        out.append("\n# No project folders found yet. Example:\n"
                   "# [[client]]\n# name = \"Acme GmbH\"\n# cwd = ['/clients/acme/']\n# markup = 1.3\n")
    return "".join(out), len(clients)
