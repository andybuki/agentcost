"""Download cloud-session turns from your agentcost collector into the local store.

The collector (cloud/worker) receives metadata from Claude Code cloud sessions
via agentcost/cloud_hook.py. Connection settings live in ~/.agentcost/cloud.json,
outside the repository, because the read token is a secret.
"""
import json, os, urllib.error, urllib.request
from pathlib import Path

from .store import insert_turns

CLOUD_CONFIG = "~/.agentcost/cloud.json"
TIMEOUT = 20
# Cloudflare rejects Python's default "Python-urllib/x.y" agent (error 1010).
USER_AGENT = "agentcost-sync/1.0"

class SyncError(Exception):
    pass

def load_cloud_config(path=CLOUD_CONFIG):
    p = Path(path).expanduser()
    if not p.is_file():
        return None
    cfg = json.loads(p.read_text())
    return cfg if cfg.get("url") and cfg.get("read_token") else None

def save_cloud_config(url, read_token, path=CLOUD_CONFIG):
    p = Path(path).expanduser()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"url": url.rstrip("/"), "read_token": read_token}, indent=2))
    try:
        os.chmod(p, 0o600)  # the token is a secret; no effect on Windows
    except OSError:
        pass

def _get(url, token):
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}",
                                               "User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        hint = " (wrong read token?)" if e.code == 401 else ""
        raise SyncError(f"collector answered {e.code}{hint}") from None
    except (urllib.error.URLError, OSError, ValueError) as e:
        raise SyncError(f"collector not reachable: {getattr(e, 'reason', e)}") from None

def sync(con, url, token):
    """Fetch all turns after the last downloaded one; returns the number of new turns."""
    url = url.rstrip("/")
    row = con.execute("SELECT last_seq FROM sync_state WHERE url = ?", (url,)).fetchone()
    last = row[0] if row else 0
    added = 0
    while True:
        page = _get(f"{url}/turns?after={last}", token)
        turns = page.get("turns") or []
        added += insert_turns(con, [(t, f"cloud:{t.get('session', '')}", "cloud") for t in turns])
        last = page.get("last", last)
        con.execute("INSERT OR REPLACE INTO sync_state VALUES (?, ?)", (url, last))
        con.commit()
        if not page.get("more"):
            return added

def sync_if_configured(con, config_path=CLOUD_CONFIG):
    """Sync when cloud.json exists. Returns (added, error message or None)."""
    cfg = load_cloud_config(config_path)
    if not cfg:
        return 0, None
    try:
        return sync(con, cfg["url"], cfg["read_token"]), None
    except SyncError as e:
        return 0, str(e)
