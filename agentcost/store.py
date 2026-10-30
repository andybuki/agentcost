"""Local SQLite store, so history survives Claude Code's log cleanup.

Only raw token counts are stored. Client, project and cost are derived at
query time from clients.toml, so a fixed rule or price re-attributes history.
"""
import sqlite3, time
from pathlib import Path

from .parse import log_files, read_file

DEFAULT_DB = "~/.agentcost/agentcost.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS turns (
    msg_id      TEXT NOT NULL,
    request_id  TEXT NOT NULL,
    ts          TEXT NOT NULL,   -- UTC, ISO 8601 as written by the agent
    session     TEXT,
    cwd         TEXT,
    branch      TEXT,
    model       TEXT,
    tok_in      INTEGER NOT NULL DEFAULT 0,
    tok_out     INTEGER NOT NULL DEFAULT 0,
    cache_write INTEGER NOT NULL DEFAULT 0,
    cache_read  INTEGER NOT NULL DEFAULT 0,
    source_file TEXT,
    ingested_at TEXT,
    PRIMARY KEY (msg_id, request_id)
);
CREATE INDEX IF NOT EXISTS turns_ts ON turns(ts);
CREATE TABLE IF NOT EXISTS files (
    path   TEXT PRIMARY KEY,
    size   INTEGER,
    mtime  REAL,
    offset INTEGER
);
"""

def connect(db_path=DEFAULT_DB):
    p = Path(db_path).expanduser()
    p.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(p)
    con.executescript(SCHEMA)
    return con

def ingest(con, log_dir):
    """Read new lines from all log files. Idempotent; returns number of new turns."""
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    known = {r[0]: r[1:] for r in con.execute("SELECT path, size, mtime, offset FROM files")}
    added = 0
    for f in log_files(log_dir):
        st = f.stat()
        path = str(f)
        size, mtime, offset = known.get(path, (None, None, 0))
        if size == st.st_size and mtime == st.st_mtime:
            continue
        if st.st_size < offset:  # rewritten or truncated: start over
            offset = 0
        turns, new_offset = read_file(f, offset)
        before = con.total_changes
        con.executemany(
            "INSERT OR IGNORE INTO turns VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [(t["msg_id"] or f"{path}:{t['pos']}", t["request_id"] or "", t["ts"],
              t["session"], t["cwd"], t["branch"], t["model"],
              t["in"], t["out"], t["cw"], t["cr"], path, now) for t in turns])
        added += con.total_changes - before
        con.execute("INSERT OR REPLACE INTO files VALUES (?,?,?,?)",
                    (path, st.st_size, st.st_mtime, new_offset))
    con.commit()
    return added

def load_turns(con):
    """All stored turns as dicts in the same shape parse.iter_turns yields."""
    cur = con.execute("""SELECT msg_id, request_id, ts, session, cwd, branch, model,
                                tok_in, tok_out, cache_write, cache_read
                         FROM turns ORDER BY ts""")
    keys = ("msg_id", "request_id", "ts", "session", "cwd", "branch", "model",
            "in", "out", "cw", "cr")
    return [dict(zip(keys, r)) for r in cur]
