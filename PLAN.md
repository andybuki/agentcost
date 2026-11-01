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

Status: done except step 4 (needs real logs).

1. ✅ **Split `agentcost.py` into a package** — no behaviour change
   (verified: identical output to the old script). Run with
   `python -m agentcost`; `store.py`, `web.py` and `static/` come in later PRs:
   ```
   agentcost/
     parse.py      # iter_turns()  (log reading + dedup)
     rules.py      # load_config(), attribute(), price_for()
     store.py      # SQLite schema, ingest, queries
     report.py     # CLI table / CSV (current main())
     web.py        # http server + JSON API
     static/index.html, app.js, style.css
   ```
2. ✅ **pytest suite** (CLAUDE.md step 2): dedup, branch-before-cwd attribution,
   longest-prefix pricing, unpriced models, `(unassigned)` bucket.
3. ✅ **`gen_sample.py`: spread data over ~6 months** (currently only 2026-10),
   with a visible trend and one month spike — otherwise there is nothing to
   compare.
4. Sanity check against real `~/.claude/projects` logs (CLAUDE.md step 1).

## Phase 1 — local store ✅

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
- `agentcost ingest` — incremental (remembers size/mtime/byte offset per file;
  a half-written last line is left for the next run).
  `serve` runs an ingest on start and on a "Refresh" button.
- Timezone: store UTC, group by local time (`Europe/Berlin` default,
  configurable) — a turn at 00:30 CET belongs to the next day/month.

## Phase 2 — JSON API ✅ (stdlib `http.server`, no new dependencies)

`agentcost serve [--port 8765]` → binds to `127.0.0.1` only, rejects foreign
`Host` headers (DNS rebinding), strict CSP. Built as one report endpoint instead
of five, since the page always needs all parts together:

| endpoint | returns |
|---|---|
| `GET /api/meta` | currency, timezone, data range, stable color order per dimension, subscription |
| `GET /api/report?from=&to=&cmp=prev\|yoy\|custom\|none&by=client\|project\|model&bucket=auto\|day\|week\|month[&client=]` | summary + comparison summary, subscription fee, time series (+ comparison line), comparison table, model mix, unassigned |
| `GET /api/export.csv?…` | comparison table as CSV |
| `POST /api/refresh` | ingest new log lines |

## Phase 3 — web view ✅ (single page, vanilla JS, inline SVG charts)

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

## Roadmap after the first real use (2026-10-31)

The first user works mostly in Claude Code cloud sessions, had no clients set
up, and got stuck on configuration. Order adjusted accordingly.

| # | Step | Status |
|---|---|---|
| 1 | Cloud capture: hook → Cloudflare Worker / Python collector → `sync` | ✅ verified end to end |
| 2 | Onboarding: `agentcost init`, clear config errors, built-in price list, sessions list with project, getting-started guide | ✅ |
| 3 | **Exact cache-write cost:** Claude Code writes 1-hour cache entries (2× input, not 1.25×). Parse `cache_creation.ephemeral_1h_input_tokens`, store it (new column; Worker + collector + hook accept the field; D1 `ALTER TABLE` + redeploy), price it separately. | ✅ Worker migrates itself; `sync --full` refills |
| 4 | **Monthly client report:** print/PDF view of one client + one month (sessions, projects, models, cost, billable) | next |
| 5 | Manual cost entries: subscriptions and tools without logs (ChatGPT, DeepSeek chat…), split across clients | |
| 6 | `agent` field in the store; "why assigned" (matched rule) in the UI; manual override per session | |
| 7 | Budgets / alerts per client; CSV/DATEV export | |
| 8 | More agents (Codex CLI first, then by demand); Haiku 5.5 long-prompt price tier | |

Deliberately not now: content-based session splitting (would need prompt
text), markup approval history, team mode.

## Decisions

1. **Currency:** one fixed `currency_rate` in `clients.toml` — no per-month rates.
2. **Subscriptions:** show both. Add to `clients.toml`:
   ```toml
   [subscription]
   name = "Claude Max"
   monthly_fee = 180.0   # in currency_symbol
   since = 2026-05-01    # optional: no fee before the plan started
   ```
   The web view shows, per period, the actual flat fee (pro-rated for
   partial periods) next to the API-equivalent cost, plus the ratio
   ("API-equivalent is 3.2× your fee"). Client billing still uses the
   API-equivalent cost × markup. No `[subscription]` section → pay-per-use,
   the fee tile is hidden. It is also hidden when drilled into one client,
   since the fee covers all clients.
3. **Rule editing:** `clients.toml` is the only place rules are edited. The web
   view is read-only; the unassigned panel generates TOML snippets to paste in.
