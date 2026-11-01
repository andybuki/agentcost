# Security policy

## Reporting a vulnerability

Please **do not open a public issue** for security problems.
Use GitHub's private reporting: **Security → Report a vulnerability** on
<https://github.com/andybuki/agentcost/security/advisories/new>.

Include what you found, how to reproduce it, and the impact. You will get an
answer as soon as possible; this is a one-person project, so please allow a
few days.

## Scope

- The Python package (`agentcost/`), including the local web view
  (`127.0.0.1` only, read-only) and `collector.py`.
- The Cloudflare Worker (`cloud/worker`) and the cloud Stop hook.

## Design notes relevant to security

- agentcost reads **metadata only** (timestamps, folders, branches, models,
  token counts) — never prompts, answers or file contents.
- The only path where data leaves the machine is the optional cloud capture,
  which sends metadata to a collector the user deploys in their own account.
- Ingest and read tokens are separate secrets and must never be committed.

If you ever find a real token, key or customer name in this repository or its
history, report it the same way.
