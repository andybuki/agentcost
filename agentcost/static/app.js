// agentcost web view. Read-only; no external requests.
"use strict";

const $ = (s) => document.querySelector(s);
const SLOTS = ["--s1", "--s2", "--s3", "--s4", "--s5", "--s6", "--s7"];
const TOP = SLOTS.length;
const UNASSIGNED = "(unassigned)";

let meta = null;
let report = null;
let sort = { key: "cost", dir: -1 };

// ---------- helpers ----------
const esc = (s) => String(s).replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const iso = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
const parse = (s) => { const [y, m, d] = s.split("-").map(Number); return new Date(y, m - 1, d); };
const addDays = (d, n) => new Date(d.getFullYear(), d.getMonth(), d.getDate() + n);
const monthEnd = (y, m) => new Date(y, m + 1, 0);

let moneyFmt, moneyFmtShort;
function setupFormats(currency) {
  try {
    moneyFmt = new Intl.NumberFormat(undefined, { style: "currency", currency });
    moneyFmtShort = new Intl.NumberFormat(undefined, { style: "currency", currency, maximumFractionDigits: 0 });
  } catch {
    const n = new Intl.NumberFormat(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    const n0 = new Intl.NumberFormat(undefined, { maximumFractionDigits: 0 });
    moneyFmt = { format: (v) => `${n.format(v)} ${currency}` };
    moneyFmtShort = { format: (v) => `${n0.format(v)} ${currency}` };
  }
}
const money = (v) => moneyFmt.format(v);
const moneyShort = (v) => moneyFmtShort.format(v);
const int = (v) => new Intl.NumberFormat().format(v);
const compact = (v) => new Intl.NumberFormat(undefined, { notation: "compact", maximumFractionDigits: 1 }).format(v);
const pct = (v) => `${v >= 0 ? "+" : ""}${(v * 100).toFixed(Math.abs(v) < 0.1 ? 1 : 0)} %`;

function fmtDate(s) {
  return parse(s).toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" });
}
function fmtRange([a, b]) { return `${fmtDate(a)} – ${fmtDate(b)}`; }
function fmtBucket(key, bucket) {
  if (bucket === "month") {
    const [y, m] = key.split("-").map(Number);
    return new Date(y, m - 1, 1).toLocaleDateString(undefined, { month: "short", year: "2-digit" });
  }
  const d = parse(key).toLocaleDateString(undefined, { day: "numeric", month: "short" });
  return bucket === "week" ? `wk ${d}` : d;
}

function color(name) {
  if (name === "Other") return "var(--other)";
  const i = meta.orders[state.by].indexOf(name);
  return i >= 0 && i < TOP ? `var(${SLOTS[i]})` : "var(--other)";
}
const swatch = (name) => `<i class="swatch" style="background:${color(name)}"></i>`;

// `prev` is the comparison summary (null when comparing with nothing).
function deltaText(cur, prev, key, { absolute = false } = {}) {
  if (!prev) return "";
  if (!prev.turns) return "no data in comparison period";
  const p = prev[key];
  const arrow = cur > p ? "▲" : cur < p ? "▼" : "■";
  if (absolute) return `${arrow} ${cur - p > 0 ? "+" : ""}${money(cur - p)} vs. comparison`;
  if (!p) return cur ? "new vs. comparison" : "no change";
  return `${arrow} ${pct((cur - p) / p)} vs. comparison`;
}

// ---------- state <-> URL ----------
const state = { from: "", to: "", cmp: "prev", cmp_from: "", cmp_to: "", by: "client", bucket: "auto", metric: "cost", client: "", preset: "" };

function readUrl() {
  const q = new URLSearchParams(location.search);
  for (const k of Object.keys(state)) if (q.has(k)) state[k] = q.get(k);
}
function writeUrl() {
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(state)) if (v) q.set(k, v);
  history.replaceState(null, "", `?${q}`);
}

function presetRange(p, today) {
  const y = today.getFullYear(), m = today.getMonth();
  switch (p) {
    case "this-month": return [new Date(y, m, 1), monthEnd(y, m)];
    case "last-month": return [new Date(y, m - 1, 1), monthEnd(y, m - 1)];
    case "last-30": return [addDays(today, -29), today];
    case "this-quarter": { const q = Math.floor(m / 3) * 3; return [new Date(y, q, 1), monthEnd(y, q + 2)]; }
    case "ytd": return [new Date(y, 0, 1), today];
    case "last-12m": return [new Date(y, m - 11, 1), monthEnd(y, m)];
  }
  return null;
}

function applyPreset(p) {
  const r = presetRange(p, parse(meta.today));
  if (!r) return;
  state.preset = p;
  state.from = iso(r[0]);
  state.to = iso(r[1]);
}

function syncControls() {
  for (const k of ["from", "to", "cmp", "cmp_from", "cmp_to", "by", "bucket", "metric"]) $(`#${k}`).value = state[k];
  $("#cmp-custom").hidden = state.cmp !== "custom";
  document.querySelectorAll("[data-preset]").forEach((b) =>
    b.setAttribute("aria-pressed", String(b.dataset.preset === state.preset)));
  const crumbs = $("#crumbs");
  crumbs.hidden = !state.client;
  crumbs.innerHTML = state.client
    ? `<a href="#" id="crumb-all">All clients</a> › <strong>${esc(state.client)}</strong>` : "";
  if (state.client) $("#crumb-all").onclick = (e) => { e.preventDefault(); state.client = ""; state.by = "client"; load(); };
}

// ---------- data ----------
function query() {
  const q = new URLSearchParams({ from: state.from, to: state.to, cmp: state.cmp, by: state.by,
                                  bucket: state.bucket, metric: state.metric });
  if (state.cmp === "custom") { q.set("cmp_from", state.cmp_from); q.set("cmp_to", state.cmp_to); }
  if (state.client) q.set("client", state.client);
  return q;
}

async function load() {
  if (state.cmp === "custom" && !(state.cmp_from && state.cmp_to)) {
    // Prefill the custom comparison with the period directly before.
    const a = parse(state.from), b = parse(state.to);
    const len = Math.round((b - a) / 864e5);
    state.cmp_to = iso(addDays(a, -1)); state.cmp_from = iso(addDays(a, -1 - len));
  }
  syncControls();
  writeUrl();
  const q = query();
  $("#export").href = `/api/export.csv?${q}`;
  const res = await fetch(`/api/report?${q}`);
  const body = await res.json();
  if (!res.ok) {
    $("#warnings").innerHTML = `<div class="warn">${esc(body.error || "Request failed")}</div>`;
    return;
  }
  report = body;
  render();
}

// ---------- render ----------
function render() {
  const r = report;
  const cmpLabel = r.cmp_range ? ` compared with ${fmtRange(r.cmp_range)}` : "";
  $("#period-label").textContent = `${fmtRange(r.range)}${cmpLabel}`;
  const warn = [];
  if (r.unpriced.length)
    warn.push(`No price in <code>clients.toml</code> for: ${r.unpriced.map((m) => `<code>${esc(m)}</code>`).join(", ")}.
      Their tokens are shown, but they count as ${money(0)}. Add a <code>[prices."model-name"]</code> section
      (or run <code>python -m agentcost doctor</code> for a ready-made one).`);
  if (meta.cloud?.error)
    warn.push(`Cloud sessions could not be synced: ${esc(meta.cloud.error)}. Showing what was synced before.`);
  $("#warnings").innerHTML = warn.map((w) => `<div class="warn">${w}</div>`).join("");
  $("#notice").innerHTML = meta.has_clients || r.client ? "" :
    `<div class="note">No work is matched to a client yet, so this view shows usage by model and project.
      To see what you can bill, add <code>[[client]]</code> rules to <code>clients.toml</code>;
      the “Unassigned work” box below suggests ready-made ones.</div>`;
  renderKpis(r);
  renderTrend(r);
  renderTable(r);
  renderModels(r);
  renderSessions(r);
  renderUnassigned(r);
}

function kpi(label, value, delta, sub = "") {
  return `<div class="kpi"><div class="label">${label}</div><div class="value">${value}</div>
    <div class="delta">${delta || "&nbsp;"}</div>${sub ? `<div class="sub">${sub}</div>` : ""}</div>`;
}

function renderKpis(r) {
  const s = r.summary, c = r.cmp_summary;
  const costSub = s.unpriced_turns ? `${int(s.unpriced_turns)} turns without a price not included`
    : c?.turns ? `comparison: ${money(c.cost)}` : "";
  const tiles = [kpi("API-equivalent cost", money(s.cost), deltaText(s.cost, c, "cost"), costSub)];
  if (!meta.has_clients) {
    tiles.push(
      kpi("Tokens", compact(s.tokens), deltaText(s.tokens, c, "tokens"),
        `${compact(s.in + s.out)} in/out · ${compact(s.cr)} cache read`),
      kpi("Sessions", int(s.sessions), deltaText(s.sessions, c, "sessions"), `${int(s.turns)} agent turns`));
  } else tiles.push(
    kpi("Billable to clients", money(s.billable), deltaText(s.billable, c, "billable"),
      c?.turns ? `comparison: ${money(c.billable)}` : ""),
    // Margin can change sign, so a % change is meaningless; show the amount.
    kpi("Margin (billable − cost)", money(s.margin), deltaText(s.margin, c, "margin", { absolute: true }),
      s.cost ? `${((s.billable / s.cost - 1) * 100).toFixed(0)} % on cost` : ""),
    kpi("Sessions", int(s.sessions), deltaText(s.sessions, c, "sessions"), `${int(s.turns)} agent turns`));
  if (r.subscription) {
    const fee = r.subscription.fee;
    const ratio = fee ? s.cost / fee : 0;
    tiles.push(kpi(`${esc(r.subscription.name)} fee`, money(fee),
      fee ? `API-equivalent is ${ratio.toFixed(1)}× the fee` : "",
      fee ? (ratio >= 1 ? `saves ${money(s.cost - fee)} vs. pay-per-token`
                        : `${money(fee - s.cost)} more than pay-per-token`) : ""));
  }
  $("#kpis").innerHTML = tiles.join("");
}

// Round tick step (1/2/2.5/5 × 10^n) so axis labels read cleanly.
function emptyState() {
  if (!meta.turns_stored) {
    const dirs = meta.log_dirs.map((d) =>
      `<li><code>${esc(d.path)}</code> — ${d.found ? `${int(d.files)} log files` : "not found"}</li>`).join("");
    return `<div class="empty"><p><strong>No Claude Code usage found.</strong> Looked in:</p>
      <ul class="dirs">${dirs}</ul>
      <p>If your logs are somewhere else, start with <code>--logs DIR</code>.
      <code>python -m agentcost doctor</code> shows what was found.</p></div>`;
  }
  return `<div class="empty"><p>No agent activity between ${fmtRange(report.range)}.</p>
    <p>Your data covers ${fmtDate(meta.first_day)} – ${fmtDate(meta.last_day)}.</p>
    <button type="button" id="show-all">Show all data</button></div>`;
}

function niceScale(v, ticks = 4) {
  if (v <= 0) return { max: 1, step: 0.25 };
  const raw = v / ticks, p = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * p).find((x) => x >= raw);
  return { max: step * Math.ceil(v / step), step };
}

function renderTrend(r) {
  const el = $("#trend");
  const ts = r.timeseries;
  const n = ts.buckets.length;
  const totals = ts.buckets.map((_, i) => ts.series.reduce((a, s) => a + s.values[i], 0));
  const cmp = ts.cmp_values && r.cmp_summary.turns ? ts.cmp_values.slice(0, n) : null;
  const tokens = r.metric === "tokens";
  const fmt = tokens ? (v) => `${compact(v)} tokens` : money;
  const fmtAxis = tokens ? compact : moneyShort;
  $("#trend-title").textContent = tokens ? "Tokens over time" : "Cost over time";

  $("#legend").innerHTML = ts.series.map((s) => `<span>${swatch(s.name)}${esc(s.name)}</span>`).join("")
    + (cmp ? `<span><i class="swatch line"></i>Comparison period (total)</span>` : "");

  if (!r.summary.turns) {
    el.innerHTML = emptyState();
    const all = $("#show-all");
    if (all) all.onclick = () => { state.from = meta.first_day; state.to = meta.last_day; state.preset = ""; load(); };
    return;
  }
  if (!totals.some((v) => v > 0) && !(cmp && cmp.some((v) => v > 0))) {
    el.innerHTML = `<div class="empty">No cost in this period — the models used have no price yet.
      Switch “Chart shows” to Tokens.</div>`;
    return;
  }

  const W = Math.max(320, el.clientWidth), H = 260;
  const m = { t: 10, r: 8, b: 26, l: 56 };
  const iw = W - m.l - m.r, ih = H - m.t - m.b;
  const { max: ymax, step: ystep } = niceScale(Math.max(...totals, ...(cmp || [0])));
  const y = (v) => m.t + ih - (v / ymax) * ih;
  const step = iw / n;
  const bw = Math.max(2, Math.min(48, step * 0.7));
  const x = (i) => m.l + step * i + (step - bw) / 2;

  let svg = `<svg viewBox="0 0 ${W} ${H}" width="${W}" height="${H}" role="img" aria-label="Cost per ${r.bucket}">`;
  for (let v = 0; v <= ymax + ystep / 2; v += ystep) {
    const yy = y(v);
    svg += `<line x1="${m.l}" x2="${W - m.r}" y1="${yy}" y2="${yy}" stroke="var(--grid)" />`;
    svg += `<text x="${m.l - 8}" y="${yy + 4}" text-anchor="end">${fmtAxis(v)}</text>`;
  }
  const every = Math.ceil(n / Math.max(1, Math.floor(iw / 70)));
  ts.buckets.forEach((b, i) => {
    if (i % every === 0)
      svg += `<text x="${m.l + step * i + step / 2}" y="${H - 8}" text-anchor="middle">${esc(fmtBucket(b, r.bucket))}</text>`;
  });

  // Stacked bars: 2px surface gap between segments, rounded top on the last segment.
  ts.buckets.forEach((_, i) => {
    let acc = 0;
    const segs = ts.series.map((s) => ({ name: s.name, v: s.values[i] })).filter((s) => s.v > 0);
    segs.forEach((s, j) => {
      const y0 = y(acc), y1 = y(acc + s.v);
      acc += s.v;
      const h = Math.max(0, y0 - y1 - (j > 0 ? 2 : 0));
      if (h <= 0) return;
      const top = y1, last = j === segs.length - 1, rad = Math.min(4, h, bw / 2);
      const d = last
        ? `M${x(i)},${top + h} V${top + rad} Q${x(i)},${top} ${x(i) + rad},${top} H${x(i) + bw - rad} Q${x(i) + bw},${top} ${x(i) + bw},${top + rad} V${top + h} Z`
        : `M${x(i)},${top} h${bw} v${h} h${-bw} Z`;
      svg += `<path d="${d}" fill="${color(s.name)}" />`;
    });
  });
  svg += `<line x1="${m.l}" x2="${W - m.r}" y1="${y(0)}" y2="${y(0)}" stroke="var(--text-3)" />`;

  if (cmp) {
    const pts = cmp.map((v, i) => `${m.l + step * i + step / 2},${y(v)}`).join(" ");
    svg += `<polyline points="${pts}" fill="none" stroke="var(--cmp)" stroke-width="2" stroke-dasharray="5 4" stroke-linejoin="round" />`;
  }
  // Hover targets: full column height, wider than the bar.
  ts.buckets.forEach((_, i) => {
    svg += `<rect class="hit" data-i="${i}" x="${m.l + step * i}" y="${m.t}" width="${step}" height="${ih}" fill="transparent" />`;
  });
  svg += `</svg>`;
  el.innerHTML = svg;

  const tip = $("#tooltip");
  el.querySelectorAll(".hit").forEach((h) => {
    h.addEventListener("mousemove", (e) => {
      const i = Number(h.dataset.i);
      const rows = ts.series.filter((s) => s.values[i] > 0)
        .map((s) => `<div class="r"><span>${swatch(s.name)}${esc(s.name)}</span><span>${fmt(s.values[i])}</span></div>`).join("");
      const cmpRow = cmp && i < cmp.length
        ? `<div class="r"><span><i class="swatch line"></i>${esc(fmtBucket(ts.cmp_buckets[i], r.bucket))}</span><span>${fmt(cmp[i])}</span></div>` : "";
      tip.innerHTML = `<div class="t">${esc(fmtBucket(ts.buckets[i], r.bucket))}</div>${rows}
        <div class="r tot"><span>Total</span><span>${fmt(totals[i])}</span></div>${cmpRow}`;
      tip.hidden = false;
      const tw = tip.offsetWidth, th = tip.offsetHeight;
      let left = e.clientX + 14, top = e.clientY + 14;
      if (left + tw > innerWidth - 8) left = e.clientX - tw - 14;
      if (top + th > innerHeight - 8) top = e.clientY - th - 14;
      tip.style.left = `${left}px`; tip.style.top = `${top}px`;
      h.setAttribute("fill", "rgb(127 127 127 / .08)");
    });
    h.addEventListener("mouseleave", () => { tip.hidden = true; h.setAttribute("fill", "transparent"); });
  });
}

const COLS = [
  { key: "name", label: null, fmt: (v) => v },
  { key: "cost", label: "Cost", fmt: money },
  { key: "cmp_cost", label: "Comparison", fmt: money, cmp: true },
  { key: "delta", label: "Δ", fmt: (v) => (v > 0 ? "+" : "") + money(v), cmp: true },
  { key: "delta_pct", label: "Δ %", fmt: (v) => (v == null ? "new" : pct(v)), cmp: true },
  { key: "billable", label: "Billable", fmt: money, billing: true },
  { key: "margin", label: "Margin", fmt: money, billing: true },
  { key: "sessions", label: "Sessions", fmt: int },
  { key: "tokens", label: "Tokens", fmt: compact },
  { key: "share", label: "Share", fmt: (v) => `<span class="share"><i style="width:${Math.round(v * 48)}px"></i>${(v * 100).toFixed(0)} %</span>` },
];

function renderTable(r) {
  const dimLabel = { client: "Client", project: "Project", model: "Model" }[r.by];
  $("#table-title").textContent = `${dimLabel}s`;
  const cols = COLS.filter((c) => (!c.cmp || r.cmp_summary?.turns) && (!c.billing || meta.has_clients));
  const rows = [...r.table].sort((a, b) => {
    const av = a[sort.key], bv = b[sort.key];
    if (typeof av === "string") return sort.dir * av.localeCompare(bv);
    return sort.dir * ((av ?? Infinity) - (bv ?? Infinity));
  });
  const head = cols.map((c) => {
    const s = sort.key === c.key ? ` aria-sort="${sort.dir > 0 ? "ascending" : "descending"}"` : "";
    const arrow = sort.key === c.key ? (sort.dir > 0 ? " ↑" : " ↓") : "";
    return `<th data-key="${c.key}"${s}>${c.label ?? dimLabel}${arrow}</th>`;
  }).join("");
  const canDrill = r.by === "client" && !r.client;
  const body = rows.map((row) => {
    const cells = cols.map((c) => c.key === "name"
      ? `<td><span class="name">${swatch(row.name)}${esc(row.name)}</span></td>`
      : `<td>${c.fmt(row[c.key])}</td>`).join("");
    return `<tr${canDrill ? ` class="drill" data-name="${esc(row.name)}" tabindex="0"` : ""}>${cells}</tr>`;
  }).join("");
  const sum = (k) => r.table.reduce((a, x) => a + x[k], 0);
  const tot = { name: "Total", cost: sum("cost"), cmp_cost: sum("cmp_cost"), billable: sum("billable"),
                margin: sum("margin"), sessions: r.summary.sessions, tokens: r.summary.tokens,
                share: r.table.length ? 1 : 0 };
  tot.delta = tot.cost - tot.cmp_cost;
  tot.delta_pct = tot.cmp_cost ? tot.delta / tot.cmp_cost : null;
  const foot = cols.map((c) => `<td>${c.key === "name" ? "Total" : c.fmt(tot[c.key])}</td>`).join("");
  $("#table").innerHTML = `<thead><tr>${head}</tr></thead><tbody>${body}</tbody><tfoot><tr>${foot}</tr></tfoot>`;
  $("#table-hint").textContent = canDrill ? "Click a client to see its projects." :
    r.client ? "Sessions counts are per project; a session can appear under several." : "";

  $("#table").querySelectorAll("th").forEach((th) => th.onclick = () => {
    const k = th.dataset.key;
    sort = { key: k, dir: sort.key === k ? -sort.dir : (k === "name" ? 1 : -1) };
    renderTable(report);
  });
  $("#table").querySelectorAll("tr.drill").forEach((tr) => {
    const go = () => { state.client = tr.dataset.name; state.by = "project"; load(); };
    tr.onclick = go;
    tr.onkeydown = (e) => { if (e.key === "Enter") go(); };
  });
}

function renderModels(r) {
  const rows = r.model_mix;
  if (!rows.length) { $("#models").innerHTML = `<div class="empty">No data</div>`; return; }
  // Bars by cost, or by tokens when a model in view has no price (cost would read as 0).
  const byTokens = rows.some((x) => r.unpriced.includes(x.name));
  const val = (x) => (byTokens ? x.tokens : x.cost);
  const max = Math.max(...rows.map(val)) || 1;
  const total = rows.reduce((a, x) => a + val(x), 0) || 1;
  const label = (x) => {
    const cost = r.unpriced.includes(x.name) ? "no price" : money(x.cost);
    return `${cost} · ${compact(x.tokens)} tokens · ${((val(x) / total) * 100).toFixed(0)} %`;
  };
  const modelColor = (name) => {
    const i = meta.orders.model.indexOf(name);
    return i >= 0 && i < TOP ? `var(${SLOTS[i]})` : "var(--other)";
  };
  $("#models").innerHTML = `<div class="hbar">${rows.map((x) => `
    <span class="nm" title="${esc(x.name)}">${esc(x.name)}</span>
    <span class="track"><span class="fill" style="display:block;width:${(val(x) / max) * 100}%;background:${modelColor(x.name)}"></span></span>
    <span class="val">${label(x)}</span>`).join("")}</div>
    ${byTokens ? `<p class="hint">Bars show token share, since not every model has a price.</p>` : ""}`;
}

function fmtDateTime(isoString) {
  return new Date(isoString).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

function renderSessions(r) {
  const rows = r.sessions;
  $("#sessions-hint").textContent = rows.length >= 100 ? "latest 100 in this period" :
    `${rows.length} in this period`;
  if (!rows.length) { $("#sessions").innerHTML = `<tbody><tr><td class="muted">No sessions in this period.</td></tr></tbody>`; return; }
  const showClient = meta.has_clients;
  const head = ["Last activity", "Project", "Branch", "Source", ...(showClient ? ["Client"] : []),
                "Models", "Turns", "Tokens", "Cost"];
  const body = rows.map((s) => {
    const cells = [
      `<td title="started ${esc(fmtDateTime(s.start))}">${esc(fmtDateTime(s.end))}</td>`,
      `<td class="l"><span class="name">${state.by === "project" ? swatch(s.project) : ""}${esc(s.project)}</span></td>`,
      `<td class="l muted">${esc(s.branch || "—")}</td>`,
      `<td class="l"><span class="tag ${s.source === "cloud" ? "cloud" : ""}">${s.source === "cloud" ? "☁ cloud" : "local"}</span></td>`,
      ...(showClient ? [`<td class="l">${esc(s.client)}</td>`] : []),
      `<td class="l muted">${s.models.map(esc).join(", ")}</td>`,
      `<td>${int(s.turns)}</td>`, `<td>${compact(s.tokens)}</td>`, `<td>${money(s.cost)}</td>`,
    ];
    return `<tr>${cells.join("")}</tr>`;
  }).join("");
  const right = new Set(["Turns", "Tokens", "Cost"]);
  $("#sessions").innerHTML = `<thead><tr>${head.map((h, i) =>
    `<th class="static${right.has(h) || i === 0 ? "" : " l"}">${h}</th>`).join("")}</tr></thead><tbody>${body}</tbody>`;
}

function renderUnassigned(r) {
  const card = $("#unassigned-card");
  card.hidden = !!r.client;
  if (r.client) return;
  if (!r.unassigned.length) {
    $("#unassigned").innerHTML = `<div class="empty">${r.summary.turns
      ? "Everything in this period is attributed to a client." : "No activity in this period."}</div>`;
    return;
  }
  $("#unassigned").innerHTML = r.unassigned.map((u, i) => `
    <div class="ua">
      <div class="row"><span class="path">${esc(u.cwd || "(no directory)")}${u.branch ? ` <span class="muted">@ ${esc(u.branch)}</span>` : ""}</span>
        <span>${money(u.cost)}</span></div>
      ${u.snippet ? `<pre id="snip-${i}">${esc(u.snippet)}</pre>
      <button type="button" data-copy="${i}">Copy snippet</button>` :
      `<p class="hint">Session without a repository — usually not client work, so no rule is suggested.</p>`}
    </div>`).join("");
  $("#unassigned").querySelectorAll("[data-copy]").forEach((b) => b.onclick = async () => {
    const text = r.unassigned[Number(b.dataset.copy)].snippet;
    try { await navigator.clipboard.writeText(text); b.textContent = "Copied"; }
    catch { b.textContent = "Select and copy manually"; }
    setTimeout(() => (b.textContent = "Copy snippet"), 1500);
  });
}

// ---------- wiring ----------
async function init() {
  meta = await (await fetch("/api/meta")).json();
  setupFormats(meta.currency);
  $("#data-range").textContent = meta.first_day
    ? `data ${fmtDate(meta.first_day)} – ${fmtDate(meta.last_day)} · ${int(meta.turns_stored)} turns stored`
      + (meta.cloud.configured ? ` (${int(meta.cloud.turns)} from cloud sessions)` : "") + ` · ${meta.timezone}`
    : "no data yet — run Claude Code, then Refresh";
  const fromUrl = new URLSearchParams(location.search);
  readUrl();
  if (!state.from || !state.to) {
    // Last 30 days rather than the calendar month: on the 1st, "this month" is nearly empty.
    applyPreset(state.preset || "last-30");
    // Don't open on an empty period: fall back to the latest month with data.
    if (!fromUrl.has("preset") && meta.last_day && meta.last_day < state.from) {
      const last = parse(meta.last_day);
      state.from = iso(new Date(last.getFullYear(), last.getMonth(), 1));
      state.to = iso(monthEnd(last.getFullYear(), last.getMonth()));
      state.preset = "";
    }
  }
  if (!fromUrl.has("by") && !meta.has_clients) state.by = "model";
  // Cost would hide the usage of unpriced models, so start with tokens then.
  if (!fromUrl.has("metric") && meta.unpriced.length) state.metric = "tokens";

  document.querySelectorAll("[data-preset]").forEach((b) => b.onclick = () => { applyPreset(b.dataset.preset); load(); });
  for (const k of ["from", "to"]) $(`#${k}`).onchange = (e) => { state[k] = e.target.value; state.preset = ""; load(); };
  for (const k of ["cmp", "cmp_from", "cmp_to", "bucket", "metric"]) $(`#${k}`).onchange = (e) => { state[k] = e.target.value; load(); };
  $("#by").onchange = (e) => { state.by = e.target.value; load(); };
  $("#refresh").onclick = async () => {
    const b = $("#refresh");
    b.disabled = true; b.textContent = "Refreshing…";
    const res = await (await fetch("/api/refresh", { method: "POST" })).json();
    meta = await (await fetch("/api/meta")).json();
    b.textContent = res.added ? `${res.added} new turns` : "Up to date";
    setTimeout(() => { b.textContent = "Refresh data"; b.disabled = false; }, 1500);
    load();
  };
  let t;
  addEventListener("resize", () => { clearTimeout(t); t = setTimeout(() => report && renderTrend(report), 150); });
  load();
}

init();
