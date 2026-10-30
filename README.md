# agentcost

Per-client cost report for AI coding agent sessions (Claude Code first).

Reads only metadata from Claude Code session logs (tokens, model, working
directory, git branch, timestamp), attributes each turn to a client via
regex rules, prices it, applies a per-client markup and prints a monthly
report. Prompt text and file contents are never read. No LLM calls.

## Quick start

```bash
# test with synthetic data
python gen_sample.py
python agentcost.py --config clients.toml --logs sample_logs --month 2026-10

# real logs (default location ~/.claude/projects)
python agentcost.py --config clients.toml --month 2026-10 --csv report.csv
```

Requires Python 3.11+ (uses `tomllib`).

## Configuration

See `clients.toml`. Prices are USD per million tokens — the shipped values are
placeholders, verify against the provider's current price list.
Branch rules take priority over directory rules, so a shared monorepo can be
split by branch naming (e.g. `acme/*`).

## Notes

- On a subscription plan the numbers are an API-equivalent cost, not actual spend.
- `(unassigned)` shows work not yet attributed to any client.
