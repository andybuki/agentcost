"""agentcost collector in plain Python — the same API as cloud/worker.

Run it on any server that cloud sessions can reach (a small VPS, e.g. in the EU):

  AGENTCOST_INGEST_TOKEN=... AGENTCOST_READ_TOKEN=... \\
      python -m agentcost collector --host 0.0.0.0 --port 8787 --db collector.db

  POST /ingest          Authorization: Bearer INGEST_TOKEN   {"turns": [...]}
  GET  /turns?after=N   Authorization: Bearer READ_TOKEN     -> {"turns": [...], "last": M, "more": bool}

It speaks plain HTTP. On the internet put it behind HTTPS (e.g. Caddy or nginx
with Let's Encrypt), otherwise tokens travel unencrypted. See docs/cloud.md.
"""
import hmac, json, sqlite3, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

MAX_TURNS = 1000
MAX_BODY = 2_000_000
PAGE = 1000
STR_FIELDS = {"msg_id": 300, "request_id": 200, "ts": 40, "session": 100,
              "cwd": 1000, "branch": 300, "model": 100}
INT_FIELDS = ("in", "out", "cw", "cr", "cw1h")

# Same table as cloud/worker/schema.sql, so both collectors behave alike.
SCHEMA = """
CREATE TABLE IF NOT EXISTS turns (
  seq         INTEGER PRIMARY KEY AUTOINCREMENT,
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
  cache_write_1h INTEGER NOT NULL DEFAULT 0,
  received_at TEXT NOT NULL,
  UNIQUE (msg_id, request_id)
);
"""

def valid_turn(t):
    """A clean row with only known fields, or None if anything is off."""
    if not isinstance(t, dict):
        return None
    row = {}
    for k, max_len in STR_FIELDS.items():
        v = t.get(k)
        v = "" if v is None else v
        if not isinstance(v, str) or len(v) > max_len:
            return None
        row[k] = v
    if not row["msg_id"] or not row["ts"]:
        return None
    for k in INT_FIELDS:
        v = t.get(k)
        v = 0 if v is None else v
        if not isinstance(v, int) or isinstance(v, bool) or v < 0 or v > 2**53:
            return None
        row[k] = v
    return row

class Collector:
    def __init__(self, db_path, ingest_token, read_token):
        if not (ingest_token and ingest_token.strip() and read_token and read_token.strip()):
            raise ValueError("both an ingest and a read token are required")
        if ingest_token == read_token:
            raise ValueError("ingest and read token must differ")
        self.ingest_token, self.read_token = ingest_token.strip(), read_token.strip()
        Path(db_path).expanduser().parent.mkdir(parents=True, exist_ok=True)
        self.con = sqlite3.connect(Path(db_path).expanduser(), check_same_thread=False)
        self.con.executescript(SCHEMA)
        if "cache_write_1h" not in {r[1] for r in self.con.execute("PRAGMA table_info(turns)")}:
            self.con.execute("ALTER TABLE turns ADD COLUMN cache_write_1h INTEGER NOT NULL DEFAULT 0")
        self.lock = threading.Lock()

    def ingest(self, turns):
        rows = [valid_turn(t) for t in turns]
        if any(r is None for r in rows):
            return None
        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        with self.lock:
            before = self.con.execute("SELECT COUNT(*) FROM turns").fetchone()[0]
            # A resent turn only fills in a missing 1-hour cache share.
            self.con.executemany(
                """INSERT INTO turns (msg_id, request_id, ts, session, cwd, branch, model,
                     tok_in, tok_out, cache_write, cache_read, cache_write_1h, received_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT (msg_id, request_id) DO UPDATE SET cache_write_1h = excluded.cache_write_1h
                   WHERE excluded.cache_write_1h > turns.cache_write_1h""",
                [(r["msg_id"], r["request_id"], r["ts"], r["session"], r["cwd"], r["branch"],
                  r["model"], r["in"], r["out"], r["cw"], r["cr"], r["cw1h"], now) for r in rows])
            self.con.commit()
            return self.con.execute("SELECT COUNT(*) FROM turns").fetchone()[0] - before

    def turns_after(self, after):
        keys = ("seq", "msg_id", "request_id", "ts", "session", "cwd", "branch", "model",
                "in", "out", "cw", "cr", "cw1h")
        with self.lock:
            rows = self.con.execute(
                """SELECT seq, msg_id, request_id, ts, session, cwd, branch, model,
                          tok_in, tok_out, cache_write, cache_read, cache_write_1h
                   FROM turns WHERE seq > ? ORDER BY seq LIMIT ?""", (after, PAGE)).fetchall()
        turns = [dict(zip(keys, r)) for r in rows]
        return {"turns": turns, "last": turns[-1]["seq"] if turns else after,
                "more": len(turns) == PAGE}

def make_handler(collector):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def _json(self, obj, status=200):
            body = json.dumps(obj).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _authorized(self, expected):
            got = (self.headers.get("Authorization") or "").removeprefix("Bearer ").strip()
            # Constant-time comparison, so timing reveals nothing about the token.
            return hmac.compare_digest(got.encode(), expected.encode())

        def do_POST(self):
            if urlparse(self.path).path != "/ingest":
                return self._json({"error": "not found"}, 404)
            if not self._authorized(collector.ingest_token):
                return self._json({"error": "unauthorized"}, 401)
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                return self._json({"error": "bad length"}, 400)
            if length > MAX_BODY:
                return self._json({"error": "body too large"}, 413)
            try:
                body = json.loads(self.rfile.read(length))
            except ValueError:
                return self._json({"error": "invalid JSON"}, 400)
            turns = body.get("turns") if isinstance(body, dict) else None
            if not isinstance(turns, list) or len(turns) > MAX_TURNS:
                return self._json({"error": f"'turns' must be an array of at most {MAX_TURNS}"}, 400)
            added = collector.ingest(turns)
            if added is None:
                return self._json({"error": "invalid turn"}, 400)
            self._json({"received": len(turns), "added": added})

        def do_GET(self):
            u = urlparse(self.path)
            if u.path == "/":
                return self._json({"service": "agentcost collector"})
            if u.path != "/turns":
                return self._json({"error": "not found"}, 404)
            if not self._authorized(collector.read_token):
                return self._json({"error": "unauthorized"}, 401)
            try:
                after = int(parse_qs(u.query).get("after", ["0"])[-1])
                if after < 0:
                    raise ValueError
            except ValueError:
                return self._json({"error": "'after' must be a number"}, 400)
            self._json(collector.turns_after(after))

    return Handler

def serve_collector(db, host, port, ingest_token, read_token):
    collector = Collector(db, ingest_token, read_token)
    httpd = ThreadingHTTPServer((host, port), make_handler(collector))
    print(f"agentcost collector on http://{host}:{port}/  (store: {db}; Ctrl+C to stop)")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
