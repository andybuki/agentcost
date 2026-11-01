# How agentcost splits work into clients

agentcost answers "what did agent work cost per client?". It never reads your
prompts or code. It only knows **where** and **when** Claude Code worked, so
the way you organise your folders decides how well it can split costs.

## 1. What agentcost can see

agentcost reads the session logs that Claude Code writes **on your own
computer** (`~/.claude/projects`, on Windows `C:\Users\<you>\.claude\projects`).

| Where you work with Claude | Logs on your PC? | Counted? |
|---|---|---|
| Claude Code in a terminal (`claude`) | yes | ✅ |
| Claude Code in VS Code / JetBrains | yes | ✅ |
| Claude desktop app, **Code** tab, on a local folder | should be | ✅ check with `doctor` (see below) |
| Claude Code on the web (claude.ai/code), cloud sessions | no, they run in a cloud container | ✅ after a one-time setup: [cloud.md](cloud.md) |
| Chat in the Claude desktop app or claude.ai | no, chats are stored by Anthropic | ❌ |
| ChatGPT, DeepSeek, other tools | no | ❌ (planned: manual cost entries) |

**Check what is seen:** open PowerShell (or a terminal), go to the agentcost
folder, run the command below, do a short Claude Code session, run it again.
If the turn count went up, that way of working is tracked.

```powershell
cd C:\Users\<you>\IdeaProjects\agentcost
python -m agentcost doctor
```

## 2. How a turn is assigned to a client

Every agent turn in the log carries two facts agentcost uses:

- **the folder** Claude Code ran in (e.g. `C:\Users\me\clients\acme\shop`)
- **the git branch** (e.g. `main`, `acme/invoice-export`)

Rules in `clients.toml` match these with patterns:

```toml
[[client]]
name = "Acme GmbH"
cwd = ["/clients/acme/"]     # folder contains this text
branch = ["^acme/"]          # or: branch starts with "acme/"
markup = 1.3                 # bill cost × 1.3
```

The order of checks:

1. **Branch rules first.** If the branch matches any client's `branch` pattern,
   that client wins. This allows splitting one shared repository.
2. **Then folder rules.** The first client (top to bottom in the file) whose
   `cwd` pattern matches wins.
3. **Nothing matched** → `(unassigned)`. Not billed (markup 0), but shown, so
   no cost gets lost.

Rules apply to **all history**, not just new work. Fix a rule, reload the page,
and past months are re-attributed.

### Monthly billing terms (optional)

`markup` bills usage: cost × markup. For retainers, minimums or budgets add
terms per client. They apply to a whole calendar month and are settled in the
**monthly report** (`monthly` command, `/report`), amounts in your currency:

```toml
[[client]]
name = "Acme GmbH"
markup = 1.3
min_fee = 50      # invoice at least 50 per month
cap = 400         # never more than 400 per month

[[client]]
name = "Stadtwerke Nord"
fixed_fee = 250   # flat fee; usage is shown but not billed on top
```

- `min_fee` / `cap` can be combined (`min_fee` ≤ `cap`); `fixed_fee` stands alone.
- The report shows *usage value*, the adjustment (with its reason) and the
  total. The client version never shows your cost.
- Clients with `fixed_fee` or `min_fee` get a report even in months without usage.
- The web dashboard keeps showing usage-based billable (cost × markup), since
  terms only make sense per month.

## 3. Recommended setup: one folder per client

You don't need to create anything inside Claude Code: no "projects" to set
up, no tags. **The folder is the project.** Give every client a parent folder:

```
C:\Users\me\clients\
├── acme\            ← Acme GmbH
│   ├── shop\
│   └── api\
├── stadtwerke-nord\ ← Stadtwerke Nord
│   └── dashboard\
└── internal\        ← your own tools, not billed
```

One rule per client then covers all of its repositories:

```toml
[[client]]
name = "Acme GmbH"
cwd = ["/clients/acme/"]
markup = 1.3

[[client]]
name = "Stadtwerke Nord"
cwd = ["/clients/stadtwerke-nord/"]
markup = 1.2

[[client]]
name = "Internal"
cwd = ["/clients/internal/"]
markup = 0
```

Then always **start Claude Code inside the client's folder** (`cd` there first,
or open that folder in VS Code / the desktop app).

If moving folders is not possible, list the existing ones instead:

```toml
[[client]]
name = "Acme GmbH"
cwd = ["/IdeaProjects/acme-shop", "/IdeaProjects/acme-api"]
```

## 4. One repository for several clients

If a shared repository (e.g. a monorepo or a product you customise per
client) can't be split by folder, use **branch names** with a client prefix:

```
acme/invoice-export
swn/login-fix
```

```toml
[[client]]
name = "Acme GmbH"
branch = ["^acme/"]
```

Work on `main` in that repository falls back to the folder rules.

## 5. Pattern tips

- Patterns are [regular expressions](https://docs.python.org/3/library/re.html),
  matched anywhere in the path. Plain text like `/clients/acme/` just works.
- **Windows:** always write `/`, even for Windows paths. agentcost converts
  `C:\Users\me\clients\acme` to `C:/Users/me/clients/acme` before matching.
- Matching is case-sensitive. To ignore case, start the pattern with `(?i)`:
  `cwd = ["(?i)/clients/acme/"]`.
- Put `'...'` (single quotes) around patterns with backslashes or special
  characters. TOML then takes them literally.
- `.` in a regex means "any character". For an exact dot write `\.` inside
  single quotes: `'/acme\.de/'`.

## 6. Workflow

1. Organise folders (section 3) or agree on branch prefixes (section 4).
   Quick start: `python -m agentcost init` writes a `clients.toml` with one
   client per folder found in your data; then rename and merge.
2. Run `python -m agentcost doctor`. The section
   **"Folders without a client"** lists every folder that needs a rule.
3. Add `[[client]]` rules to `clients.toml`. Delete the example clients
   (Acme, Stadtwerke Nord, Internal) if you don't need them.
4. Reload the web view (`python -m agentcost serve --open`).
   The **Unassigned work** box shows what is still left, with a ready-made rule
   to copy.

## 7. Limits today

- **One session, several clients:** the whole session counts for the folder it
  runs in. If you switch clients, start a new Claude Code session in the
  other client's folder.
- **Costs are API-equivalent:** on a Pro/Max subscription you don't pay per
  token. agentcost shows what the same work would cost on the API, which is a
  fair basis for billing. Configure `[subscription]` to also see your flat fee.
- **Keep history:** Claude Code deletes its logs after about 30 days. Running
  agentcost (`report`, `serve` or `ingest`) at least every few weeks keeps the
  data in its own store.
