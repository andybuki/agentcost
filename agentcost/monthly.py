"""Monthly report for one client: a printable, self-contained HTML page.

The client version shows billable amounts only (cost × markup); the internal
version adds cost, markup and margin. Both are built from the same records as
the web view, so the numbers always match.
"""
import calendar
from collections import defaultdict
from datetime import date
from html import escape

from . import analytics as A
from .billing import REASONS, settle
from .rules import UNASSIGNED

def month_range(month):
    """'2026-10' -> (date(2026,10,1), date(2026,10,31)); raises ValueError."""
    y, m = (int(x) for x in month.split("-"))
    return date(y, m, 1), date(y, m, calendar.monthrange(y, m)[1])

def build(recs, cfg, rules, client, month):
    start, end = month_range(month)
    cur = A.select(recs, start, end, client)
    rule = next((r for r in rules if r["name"] == client), None)
    markup = rule["markup"] if rule else 0.0
    weeks = defaultdict(lambda: {"cost": 0.0, "billable": 0.0, "turns": 0})
    for r in cur:
        w = weeks[A.bucket_key(r["day"], "week")]
        w["cost"] += r["cost"]; w["billable"] += r["billable"]; w["turns"] += 1
    unpriced = sorted({r["model"] for r in cur if not r["priced"]})
    summary = A.summary(cur)
    billing = settle(summary["billable"], rule["terms"] if rule else None)
    return {
        "client": client, "month": month, "start": start, "end": end, "markup": markup,
        "currency": cfg.get("currency_symbol", "USD"), "rate": cfg.get("currency_rate", 1.0),
        "company": (cfg.get("company") or {}).get("name", ""),
        "summary": summary, "billing": billing,
        "projects": A.breakdown(cur, "project"),
        "models": A.breakdown(cur, "model"),
        "weeks": [{"week": k, **v} for k, v in sorted(weeks.items())],
        "sessions": sorted(A.sessions(cur, limit=10_000), key=lambda s: s["start"]),
        "unpriced": unpriced,
        "known_client": client in {r["name"] for r in rules} or client == UNASSIGNED,
    }

def _money(v, cur):
    s = f"{v:,.2f}".replace(",", " ")  # thin space as thousands separator
    return f"{s} {escape(cur)}"

def _tokens(n):
    return f"{n / 1e6:.1f}M" if n >= 1e6 else f"{n / 1e3:.1f}k" if n >= 1e3 else str(n)

def render(d, internal=False, generated=None, nav=None):
    """HTML page. `nav` (web view only) adds month navigation links and loads the
    print button's script from print.js, since the server forbids inline scripts."""
    cur = d["currency"]
    m = lambda v: _money(v, cur)
    s = d["summary"]
    month_name = d["start"].strftime("%B %Y")
    generated = generated or date.today()

    def table(head, rows, right_from=1, foot=None):
        th = "".join(f'<th class="{"r" if i >= right_from else ""}">{escape(h)}</th>' for i, h in enumerate(head))
        body = "".join("<tr>" + "".join(f'<td class="{"r" if i >= right_from else ""}">{c}</td>'
                                        for i, c in enumerate(r)) + "</tr>" for r in rows)
        tf = ("<tfoot>" + "".join(
            "<tr>" + "".join(f'<td class="{"r" if i >= right_from else ""}">{c}</td>'
                             for i, c in enumerate(row)) + "</tr>" for row in foot) + "</tfoot>") if foot else ""
        return f"<table><thead><tr>{th}</tr></thead><tbody>{body}</tbody>{tf}</table>"

    def amount_cols(row):
        return [m(row["cost"]), m(row["billable"])] if internal else [m(row["billable"])]
    amount_head = ["Cost", "Billable"] if internal else ["Amount"]

    b = d["billing"]
    foot = [["Total", str(s["sessions"]), _tokens(s["tokens"]), *amount_cols(s)]]
    if b["rule"]:  # terms changed the amount: show usage, adjustment and what is invoiced
        blank = [""] if internal else []
        foot = [["Usage value", str(s["sessions"]), _tokens(s["tokens"]), *amount_cols(s)],
                [escape(REASONS[b["rule"]]), "", "", *blank, m(b["adjustment"])],
                ["Total", "", "", *blank, m(b["amount"])]]
    projects = table(["Project", "Sessions", "Tokens", *amount_head],
                     [[escape(p["name"]), str(p["sessions"]), _tokens(p["tokens"]), *amount_cols(p)]
                      for p in d["projects"]],
                     foot=foot)
    models = table(["Model", "Turns", "Tokens", *amount_head],
                   [[escape(x["name"]), str(x["turns"]), _tokens(x["tokens"]), *amount_cols(x)]
                    for x in d["models"]])
    weeks = table(["Week starting", "Agent turns", *amount_head],
                  [[w["week"], str(w["turns"]), *amount_cols(w)] for w in d["weeks"]])
    sessions = table(["Date", "Project", "Branch", "Models", "Tokens", *amount_head],
                     [[s_["start"][:16].replace("T", " "), escape(s_["project"]),
                       escape(s_["branch"] or "—"), escape(", ".join(s_["models"])),
                       _tokens(s_["tokens"]), *amount_cols(s_)] for s_ in d["sessions"]],
                     right_from=4)

    tiles = [("Amount for " + month_name if not internal else "Billable", m(b["amount"])),
             ("Sessions", str(s["sessions"])), ("Agent turns", f"{s['turns']:,}".replace(",", " ")),
             ("Tokens", _tokens(s["tokens"]))]
    if internal:
        tiles[1:1] = [("API-equivalent cost", m(s["cost"])), ("Markup", f"× {d['markup']:g}"),
                      ("Margin", m(b["amount"] - s["cost"]))]
    tiles_html = "".join(f'<div class="tile"><div class="k">{escape(k)}</div><div class="v">{v}</div></div>'
                         for k, v in tiles)

    notes = [f"Amounts are calculated from the token usage of AI coding agents at the "
             f"providers' API list prices, converted at a fixed rate of {d['rate']:g} {escape(cur)} per USD"
             + (f" and multiplied by {d['markup']:g}." if internal else ".")]
    notes.append("Only usage metadata (time, project folder, branch, model, token counts) was "
                 "used — no prompts, answers or code.")
    if b["rule"]:
        notes.append(escape(REASONS[b["rule"]]) + " applies to this month; the tables below show "
                     "the usage value before that adjustment.")
    if d["unpriced"]:
        notes.append("Models without a price are not included: " + escape(", ".join(d["unpriced"])) + ".")
    if not d["known_client"]:
        notes.append("Warning: no client rule with this name exists in clients.toml.")
    warn = "" if s["turns"] else '<p class="empty">No agent activity for this client in this month.</p>'

    links = ""
    if nav:
        links = " ".join(f'<a href="{escape(href)}">{escape(label)}</a>' for label, href in nav)
    button = (f'<div class="actions"><span class="nav">{links}</span>'
              '<button id="print" type="button">Print / save as PDF</button></div>')
    script = ('<script src="print.js"></script>' if nav is not None else
              '<script>document.getElementById("print").onclick = () => window.print();</script>')
    variant = "internal" if internal else "client"
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{escape(d['client'])} — AI agent usage {escape(d['month'])}</title>
<style>
  :root {{ --text:#111; --muted:#5b5b57; --line:#e3e2dd; --bg:#fff; --accent:#2a78d6; }}
  * {{ box-sizing: border-box; }}
  body {{ margin:0; background:var(--bg); color:var(--text);
         font:13px/1.45 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif; font-variant-numeric:tabular-nums; }}
  main {{ max-width: 860px; margin: 0 auto; padding: 32px 24px 48px; }}
  header {{ display:flex; justify-content:space-between; align-items:flex-start; gap:16px;
           border-bottom: 2px solid var(--text); padding-bottom: 12px; margin-bottom: 20px; }}
  h1 {{ font-size: 22px; margin: 0 0 2px; }}
  h2 {{ font-size: 14px; margin: 24px 0 8px; }}
  .muted, .meta {{ color: var(--muted); }}
  .meta {{ text-align: right; font-size: 12px; }}
  .tiles {{ display:grid; grid-template-columns: repeat(auto-fit, minmax(150px,1fr)); gap: 10px; }}
  .tile {{ border:1px solid var(--line); border-radius:8px; padding:10px 12px; }}
  .tile .k {{ color: var(--muted); font-size: 12px; }}
  .tile .v {{ font-size: 18px; font-weight: 600; margin-top: 2px; }}
  .tile:first-child {{ border-color: var(--text); }}
  table {{ width:100%; border-collapse: collapse; }}
  th, td {{ text-align:left; padding: 5px 8px; border-bottom: 1px solid var(--line); vertical-align: top; }}
  th {{ color: var(--muted); font-weight: 500; font-size: 12px; }}
  .r {{ text-align: right; white-space: nowrap; }}
  td:first-child {{ white-space: nowrap; }}
  tfoot td {{ font-weight: 600; border-bottom: none; border-top: 1px solid var(--text); }}
  .notes {{ margin-top: 24px; color: var(--muted); font-size: 11.5px; }}
  .notes p {{ margin: 0 0 4px; }}
  .empty {{ padding: 16px; border: 1px dashed var(--line); text-align: center; color: var(--muted); }}
  .actions {{ display:flex; justify-content:space-between; align-items:center; gap:12px; margin-bottom: 12px; }}
  .nav a {{ color: var(--accent); margin-right: 12px; text-decoration: none; }}
  button {{ font: inherit; padding: 6px 12px; border: 1px solid var(--line); border-radius: 6px;
           background: #f6f6f4; cursor: pointer; }}
  .two {{ display:grid; grid-template-columns: 1fr 1fr; gap: 20px; }}
  @media (max-width: 700px) {{ .two {{ grid-template-columns: 1fr; }} header {{ flex-direction: column; }}
                              .meta {{ text-align: left; }} }}
  @media print {{
    @page {{ size: A4; margin: 16mm 14mm; }}
    main {{ padding: 0; max-width: none; }}
    .actions {{ display: none; }}
    h2 {{ break-after: avoid; }}
    tr {{ break-inside: avoid; }}
  }}
</style></head>
<body><main data-variant="{variant}">
{button}
<header>
  <div><h1>{escape(d['client'])}</h1>
    <div class="muted">AI agent usage · {escape(month_name)}{' · internal view' if internal else ''}</div></div>
  <div class="meta">{escape(d['company']) + '<br>' if d['company'] else ''}Period {d['start'].strftime('%d.%m.%Y')} – {d['end'].strftime('%d.%m.%Y')}<br>
    Created {generated.strftime('%d.%m.%Y')}</div>
</header>
<section class="tiles">{tiles_html}</section>
{warn}
<h2>By project</h2>{projects}
<div class="two">
  <section><h2>By model</h2>{models}</section>
  <section><h2>By week</h2>{weeks}</section>
</div>
<h2>Sessions</h2>{sessions}
<div class="notes">{''.join(f'<p>{n}</p>' for n in notes)}</div>
</main>{script}</body></html>
"""
