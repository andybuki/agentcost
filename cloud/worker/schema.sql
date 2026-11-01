-- agentcost collector: metadata of Claude Code cloud sessions (no prompt text).
CREATE TABLE IF NOT EXISTS turns (
  seq         INTEGER PRIMARY KEY AUTOINCREMENT,  -- download cursor
  msg_id      TEXT NOT NULL,
  request_id  TEXT NOT NULL,
  ts          TEXT NOT NULL,
  session     TEXT,
  cwd         TEXT,
  branch      TEXT,
  model       TEXT,
  tok_in      INTEGER NOT NULL DEFAULT 0,
  tok_out     INTEGER NOT NULL DEFAULT 0,
  cache_write INTEGER NOT NULL DEFAULT 0,
  cache_read  INTEGER NOT NULL DEFAULT 0,
  cache_write_1h INTEGER NOT NULL DEFAULT 0,  -- share of cache_write with 1-hour TTL
  received_at TEXT NOT NULL,
  UNIQUE (msg_id, request_id)
);
