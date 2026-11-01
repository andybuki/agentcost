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
    cache_write_1h INTEGER NOT NULL DEFAULT 0,  -- share of cache_write with 1-hour TTL
    source_file TEXT,
    ingested_at TEXT,
    source      TEXT NOT NULL DEFAULT 'local',  -- 'local' log file or 'cloud' collector
    PRIMARY KEY (msg_id, request_id)
);
CREATE INDEX IF NOT EXISTS turns_ts ON turns(ts);
CREATE TABLE IF NOT EXISTS files (
    path   TEXT PRIMARY KEY,
    size   INTEGER,
    mtime  REAL,
    offset INTEGER
);
CREATE TABLE IF NOT EXISTS sync_state (
    url      TEXT PRIMARY KEY,
    last_seq INTEGER NOT NULL DEFAULT 0
);
"""

# Existing rows only get a missing 1-hour cache share filled in (backfill after
# the column was added); everything else about a stored turn never changes.
INSERT_TURN = """INSERT INTO turns
    (msg_id, request_id, ts, session, cwd, branch, model,
     tok_in, tok_out, cache_write, cache_read, cache_write_1h, source_file, ingested_at, source)
    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    ON CONFLICT (msg_id, request_id) DO UPDATE SET cache_write_1h = excluded.cache_write_1h
    WHERE excluded.cache_write_1h > turns.cache_write_1h"""

def connect(db_path=DEFAULT_DB):
    p = Path(db_path).expanduser()
    p.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(p)
    con.executescript(SCHEMA)
    columns = {r[1] for r in con.execute("PRAGMA table_info(turns)")}
    # Stores created before cloud sync have no source column.
    if "source" not in columns:
        con.execute("ALTER TABLE turns ADD COLUMN source TEXT NOT NULL DEFAULT 'local'")
    # Stores created before 1-hour cache pricing: add the column and re-read all
    # log files once, so the share is filled in for history still on disk.
    if "cache_write_1h" not in columns:
        con.execute("ALTER TABLE turns ADD COLUMN cache_write_1h INTEGER NOT NULL DEFAULT 0")
        con.execute("DELETE FROM files")
        con.commit()
    return con

def now_utc():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

def insert_turns(con, rows):
    """Insert (turn, source_file, source) tuples; returns the number of new rows."""
    now = now_utc()
    before = con.execute("SELECT COUNT(*) FROM turns").fetchone()[0]
    con.executemany(INSERT_TURN, [
        (t["msg_id"], t["request_id"] or "", t["ts"], t["session"], t["cwd"], t["branch"],
         t["model"], t["in"], t["out"], t["cw"], t["cr"], t.get("cw1h", 0) or 0,
         src_file, now, source)
        for t, src_file, source in rows])
    return con.execute("SELECT COUNT(*) FROM turns").fetchone()[0] - before

def ingest(con, log_dirs):
    """Read new lines from all log files. Idempotent; returns number of new turns."""
    known = {r[0]: r[1:] for r in con.execute("SELECT path, size, mtime, offset FROM files")}
    added = 0
    for f in log_files(log_dirs):
        st = f.stat()
        path = str(f)
        size, mtime, offset = known.get(path, (None, None, 0))
        if size == st.st_size and mtime == st.st_mtime:
            continue
        if st.st_size < offset:  # rewritten or truncated: start over
            offset = 0
        turns, new_offset = read_file(f, offset)
        for t in turns:
            t["msg_id"] = t["msg_id"] or f"{path}:{t['pos']}"
        added += insert_turns(con, [(t, path, "local") for t in turns])
        con.execute("INSERT OR REPLACE INTO files VALUES (?,?,?,?)",
                    (path, st.st_size, st.st_mtime, new_offset))
    con.commit()
    return added

def load_turns(con):
    """All stored turns as dicts in the same shape parse.iter_turns yields."""
    cur = con.execute("""SELECT msg_id, request_id, ts, session, cwd, branch, model,
                                tok_in, tok_out, cache_write, cache_read, cache_write_1h, source
                         FROM turns ORDER BY ts""")
    keys = ("msg_id", "request_id", "ts", "session", "cwd", "branch", "model",
            "in", "out", "cw", "cr", "cw1h", "source")
    return [dict(zip(keys, r)) for r in cur]
