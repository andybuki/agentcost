# agentcost

Per-client cost report for AI coding agent sessions (Claude Code first).

Reads only metadata from Claude Code session logs (tokens, model, working
directory, git branch, timestamp), attributes each turn to a client via
regex rules, prices it, applies a per-client markup, and shows the result in
a local web view or as a table / CSV. Prompt text and file contents are never
read. No LLM calls, no network calls.

![Web view](docs/screenshot.png)

## Quick start

```bash
# web view with synthetic data (May–Oct 2026) → http://127.0.0.1:8765/
python gen_sample.py
python -m agentcost serve --config clients.toml --logs sample_logs --db sample.db --open

# real logs (default location ~/.claude/projects)
python -m agentcost serve --config clients.toml --open
python -m agentcost report --config clients.toml --month 2026-10 --csv report.csv
```

Requires Python 3.11+ (uses `tomllib`). No runtime dependencies. On Windows,
`pip install tzdata` for time zone support.

## Commands

| command | what it does |
|---|---|
| `ingest` | copy new log lines into the local store (`~/.agentcost/agentcost.db`) |
| `report` | ingest, then print a per-client table (`--month`, `--csv`); the default when only flags are given |
| `serve` | ingest, then serve the web view on `127.0.0.1` (`--port`, `--open`) |

All take `--logs DIR` and `--db FILE`.

**Why a store:** Claude Code deletes session logs after 30 days by default
(`cleanupPeriodDays`). Run `ingest` (or `serve` / `report`) at least that often
— e.g. from cron — and history is kept. Only token counts are stored; clients
and prices are applied when you look, so fixing a rule in `clients.toml`
re-attributes all history.

## Web view

- Period presets (this/last month, last 30 days, quarter, YTD, last 12 months) or a custom range.
- Compare with the previous period, the same period last year, or a custom range.
- Totals: API-equivalent cost, billable, margin, sessions — each with the change vs. the comparison.
- Subscription: fee for the period next to the API-equivalent cost (if `[subscription]` is configured).
- Cost over time, stacked by client, project or model, with the comparison period as a dashed line.
- Sortable comparison table; click a client to drill into its projects. CSV export.
- Model mix, and unassigned work with a ready-to-paste `clients.toml` rule.
- The URL holds the view, so you can bookmark it. Read-only: rules are edited in
  `clients.toml` only, and changes show up on the next page load.

## Tests

```bash
pip install pytest
python -m pytest
```

## Configuration

See `clients.toml`. Prices are USD per million tokens — the shipped values are
placeholders, verify against the provider's current price list.
Branch rules take priority over directory rules, so a shared monorepo can be
split by branch naming (e.g. `acme/*`).

Other settings: `timezone` (days and months are counted in local time),
`currency_symbol` / `currency_rate` (one fixed rate), and an optional
`[subscription]` with `name`, `monthly_fee` and `since`.

## Notes

- On a subscription plan the numbers are an API-equivalent cost, not actual spend.
- `(unassigned)` shows work not yet attributed to any client.
