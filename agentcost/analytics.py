"""Period queries over stored turns: summaries, time series, comparisons.

Everything here is computed at query time from raw token counts plus the
current clients.toml, so rule and price fixes apply to all history.
"""
import calendar
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import PurePosixPath
from zoneinfo import ZoneInfo

from .rules import UNASSIGNED, attribute, cwd_pattern, normalize_path, price_for, toml_string, turn_cost

OTHER = "Other"
DIMENSIONS = ("client", "project", "model")
TOKEN_KEYS = ("in", "out", "cw", "cr")
NO_REPO = "(no repository)"
# Home folders of cloud containers: a session there has no repository checked out.
HOME_DIRS = {"/home/user", "/root"}

def project_name(cwd):
    path = normalize_path(cwd).rstrip("/")
    if not path:
        return "(none)"
    if path in HOME_DIRS:
        return NO_REPO
    return PurePosixPath(path).name or path

def enrich(turns, cfg, rules):
    """Attach local day, client, project, cost and billable amount to each turn.

    Returns (records sorted by time, set of models without a price). Turns of
    unpriced models are kept with cost 0 and priced=False, so their usage still
    shows; callers must surface the unpriced set so cost is never silently low.
    """
    tz = ZoneInfo(cfg.get("timezone", "UTC"))
    rate = cfg.get("currency_rate", 1.0)
    prices = cfg["prices"]
    markups = {r["name"]: r["markup"] for r in rules}
    clients, price_cache, unpriced, out = {}, {}, set(), []
    for t in turns:
        try:
            dt = datetime.fromisoformat(t["ts"].replace("Z", "+00:00")).astimezone(tz)
        except (ValueError, AttributeError):
            continue
        model = t["model"]
        if model not in price_cache:
            price_cache[model] = price_for(prices, model)
        p = price_cache[model]
        if p is None:
            unpriced.add(model)
        key = (t["cwd"], t["branch"])
        if key not in clients:
            clients[key] = attribute(rules, *key)
        client = clients[key]
        cost = turn_cost(t, p) * rate if p else 0.0
        out.append({
            "day": dt.date(), "dt": dt, "client": client, "model": model,
            "source": t.get("source") or "local",
            "project": project_name(t["cwd"]),
            "session": t["session"], "cwd": normalize_path(t["cwd"]), "branch": t["branch"],
            "in": t["in"], "out": t["out"], "cw": t["cw"], "cr": t["cr"],
            "cost": cost, "billable": cost * markups.get(client, 0.0), "priced": p is not None,
        })
    out.sort(key=lambda r: r["dt"])
    return out, unpriced

def select(recs, start, end, client=None):
    """Records with start <= day <= end (local dates, inclusive)."""
    return [r for r in recs if start <= r["day"] <= end
            and (client is None or r["client"] == client)]

def _totals(recs):
    g = {"cost": 0.0, "billable": 0.0, "turns": len(recs), **{k: 0 for k in TOKEN_KEYS}}
    sessions = set()
    for r in recs:
        g["cost"] += r["cost"]; g["billable"] += r["billable"]
        for k in TOKEN_KEYS:
            g[k] += r[k]
        sessions.add(r["session"])
    g["sessions"] = len(sessions)
    g["margin"] = g["billable"] - g["cost"]
    g["tokens"] = sum(g[k] for k in TOKEN_KEYS)
    g["unpriced_turns"] = sum(1 for r in recs if not r["priced"])
    return g

def summary(recs):
    return _totals(recs)

def breakdown(recs, by):
    groups = defaultdict(list)
    for r in recs:
        groups[r[by]].append(r)
    rows = [{"name": k, **_totals(v)} for k, v in groups.items()]
    return sorted(rows, key=lambda r: (-r["cost"], -r["tokens"], r["name"]))

def compare(recs_a, recs_b, by):
    """Rows for period A with period B alongside; includes rows only present in B."""
    a = {r["name"]: r for r in breakdown(recs_a, by)}
    b = {r["name"]: r for r in breakdown(recs_b, by)}
    total_a = sum(r["cost"] for r in a.values())
    rows = []
    for name in a.keys() | b.keys():
        ra, rb = a.get(name), b.get(name)
        ca = ra["cost"] if ra else 0.0
        cb = rb["cost"] if rb else 0.0
        rows.append({
            "name": name, "cost": ca, "cmp_cost": cb, "delta": ca - cb,
            "delta_pct": (ca - cb) / cb if cb else None,
            "billable": ra["billable"] if ra else 0.0,
            "cmp_billable": rb["billable"] if rb else 0.0,
            "margin": ra["margin"] if ra else 0.0,
            "sessions": ra["sessions"] if ra else 0,
            "tokens": ra["tokens"] if ra else 0,
            "cmp_tokens": rb["tokens"] if rb else 0,
            "share": ca / total_a if total_a else 0.0,
        })
    return sorted(rows, key=lambda r: (-r["cost"], -r["tokens"], -r["cmp_cost"], r["name"]))

def bucket_key(d, bucket):
    if bucket == "day":
        return d.isoformat()
    if bucket == "week":
        return (d - timedelta(days=d.weekday())).isoformat()  # Monday
    return f"{d.year:04d}-{d.month:02d}"

def auto_bucket(start, end):
    days = (end - start).days + 1
    return "day" if days <= 45 else "week" if days <= 200 else "month"

def buckets(start, end, bucket):
    keys, d = [], start
    while d <= end:
        k = bucket_key(d, bucket)
        if not keys or keys[-1] != k:
            keys.append(k)
        d += timedelta(days=1)
    return keys

METRICS = ("cost", "tokens")

def _value(r, metric):
    return r["cost"] if metric == "cost" else r["in"] + r["out"] + r["cw"] + r["cr"]

def timeseries(recs, start, end, bucket, by, order, top=7, metric="cost"):
    """Cost (or tokens) per bucket, one series per `by` value.

    Series beyond the first `top` names in `order` (stable, all-time order) fold
    into "Other", so colors never depend on the selected period.
    """
    keys = buckets(start, end, bucket)
    idx = {k: i for i, k in enumerate(keys)}
    keep = set(order[:top])
    series = defaultdict(lambda: [0.0] * len(keys))
    for r in recs:
        name = r[by] if r[by] in keep else OTHER
        series[name][idx[bucket_key(r["day"], bucket)]] += _value(r, metric)
    names = [n for n in order[:top] if n in series] + ([OTHER] if OTHER in series else [])
    return {"buckets": keys, "series": [{"name": n, "values": series[n]} for n in names]}

def bucket_totals(recs, start, end, bucket, metric="cost"):
    keys = buckets(start, end, bucket)
    idx = {k: i for i, k in enumerate(keys)}
    vals = [0.0] * len(keys)
    for r in recs:
        vals[idx[bucket_key(r["day"], bucket)]] += _value(r, metric)
    return vals

def previous_period(start, end):
    """Same length, directly before. Whole calendar months map to whole months."""
    if start.day == 1 and end == _month_end(end) and start <= end:
        months = (end.year - start.year) * 12 + end.month - start.month + 1
        pend = start - timedelta(days=1)
        y, m = pend.year, pend.month - months + 1
        while m < 1:
            y, m = y - 1, m + 12
        return date(y, m, 1), pend
    length = end - start
    pend = start - timedelta(days=1)
    return pend - length, pend

def year_before(start, end):
    def back(d):
        try:
            return d.replace(year=d.year - 1)
        except ValueError:  # 29 Feb
            return d.replace(year=d.year - 1, day=28)
    return back(start), back(end)

def _month_end(d):
    return d.replace(day=calendar.monthrange(d.year, d.month)[1])

def subscription_fee(cfg, start, end):
    """Flat subscription fee for the period, pro-rated by days per month."""
    sub = cfg.get("subscription")
    if not sub:
        return None
    since = sub.get("since")  # TOML date; no fee before the plan started
    if since:
        start = max(start, since)
    fee, d = 0.0, start
    while d <= end:
        days_in_month = calendar.monthrange(d.year, d.month)[1]
        last = min(end, _month_end(d))
        fee += float(sub["monthly_fee"]) * ((last - d).days + 1) / days_in_month
        d = last + timedelta(days=1)
    return fee

def unassigned(recs, limit=10):
    """Top cwd/branch combinations without a client, with a TOML rule snippet."""
    groups = defaultdict(list)
    for r in recs:
        if r["client"] == UNASSIGNED:
            groups[(r["cwd"], r["branch"])].append(r)
    rows = []
    for (cwd, branch), v in groups.items():
        t = _totals(v)
        # A home-folder rule would match every cloud session, so offer none there.
        snippet = None if project_name(cwd) in (NO_REPO, "(none)") else rule_snippet(cwd)
        rows.append({"cwd": cwd, "branch": branch, "cost": t["cost"],
                     "sessions": t["sessions"], "snippet": snippet})
    return sorted(rows, key=lambda r: -r["cost"])[:limit]

def rule_snippet(cwd, name=None):
    name = name or (project_name(cwd) if project_name(cwd) not in (NO_REPO, "(none)") else "New client")
    return (f"[[client]]\nname = {toml_string(name)}\n"
            f"cwd = [{toml_string(cwd_pattern(cwd))}]\nmarkup = 1.0\n")

def sessions(recs, limit=100):
    """One row per agent session, most recent first."""
    groups = defaultdict(list)
    for r in recs:
        groups[r["session"]].append(r)
    rows = []
    for sid, v in groups.items():
        t = _totals(v)
        first, last = v[0], v[-1]  # recs are sorted by time
        rows.append({
            "session": sid, "start": first["dt"].isoformat(timespec="minutes"),
            "end": last["dt"].isoformat(timespec="minutes"),
            "project": last["project"], "branch": last["branch"], "client": last["client"],
            "source": last["source"], "models": sorted({r["model"] for r in v}),
            "turns": t["turns"], "tokens": t["tokens"], "cost": t["cost"],
            "billable": t["billable"],
        })
    rows.sort(key=lambda r: r["end"], reverse=True)
    return rows[:limit]
