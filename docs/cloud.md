# Tracking Claude Code cloud sessions

Cloud sessions (the ☁ ones in the Claude app, or claude.ai/code) run in a
container on Anthropic's servers. Their logs never reach your PC, and the
container is deleted after the session, and it can't reach your PC. So each
cloud session drops its usage **metadata** into a small "mailbox" on the
internet — the **collector** — and `agentcost sync` picks it up on your PC.

```
cloud session ──(Stop hook, after each reply)──▶ collector (yours)
                                                        │
your PC: python -m agentcost sync  ◀────────────────────┘
```

You choose where the collector runs. Both speak the same protocol, so the hook
and `sync` work the same with either:

| | **A. Cloudflare Worker** | **B. Python collector on your own server** |
|---|---|---|
| Cost | free tier | a small VPS (e.g. Hetzner, ~4 €/month) |
| Data location | Cloudflare | wherever your server is (e.g. Germany) |
| You need | Node.js, Cloudflare account | a server with a public HTTPS address |
| Steps | section 2A | section 2B |

**What is sent:** time, folder (e.g. `/home/user/bankstm`), git branch,
session id, model, token counts. **Never** prompts, answers or code.

**Two tokens:** the *ingest* token (stored in your cloud environment) can only
add rows. The *read* token (only on your PC) can only read them.

Setup takes about 15 minutes, once.

## 1. Create two tokens

```powershell
python -m agentcost tokens
```

Keep both values (e.g. in your password manager): the **ingest token** goes to
the cloud environment, the **read token** stays on your PC.

## 2A. Deploy the collector to Cloudflare

You need Node.js (https://nodejs.org) and a free Cloudflare account
(https://dash.cloudflare.com/sign-up).

```powershell
cd C:\Users\<you>\IdeaProjects\agentcost\cloud\worker
npx wrangler login
npx wrangler d1 create agentcost
```

**If `wrangler login` fails** (e.g. `OAuth error: request_forbidden … No CSRF
value available in the session cookie`): the browser that opened didn't keep
Cloudflare's login cookie. Common on managed/company PCs or with strict cookie
settings. Two ways around it:

1. Log in at https://dash.cloudflare.com in your **default** browser first,
   then run `npx wrangler login` again. If another browser opens than the one
   you're logged in with, copy the long link wrangler prints into the right one.
2. Skip the browser login and use an API token instead (works always):
   - Dashboard → profile icon → **My Profile → API Tokens → Create Token** →
     template **"Edit Cloudflare Workers"** → under *Permissions* add
     **Account · D1 · Edit** → Continue → Create. Copy the token.
   - Your **Account ID** is on the dashboard's *Workers & Pages* overview (right side).
   - In the same PowerShell window:
     ```powershell
     $env:CLOUDFLARE_API_TOKEN = "<API token>"
     $env:CLOUDFLARE_ACCOUNT_ID = "<Account ID>"
     npx wrangler whoami
     ```
     `whoami` should show your account. Then continue with
     `npx wrangler d1 create agentcost` — no `wrangler login` needed.

A line like `Assertion failed: !(handle->flags & UV_HANDLE_CLOSING)` after an
error is a harmless crash of Node.js on Windows while exiting; ignore it.

The last command prints a `database_id`. Paste it into `wrangler.toml` in place
of `REPLACE-WITH-ID-FROM-wrangler-d1-create`. Then:

```powershell
npx wrangler d1 execute agentcost --remote --file=schema.sql
npx wrangler deploy
npx wrangler secret put INGEST_TOKEN
npx wrangler secret put READ_TOKEN
```

`wrangler secret put` asks for the value: paste the ingest and the read token.
`wrangler deploy` prints your collector's address, like
`https://agentcost.<your-subdomain>.workers.dev`. That is your **collector URL**.
Open it in a browser: it should show `{"service": "agentcost collector"}`.

## 2B. Or: run the Python collector on your own server

On a Linux server with Python 3.11+ and a domain (here `agentcost.example.de`):

```bash
git clone <your agentcost repository> && cd agentcost
export AGENTCOST_INGEST_TOKEN=<ingest token> AGENTCOST_READ_TOKEN=<read token>
python3 -m agentcost collector --port 8787 --db ~/agentcost-collector.db
```

It listens on `127.0.0.1:8787` and speaks plain HTTP, so put HTTPS in front —
otherwise tokens travel unencrypted. With [Caddy](https://caddyserver.com),
which gets the certificate automatically, the whole config is:

```
agentcost.example.de {
    reverse_proxy 127.0.0.1:8787
}
```

Your collector URL is then `https://agentcost.example.de`. To keep it running
after logout, start it as a systemd service (put the two tokens in the unit's
`Environment=` lines).

## 3. Connect your PC

```powershell
cd C:\Users\<you>\IdeaProjects\agentcost
python -m agentcost sync --url https://agentcost.<your-subdomain>.workers.dev --token <READ token>
```

It should answer `0 new cloud turns`. The settings are saved in
`~/.agentcost/cloud.json` (outside the repository). From now on `sync`, `serve`,
`report` and `doctor` download new cloud turns automatically.

## 4. Configure your cloud environment

In the Claude app, open a cloud session, then the **environment menu in the
session's title bar → Edit**. Do this for every environment you use.

1. **Network access:** add your collector's host
   (`agentcost.<your-subdomain>.workers.dev`) under *Allowed domains*. Leave
   *Allow package managers* ticked. Without this, cloud sessions can't reach the
   collector. Steps: https://code.claude.com/docs/en/cloud-environments#network-access
2. **Environment variables:**
   ```
   AGENTCOST_URL=https://agentcost.<your-subdomain>.workers.dev
   AGENTCOST_INGEST_TOKEN=<ingest token>
   ```
3. **Setup script:** copy the script to the clipboard and paste it into
   *Setup script* (add it below anything that is already there):
   ```powershell
   python -m agentcost cloud-setup | Set-Clipboard
   ```
   It installs the hook into `~/.claude/settings.json` of each new cloud
   session. It contains no tokens.

## 5. Check

Start a **new** cloud session (the setup script runs at session start), let
Claude answer once, then on your PC:

```powershell
python -m agentcost doctor
```

Under *Store* the number of **cloud** turns should go up. The web view shows
"N turns from cloud sessions" in the header.

## Assigning cloud work to clients

Cloud sessions run in `/home/user/<repository name>`, so client rules match
the repository name:

```toml
[[client]]
name = "Bank project"
cwd = ["/home/user/bankstm", "/home/user/BankingStatement"]
```

`python -m agentcost doctor` lists these folders under *Folders without a client*.

## Troubleshooting

| Symptom | Check |
|---|---|
| `sync` says "wrong read token" | `--token` must be the **read** token. Re-run step 3 with the right one. |
| `sync` says "not reachable" | Collector URL correct? Open it in a browser: it should show `{"service":"agentcost collector"}`. |
| No new cloud turns | Was the session started **after** step 4? Is the host allowed under Network access? Are the variable names exactly `AGENTCOST_URL` and `AGENTCOST_INGEST_TOKEN`? |
| Still nothing | In a cloud session, ask Claude to run `python3 ~/.agentcost-hook/agentcost_hook.py check`. It sends right away and prints what went wrong (missing variables, blocked host, wrong token). If the file doesn't exist, the setup script didn't run: check step 4.3. |

**Leaked token?** Create a new one (step 1), run `npx wrangler secret put`
again, and update the cloud environment variable or `sync --token`.

**Costs:** one person's usage should fit Cloudflare's free tier for Workers and
D1. Check Cloudflare's current limits if you track a whole team.

## Develop / test locally

The Worker runs locally with a stand-in for D1 (needs Node.js 22.5+):

```powershell
cd cloud\worker
node --test test/worker.test.mjs
$env:INGEST_TOKEN="a"; $env:READ_TOKEN="b"; node test/dev-server.mjs 8787
```

`tests/test_cloud.py` runs hook → Worker → sync end to end.
