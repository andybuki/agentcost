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
  - `report.py` — CLI (`report` / `ingest` / `serve`), table / CSV output.
  - `web.py` + `static/` — local web view on 127.0.0.1, vanilla JS + SVG,
    no external requests. Read-only.
  - `cloud_hook.py` (standalone Stop hook in cloud sessions) → `cloud/worker`
    (Cloudflare Worker + D1, user's own account, separate ingest/read tokens)
    → `sync.py` pulls into the store (`source = 'cloud'`). See `docs/cloud.md`.
    Alternative collector: `collector.py` (`python -m agentcost collector`),
    same API; `tests/test_collectors.py` runs one contract against both.
    Cloud capture sends metadata to the user's own collector — the only path
    where data leaves the machine.
- `clients.toml` — prices (placeholders!) and client rules. Single source of
  truth for rules; the planned web view stays read-only.
- `gen_sample.py` — synthetic logs, 2026-05..2026-10 with an Acme spike in August.
- `tests/` — pytest suite. `PLAN.md` — roadmap for the store + web view.

## Next steps
1. ✅ Verified against real Claude Code logs on Windows (2026-10): field names
   match; Windows paths are normalized to "/" for rules and project names.
   Finding: the first user works mostly in Claude Code cloud sessions → cloud
   capture added; still to verify in a real cloud environment (setup script
   + env vars reaching the Stop hook).
2. Client-facing monthly report (print view) — see `PLAN.md`.
3. Session splitting when one session touches several clients.
4. Human time + token cost → margin per client (fixed price vs T&M).
5. Client-facing monthly report (HTML/PDF), export for DATEV / sevDesk.
6. Support more agents (Codex CLI, Cursor, Copilot).
