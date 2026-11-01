# Getting started with agentcost

Step by step, from installation to your first per-client numbers. Commands are
shown for Windows PowerShell; on macOS/Linux use the same commands in a
terminal (with `/` paths).

**Where commands go:** everything starting with `python -m agentcost` is typed
in **PowerShell on your PC**, inside the agentcost folder. Only commands
starting with `python3 ~/.agentcost-hook/…` belong in a **chat with Claude in
a cloud session**.

## 1. Install (once)

You need Python 3.11 or newer and git.

```powershell
cd C:\Users\<you>\IdeaProjects
git clone https://github.com/andybuki/agentcost.git
cd agentcost
pip install tzdata
```

(`tzdata` gives Python the time zone names on Windows.) There is nothing else
to install: agentcost has no other dependencies.

Optional: `pip install -e .` lets you type `agentcost …` instead of
`python -m agentcost …`.

## 2. See what agentcost finds

```powershell
python -m agentcost doctor
```

`doctor` reads your Claude Code logs, copies the metadata into the local
database (`~/.agentcost/agentcost.db`) and lists:

- which log folders exist and how many log files they hold,
- every model you used, with turns, tokens and price,
- folders that are not assigned to a client yet.

No `clients.toml` yet? `doctor` says so — that's the next step.

## 3. Create your configuration

```powershell
python -m agentcost init
```

This writes `clients.toml` with **one client per project folder** found in
your data, most used first. Local and cloud folders of the same repository
become one client. Open the file and:

1. rename clients (`name = 'agentcost'` → `name = "Acme GmbH"`),
2. merge folders that belong to the same client into one `[[client]]`,
3. set `markup` (1.3 = bill cost × 1.3; 0 = internal, not billed),
4. delete what you don't need.

How rules work and how to organise folders: [clients.md](clients.md).
Your `clients.toml` is ignored by git, so `git pull` never overwrites it.
`clients.example.toml` shows a filled-in example.

Prices for Claude models are built in (`agentcost/prices.toml`). Add a
`[prices."model-id"]` block to `clients.toml` only for other models or to
override a price.

## 4. Open the web view

```powershell
python -m agentcost serve --open
```

Your browser opens `http://127.0.0.1:8765/`. The page only runs on your PC and
loads nothing from the internet. You see:

- totals for the period (cost, billable, margin, sessions) with the change vs.
  a comparison period,
- cost (or tokens) over time per client, project or model,
- a table per client — click a client to see its projects,
- **Sessions**: every session with time, project, branch, local/cloud, models
  and cost,
- model mix and work not assigned to a client yet (with a ready rule to copy).

Edit `clients.toml`, reload the page: all history is re-attributed. Stop the
server with Ctrl+C in PowerShell.

## 5. Monthly report per client

In the web view's client table, click **Report ↗** next to a client (or, after
clicking a client, **Monthly report** at the top). A printable page opens:

- **client version** (default): the amount to bill, by project, model, week and
  session — no cost, markup or margin;
- **internal version**: also cost, markup and margin.

Use **Print / save as PDF** to get a PDF for the client. ← / → switch months.

Or create HTML files for every client with activity in a month:

```powershell
python -m agentcost monthly --month 2026-10
python -m agentcost monthly --month 2026-10 --client "Acme GmbH" --internal
```

Files go to the `reports` folder; open one in a browser and print to PDF.
Optional: put your company name on the reports by adding to `clients.toml`:

```toml
[company]
name = "Bukhman IT-Beratung"
```

## 6. Optional: count cloud sessions

Sessions in Claude Code on the web / ☁ in the Claude app run on Anthropic's
servers; their logs never reach your PC. To include them, set up a small
collector once (~15 minutes): [cloud.md](cloud.md).

After that, `serve`, `report` and `doctor` fetch new cloud sessions
automatically; the page header shows "N turns from cloud sessions".

## 7. Everyday use

| Want to | Command |
|---|---|
| look at numbers | `python -m agentcost serve --open` (then **Refresh data** for new sessions) |
| table in the terminal | `python -m agentcost report --month 2026-10` |
| CSV for a month | `python -m agentcost report --month 2026-10 --csv oktober.csv` |
| reports for the client | **Report ↗** in the web view, or `python -m agentcost monthly --month 2026-10` |
| check what's found / missing | `python -m agentcost doctor` |
| new client folder appeared | add a `[[client]]` rule, or see `python -m agentcost init --print` for suggestions |
| update agentcost | `git pull` |

**Keep history:** Claude Code deletes its own logs after about 30 days. Every
agentcost command copies new entries into `~/.agentcost/agentcost.db` first,
and that database keeps them. Run any command at least every few weeks (or
schedule `python -m agentcost ingest`).

## 8. Troubleshooting

| Symptom | Fix |
|---|---|
| `doctor` is not recognised | Type the full command: `python -m agentcost doctor`. |
| `config file not found` | Run in the agentcost folder, or `python -m agentcost init`. |
| `problem in clients.toml: …` | The message names the client and field; fix that line. |
| Page shows nothing for this month | The page opens on the latest month with data; use the period buttons or **Show all data**. |
| A model shows "no price" | Add `[prices."model-id"]` to `clients.toml` (`doctor` prints a template). |
| Everything is `(unassigned)` | No rule matches: `python -m agentcost init`, or add rules ([clients.md](clients.md)). |
| Cloud sessions missing | See the checks in [cloud.md](cloud.md#5-check). |

## What agentcost reads — and what not

Only metadata: time, folder, git branch, model and token counts. Never prompt
text, answers or file contents. No AI calls. Everything stays on your PC,
except cloud-session metadata if you set up your own collector (step 6).
