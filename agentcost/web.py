"""Local web view: `python -m agentcost serve`.

Stdlib HTTP server bound to 127.0.0.1. The page makes no external requests.
The view is read-only — clients.toml stays the only place rules are edited;
it is re-read whenever it changes, so edits show up on the next reload.
"""
import csv, io, json, threading
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import analytics as A
from .parse import log_files
from .rules import UNASSIGNED, ConfigError, load_config
from .store import connect, ingest, load_turns
from .sync import CLOUD_CONFIG, load_cloud_config, sync_if_configured

STATIC = Path(__file__).parent / "static"
CONTENT_TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
                 ".css": "text/css; charset=utf-8", ".svg": "image/svg+xml"}

class BadRequest(Exception):
    pass

class App:
    def __init__(self, config, db, logs, cloud_config=CLOUD_CONFIG):
        self.config, self.db, self.cloud_config = Path(config), db, cloud_config
        self.sync_error = None
        self.logs = [logs] if isinstance(logs, (str, Path)) else list(logs)
        self.lock = threading.Lock()
        self._key = None
        self.data_version = 0
        self.refresh()

    def refresh(self):
        """Ingest new log lines and cloud turns; returns the number of new turns."""
        with self.lock:
            con = connect(self.db)
            try:
                added = ingest(con, self.logs)
                cloud, self.sync_error = sync_if_configured(con, self.cloud_config)
                added += cloud
                self.turns = load_turns(con)
            finally:
                con.close()
            self.log_status = [{"path": str(Path(d).expanduser()), "found": Path(d).expanduser().is_dir(),
                                "files": len(log_files(d))} for d in self.logs]
            self.data_version += 1
            return added

    def data(self):
        """(cfg, records, unpriced, orders), rebuilt when config or data changed."""
        with self.lock:
            key = (self.config.stat().st_mtime, self.data_version)
            if key != self._key:
                cfg, rules = load_config(self.config)
                recs, unpriced = A.enrich(self.turns, cfg, rules)
                # Stable order per dimension (all-time cost) so colors follow
                # the entity, not its rank in the selected period.
                orders = {by: [r["name"] for r in A.breakdown(recs, by)] for by in A.DIMENSIONS}
                rule_order = [r["name"] for r in rules]
                orders["client"] = ([n for n in rule_order if n in orders["client"]]
                                    + [n for n in orders["client"] if n not in rule_order])
                self._cache = (cfg, recs, unpriced, orders)
                self._key = key
            return self._cache

    def meta(self):
        cfg, recs, unpriced, orders = self.data()
        return {
            "currency": cfg.get("currency_symbol", "USD"),
            "timezone": cfg.get("timezone", "UTC"),
            "first_day": recs[0]["day"].isoformat() if recs else None,
            "last_day": recs[-1]["day"].isoformat() if recs else None,
            "today": date.today().isoformat(),
            "orders": orders,
            "subscription": cfg.get("subscription"),
            "unpriced": sorted(unpriced),
            # Example rules that match nothing count as "no clients yet".
            "has_clients": any(n != UNASSIGNED for n in orders["client"]),
            "turns_stored": len(self.turns),
            "log_dirs": self.log_status,
            "cloud": {"configured": load_cloud_config(self.cloud_config) is not None,
                      "turns": sum(1 for t in self.turns if t["source"] == "cloud"),
                      "error": self.sync_error},
        }

    def report(self, q):
        cfg, recs, unpriced, orders = self.data()
        start, end = _date(q, "from"), _date(q, "to")
        if end < start:
            raise BadRequest("'to' is before 'from'")
        by = q.get("by", "client")
        if by not in A.DIMENSIONS:
            raise BadRequest(f"'by' must be one of {', '.join(A.DIMENSIONS)}")
        bucket = q.get("bucket", "auto")
        if bucket == "auto":
            bucket = A.auto_bucket(start, end)
        elif bucket not in ("day", "week", "month"):
            raise BadRequest("'bucket' must be day, week, month or auto")
        cmp = q.get("cmp", "prev")
        if cmp == "prev":
            cstart, cend = A.previous_period(start, end)
        elif cmp == "yoy":
            cstart, cend = A.year_before(start, end)
        elif cmp == "custom":
            cstart, cend = _date(q, "cmp_from"), _date(q, "cmp_to")
        elif cmp == "none":
            cstart = cend = None
        else:
            raise BadRequest("'cmp' must be prev, yoy, custom or none")
        client = q.get("client") or None
        metric = q.get("metric", "cost")
        if metric not in A.METRICS:
            raise BadRequest("'metric' must be cost or tokens")

        cur = A.select(recs, start, end, client)
        prev = A.select(recs, cstart, cend, client) if cstart else []
        # The flat fee covers all clients, so it is only shown for the full view.
        fee = None if client else A.subscription_fee(cfg, start, end)
        cmp_fee = A.subscription_fee(cfg, cstart, cend) if cstart and fee is not None else None
        ts = A.timeseries(cur, start, end, bucket, by, orders[by], metric=metric)
        if cstart:
            # Comparison drawn bucket-by-bucket against the current period.
            ts["cmp_values"] = A.bucket_totals(prev, cstart, cend, bucket, metric)
            ts["cmp_buckets"] = A.buckets(cstart, cend, bucket)
        return {
            "range": [start.isoformat(), end.isoformat()],
            "cmp_range": [cstart.isoformat(), cend.isoformat()] if cstart else None,
            "bucket": bucket, "by": by, "client": client, "metric": metric,
            "currency": cfg.get("currency_symbol", "USD"),
            "summary": A.summary(cur),
            "cmp_summary": A.summary(prev) if cstart else None,
            "subscription": None if fee is None else {
                "name": cfg["subscription"].get("name", "Subscription"),
                "fee": fee, "cmp_fee": cmp_fee},
            "timeseries": ts,
            "table": A.compare(cur, prev, by),
            "model_mix": A.breakdown(cur, "model"),
            "unassigned": [] if client else A.unassigned(cur),
            "sessions": A.sessions(cur),
            "unpriced": sorted(unpriced),
        }

    def export_csv(self, q):
        r = self.report(q)
        cols = ["name", "cost", "cmp_cost", "delta", "delta_pct", "billable",
                "margin", "sessions", "tokens", "share"]
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow([r["by"]] + cols[1:])
        for row in r["table"]:
            w.writerow([row["name"]] + [_csv_num(row[c]) for c in cols[1:]])
        return buf.getvalue()

def _date(q, name):
    try:
        return date.fromisoformat(q[name])
    except KeyError:
        raise BadRequest(f"missing '{name}'")
    except ValueError:
        raise BadRequest(f"'{name}' must be YYYY-MM-DD")

def _csv_num(v):
    return "" if v is None else round(v, 4) if isinstance(v, float) else v

def make_handler(app, port):
    allowed_hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def _send(self, status, body, ctype="application/json", extra=None):
            data = body.encode() if isinstance(body, str) else body
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Security-Policy",
                             "default-src 'self'; style-src 'self' 'unsafe-inline'")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(data)

        def _json(self, obj, status=200):
            self._send(status, json.dumps(obj, default=str))

        def _guard(self):
            # Reject DNS-rebinding requests: only our own host names may talk to us.
            if self.headers.get("Host") not in allowed_hosts:
                self._json({"error": "forbidden host"}, 403)
                return False
            return True

        def do_GET(self):
            if not self._guard():
                return
            u = urlparse(self.path)
            q = {k: v[-1] for k, v in parse_qs(u.query).items()}
            try:
                if u.path == "/api/meta":
                    return self._json(app.meta())
                if u.path == "/api/report":
                    return self._json(app.report(q))
                if u.path == "/api/export.csv":
                    name = f"agentcost_{q.get('from', '')}_{q.get('to', '')}.csv"
                    return self._send(200, app.export_csv(q), "text/csv; charset=utf-8",
                                      {"Content-Disposition": f'attachment; filename="{name}"'})
            except ConfigError as e:
                return self._json({"error": f"clients.toml: {e}"}, 400)
            except BadRequest as e:
                return self._json({"error": str(e)}, 400)
            path = "index.html" if u.path == "/" else u.path.lstrip("/")
            f = (STATIC / path).resolve()
            if f.parent != STATIC.resolve() or not f.is_file():
                return self._json({"error": "not found"}, 404)
            self._send(200, f.read_bytes(), CONTENT_TYPES.get(f.suffix, "application/octet-stream"))

        def do_POST(self):
            if not self._guard():
                return
            if urlparse(self.path).path == "/api/refresh":
                return self._json({"added": app.refresh()})
            self._json({"error": "not found"}, 404)

    return Handler

def serve(config, db, logs, port=8765, open_browser=False, cloud_config=CLOUD_CONFIG):
    app = App(config, db, logs, cloud_config)
    httpd = ThreadingHTTPServer(("127.0.0.1", port), make_handler(app, port))
    url = f"http://127.0.0.1:{port}/"
    print(f"agentcost web view on {url}  (Ctrl+C to stop)")
    if open_browser:
        import webbrowser
        webbrowser.open(url)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
