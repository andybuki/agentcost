# agentcost — project context

## Goal
Product idea: FinOps for a solo architect / small agency working with AI agents.
Answer "what did agent work cost per client, and what can I bill?" — the gap
between LLM observability tools (Langfuse, Helicone: built for AI *products*)
and coding-agent usage trackers (ccusage, aidash: per developer/project, no
client or billing layer).

## Principles
- Local-first, metadata only: never read prompt text or file contents (DSGVO selling point).
- Zero LLM calls in the core pipeline — deterministic parsing and rules.
- Target market: DACH freelancers, consultancies, agencies (2–20 people).

## Current state (MVP)
- `agentcost/` package, run with `python -m agentcost`:
  - `parse.py` — parses `~/.claude/projects/**/*.jsonl`, dedups streamed
    lines by (message.id, requestId).
  - `rules.py` — attribution by branch regex then cwd regex, pricing via
    longest model-prefix match.
  - `store.py` — SQLite store (`~/.agentcost/agentcost.db`), incremental ingest;
    raw tokens only, so history survives Claude Code's 30-day log cleanup.
  - `analytics.py` — local-time enrichment (client, project, cost), period
    comparison, time series, subscription fee, unassigned rule snippets.
  - `report.py` — CLI (`init` / `doctor` / `report` / `serve` / `ingest` / `sync` / …).
  - `init.py` — starter clients.toml from the folders in the data.
  - `monthly.py` — printable monthly report per client (client / internal
    version), served at `/report` and written by the `monthly` command.
  - `prices.toml` — built-in API list prices (checked 2026-10); clients.toml overrides.
  - `web.py` + `static/` — local web view on 127.0.0.1, vanilla JS + SVG,
    no external requests. Read-only.
  - `cloud_hook.py` (standalone Stop hook in cloud sessions) → `cloud/worker`
    (Cloudflare Worker + D1, user's own account, separate ingest/read tokens)
    → `sync.py` pulls into the store (`source = 'cloud'`). See `docs/cloud.md`.
    Alternative collector: `collector.py` (`python -m agentcost collector`),
    same API; `tests/test_collectors.py` runs one contract against both.
    Cloud capture sends metadata to the user's own collector — the only path
    where data leaves the machine.
  - Several devices: the hook also runs on other computers
    (`hook-setup` → `~/.agentcost-hook/config.json`); every turn carries a
    `device` ("cloud", host name, or `local`/`cloud` for old rows).
    `analytics.devices` flags devices 3–29 days behind the others (≥30: inactive) (`doctor`, web
    warning) — a silent device otherwise looks like a day without work.
- `clients.toml` — the user's rules (git-ignored, created by `init`); validated
  with clear `ConfigError` messages. `clients.example.toml` is the demo config.
  Single source of truth for rules; the web view stays read-only.
- `gen_sample.py` — synthetic logs, 2026-05..2026-10 with an Acme spike in August.
- `tests/` — pytest suite. `PLAN.md` — roadmap. Docs: `docs/getting-started.md`,
  `docs/clients.md`, `docs/cloud.md`.

## Next steps
1. ✅ Verified against real Claude Code logs on Windows (2026-10): field names
   match; Windows paths are normalized to "/" for rules and project names.
   Finding: the first user works mostly in Claude Code cloud sessions → cloud
   capture added. ✅ Verified end to end in a real cloud environment with a
   deployed Cloudflare Worker (2026-10-31). Lessons: Cloudflare blocks the
   default Python-urllib User-Agent (error 1010); users typed variables into
   the environment *name*; wrong secret pasted (Account ID) → see docs/cloud.md.
2. ✅ Onboarding (`init`, config validation, built-in prices, sessions list).
   ✅ Exact 1-hour cache-write pricing (`cw1h`, 2× input; Claude Code writes
   only 1-hour entries). ✅ Monthly client report. Next: manual cost entries — see `PLAN.md`.
3. Session splitting when one session touches several clients.
4. Human time + token cost → margin per client (fixed price vs T&M).
5. Client-facing monthly report (HTML/PDF), export for DATEV / sevDesk.
6. Support more agents (Codex CLI, Cursor, Copilot).
