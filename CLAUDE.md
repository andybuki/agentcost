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
- `agentcost.py` — parses `~/.claude/projects/**/*.jsonl`, dedups streamed
  lines by (message.id, requestId), attributes by branch regex then cwd regex,
  prices via longest model-prefix match, applies markup, prints table / CSV.
- `clients.toml` — prices (placeholders!) and client rules.
- `gen_sample.py` — synthetic logs for testing.

## Next steps
1. Verify against real Claude Code logs; adjust field names if the format differs.
2. Unit tests (pytest) for attribution, pricing, dedup.
3. Session splitting when one session touches several clients.
4. Human time + token cost → margin per client (fixed price vs T&M).
5. Client-facing monthly report (HTML/PDF), export for DATEV / sevDesk.
6. Support more agents (Codex CLI, Cursor, Copilot).
