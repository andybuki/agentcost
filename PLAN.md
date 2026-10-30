# agentcost — plan: local web view with period comparison

Goal of this iteration: open a browser tab and answer
"how much did I spend (and can I bill) this month vs. last month — per client,
per project, per model?"

Constraints carried over from `CLAUDE.md`: local-first, metadata only, no LLM
calls, and — new — **no network calls from the web view** (no CDN, no
analytics). Everything is served from `127.0.0.1`.

---

## Why a data store comes first

Claude Code deletes session logs older than `cleanupPeriodDays` (default
30 days). Today `agentcost.py` re-reads the raw `.jsonl` every run, so
"compare October with July" is impossible: July's logs are gone.

→ Ingest turns into a local SQLite file (`~/.agentcost/agentcost.db`) and keep
them. The web view and the CLI both query the DB, not the raw logs.

---

## Phase 0 — groundwork (small, unblocks everything)

1. **Split `agentcost.py` into a package** — no behaviour change:
   ```
   agentcost/
     parse.py      # iter_turns()  (log reading + dedup)
     rules.py      # load_config(), attribute(), price_for()
     store.py      # SQLite schema, ingest, queries
     report.py     # CLI table / CSV (current main())
     web.py        # http server + JSON API
     static/index.html, app.js, style.css
   agentcost.py    # thin wrapper, keeps old CLI working
   ```
2. **pytest suite** (CLAUDE.md step 2): dedup, branch-before-cwd attribution,
   longest-prefix pricing, unpriced models, `(unassigned)` bucket.
3. **`gen_sample.py`: spread data over ~6 months** (currently only 2026-10),
   with a visible trend and one month spike — otherwise there is nothing to
   compare.
4. Sanity check against real `~/.claude/projects` logs (CLAUDE.md step 1).

## Phase 1 — local store

Table `turns` (one row per deduped assistant message):

| column | note |
|---|---|
| `msg_id`, `request_id` | primary key → re-ingest is idempotent |
| `ts` (UTC), `session`, `cwd`, `branch`, `model` | metadata only |
| `in`, `out`, `cache_write`, `cache_read` | token counts |
| `source_file`, `ingested_at` | for debugging |

Design decisions:
- **Store raw tokens, not cost.** Client and price are computed at query time
  from `clients.toml`, so fixing a rule or a price instantly re-attributes all
  history. (Optional later: snapshot prices per month for invoiced periods.)
- `agentcost ingest` — incremental (remember file size/mtime per file).
  `serve` runs an ingest on start and on a "Refresh" button.
- Timezone: store UTC, group by local time (`Europe/Berlin` default,
  configurable) — a turn at 00:30 CET belongs to the next day/month.

## Phase 2 — JSON API (stdlib `http.server`, no new dependencies)

`agentcost serve [--port 8765]` → binds to `127.0.0.1` only.

| endpoint | returns |
|---|---|
| `GET /api/summary?from=&to=` | totals: cost, billable, margin, tokens, sessions |
| `GET /api/timeseries?from=&to=&bucket=day\|week\|month&by=client\|model\|project` | stacked series |
| `GET /api/breakdown?from=&to=&by=client\|model\|project\|session` | table rows |
| `GET /api/compare?a_from=&a_to=&b_from=&b_to=&by=client` | A, B, Δ abs, Δ % per row |
| `GET /api/unassigned?from=&to=` | top cwd/branch combos without a client (to write rules) |
| `GET /api/export.csv?...` | same as breakdown, CSV |

All money in the configured currency; every response carries `currency`,
`price_source` and a list of unpriced models so the UI can warn.

## Phase 3 — web view (single page, vanilla JS, inline SVG charts)

No build step, no framework, no CDN. Layout top → bottom:

1. **Period bar** — presets: this month / last month / this quarter /
   last 30 days / YTD / custom range; bucket toggle day·week·month.
   Toggle **"compare with"**: previous period (default) · same period last year · custom.
2. **KPI tiles** — Cost · Billable · Margin (billable − cost) · Sessions ·
   each with Δ vs. comparison period (▲ +12 % / ▼ −8 %).
3. **Trend chart** — stacked bars per bucket, stacked by client (or model);
   comparison period drawn as a faint line on top.
4. **Comparison table** — rows = clients (switchable: projects, models):
   cost A · cost B · Δ · Δ % · billable · share of total; sortable; click a
   client → drill-down to its projects/sessions.
5. **Model mix** — share of cost by model (answers "did Opus usage explode?").
6. **Unassigned panel** — top unattributed directories/branches with a
   copy-paste TOML snippet for a new rule.
7. **Export** — CSV of the current view.

URL holds the state (`?from=…&to=…&cmp=prev&by=client`) so a view can be
bookmarked.

## Phase 4 — after the web view (existing roadmap, re-ordered)

- Session splitting when one session spans several clients (CLAUDE.md step 3).
- Human time + fixed-price vs. T&M margin (step 4) — adds a `margin` column
  that already has a place in the UI.
- Client-facing monthly report: "Print / PDF" view of one client + one month
  (step 5), then DATEV / sevDesk export.
- Budgets/alerts per client ("Acme > 80 % of monthly budget").
- More agents (Codex CLI, Cursor, Copilot) → `parse.py` gets one adapter per
  agent writing into the same `turns` table (add an `agent` column).

---

## Suggested order of PRs

| # | PR | size |
|---|---|---|
| 1 | Package split + pytest + multi-month sample data | S |
| 2 | SQLite store + `ingest` command; CLI reads from DB | M |
| 3 | `serve` + JSON API (+ API tests) | M |
| 4 | Web view: period bar, KPI tiles, comparison table | M |
| 5 | Web view: trend chart, model mix, unassigned panel, CSV | M |
| 6 | Client monthly report (print view) | S |

## Open questions

1. Currency: keep one fixed `currency_rate`, or per-month rates (ECB)? Fixed
   is fine for MVP; per-month matters once invoices are generated.
2. Subscription users (Max/Pro): show "API-equivalent cost" next to the
   actual flat fee, so the view also answers "is my subscription worth it"?
3. Should the web view be able to edit client rules, or stay read-only and
   leave `clients.toml` as the single source of truth? (Proposal: read-only
   + generated snippets for MVP.)
