#!/usr/bin/env python3
"""agentcost cloud hook — sends usage metadata of a Claude Code cloud session.

Runs as a Claude Code "Stop" hook inside a cloud session. It reads the new lines
of the session transcript, keeps only metadata (time, folder, branch, model,
token counts — never prompt text or code) and POSTs them to your own agentcost
collector (a Cloudflare Worker).

Standalone on purpose: `python -m agentcost cloud-setup` embeds this file in a
setup script, so the cloud container needs nothing but Python 3.

Configuration (environment variables of the cloud environment):
  AGENTCOST_URL           collector URL, e.g. https://agentcost.<you>.workers.dev
  AGENTCOST_INGEST_TOKEN  write-only token

It never fails the session: every error is swallowed and the unsent lines are
retried on the next Stop. To see what goes wrong, run it by hand:
  python3 ~/.agentcost-hook/agentcost_hook.py check
"""
import json, os, sys, urllib.request
from pathlib import Path

STATE = Path(os.environ.get("AGENTCOST_HOOK_STATE", "~/.agentcost-hook/state.json")).expanduser()
BATCH = 500
TIMEOUT = 5
# Cloudflare rejects Python's default "Python-urllib/x.y" agent (error 1010).
USER_AGENT = "agentcost-hook/1.0"

def metadata(line):
    """Same fields as agentcost.parse.parse_line — keep the two in sync."""
    try:
        e = json.loads(line)
    except ValueError:
        return None
    if not isinstance(e, dict) or e.get("type") != "assistant":
        return None
    msg = e.get("message") or {}
    usage = msg.get("usage")
    if not usage or msg.get("model") == "<synthetic>":
        return None
    return {
        "msg_id": msg.get("id"),
        "request_id": e.get("requestId"),
        "ts": e.get("timestamp", ""),
        "session": e.get("sessionId", ""),
        "cwd": e.get("cwd", ""),
        "branch": e.get("gitBranch", ""),
        "model": msg.get("model", "unknown"),
        "in": usage.get("input_tokens", 0) or 0,
        "out": usage.get("output_tokens", 0) or 0,
        "cw": usage.get("cache_creation_input_tokens", 0) or 0,
        "cr": usage.get("cache_read_input_tokens", 0) or 0,
    }

def new_turns(path, offset):
    """Turns from complete lines after byte `offset`; returns (turns, new_offset)."""
    turns = []
    with open(path, "rb") as fh:
        fh.seek(offset)
        pos = offset
        for raw in fh:
            if not raw.endswith(b"\n"):
                break  # still being written
            t = metadata(raw)
            if t:
                if not t["msg_id"]:
                    t["msg_id"] = f"{path}:{pos}"
                turns.append(t)
            pos += len(raw)
    return turns, pos

def post(url, token, turns):
    req = urllib.request.Request(
        url.rstrip("/") + "/ingest", data=json.dumps({"turns": turns}).encode(), method="POST",
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}",
                 "User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        if r.status != 200:
            raise OSError(f"collector answered {r.status}")

def load_state():
    try:
        return json.loads(STATE.read_text())
    except (OSError, ValueError):
        return {}

def save_state(state):
    STATE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state))
    tmp.replace(STATE)

def run(hook_input, url, token):
    path = hook_input.get("transcript_path")
    if not (url and token and path and os.path.isfile(path)):
        return 0
    state = load_state()
    offset = state.get(path, 0)
    if os.path.getsize(path) < offset:
        offset = 0  # transcript rewritten
    turns, new_offset = new_turns(path, offset)
    for i in range(0, len(turns), BATCH):
        post(url, token, turns[i:i + BATCH])
    state[path] = new_offset
    save_state(state)
    return len(turns)

def check():
    """Manual test inside a cloud session: send the newest transcript, print the outcome."""
    url, token = os.environ.get("AGENTCOST_URL"), os.environ.get("AGENTCOST_INGEST_TOKEN")
    if not (url and token):
        print("AGENTCOST_URL and AGENTCOST_INGEST_TOKEN are not set in this environment.")
        return 1
    files = sorted(Path("~/.claude/projects").expanduser().glob("*/*.jsonl"),
                   key=lambda p: p.stat().st_mtime)
    if not files:
        print("No Claude Code transcript found in ~/.claude/projects.")
        return 1
    try:
        n = run({"transcript_path": str(files[-1])}, url, token)
    except Exception as e:
        print(f"Sending to {url} failed: {e}")
        print("Is the host allowed under the environment's Network access? Is the token right?")
        return 1
    print(f"OK: {n} new turns sent to {url}")
    return 0

def main():
    if sys.argv[1:] == ["check"]:
        return check()
    try:
        hook_input = json.loads(sys.stdin.read() or "{}")
        run(hook_input, os.environ.get("AGENTCOST_URL"), os.environ.get("AGENTCOST_INGEST_TOKEN"))
    except Exception as e:  # never break the user's session
        if os.environ.get("AGENTCOST_HOOK_DEBUG"):
            print(f"agentcost hook: {e}", file=sys.stderr)
    return 0

if __name__ == "__main__":
    sys.exit(main())
