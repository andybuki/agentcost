import json, shutil, threading, urllib.error, urllib.request
from http.server import ThreadingHTTPServer
import pytest
from agentcost.web import App, make_handler
from conftest import make_entry

@pytest.fixture
def server(tmp_path, config_path, write_log):
    logs = write_log([make_entry(msg_id="a", ts="2026-09-10T10:00:00Z"),
                      make_entry(msg_id="b", ts="2026-10-10T10:00:00Z"),
                      make_entry(msg_id="c", ts="2026-10-11T10:00:00Z", cwd="/oss/x")])
    app = App(config_path, tmp_path / "t.db", logs, tmp_path / "cloud.json")
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), None)
    port = httpd.server_address[1]
    httpd.RequestHandlerClass = make_handler(app, port)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{port}", app, logs
    httpd.shutdown()

def get(url, headers=None):
    with urllib.request.urlopen(urllib.request.Request(url, headers=headers or {})) as r:
        body = r.read().decode()
        return r.status, r.headers, body

def test_report_endpoint(server):
    base, _, _ = server
    _, _, body = get(f"{base}/api/report?from=2026-10-01&to=2026-10-31&cmp=prev&by=client")
    r = json.loads(body)
    assert r["cmp_range"] == ["2026-09-01", "2026-09-30"]
    assert r["bucket"] == "day"
    assert r["summary"]["cost"] == 3.0 and r["cmp_summary"]["cost"] == 1.5
    rows = {x["name"]: x for x in r["table"]}
    assert rows["Acme"]["delta_pct"] == 0.0
    assert r["unassigned"][0]["cwd"] == "/oss/x"
    assert len(r["timeseries"]["buckets"]) == 31

def test_bad_request(server):
    base, _, _ = server
    with pytest.raises(urllib.error.HTTPError) as e:
        get(f"{base}/api/report?from=2026-10-31&to=2026-10-01")
    assert e.value.code == 400

def test_foreign_host_rejected(server):
    base, _, _ = server
    with pytest.raises(urllib.error.HTTPError) as e:
        get(f"{base}/api/meta", {"Host": "evil.example:80"})
    assert e.value.code == 403

def test_static_and_traversal(server):
    base, _, _ = server
    status, headers, body = get(f"{base}/")
    assert status == 200 and "<html" in body
    assert "default-src 'self'" in headers["Content-Security-Policy"]
    with pytest.raises(urllib.error.HTTPError) as e:
        get(f"{base}/../web.py")
    assert e.value.code == 404

def test_csv_export(server):
    base, _, _ = server
    _, headers, body = get(f"{base}/api/export.csv?from=2026-10-01&to=2026-10-31")
    assert "attachment" in headers["Content-Disposition"]
    assert body.splitlines()[0].startswith("client,cost,cmp_cost")

def test_refresh_and_config_reload(server, config_path, write_log):
    import os, time
    base, app, _ = server
    write_log([make_entry(msg_id="d", ts="2026-10-12T10:00:00Z")])
    req = urllib.request.Request(f"{base}/api/refresh", method="POST", data=b"")
    assert json.loads(urllib.request.urlopen(req).read())["added"] == 1
    # Edit clients.toml: /oss/ becomes a client without restarting the server
    config_path.write_text(config_path.read_text() + '\n[[client]]\nname = "OSS"\ncwd = ["^/oss/"]\n')
    os.utime(config_path, (time.time() + 5, time.time() + 5))
    meta = json.loads(get(f"{base}/api/meta")[2])
    assert "OSS" in meta["orders"]["client"]

def test_meta_reports_clients_and_log_dirs(server):
    base, _, logs = server
    meta = json.loads(get(f"{base}/api/meta")[2])
    assert meta["has_clients"] is True
    assert meta["turns_stored"] == 3
    assert meta["log_dirs"] == [{"path": str(logs), "found": True, "files": 1}]
    (d,) = meta["devices"]
    assert d["turns"] == 3 and d["last_day"] == "2026-10-11" and d["quiet"] is False

def test_monthly_report_page(server):
    base, _, _ = server
    status, headers, body = get(f"{base}/report?client=Acme&month=2026-10")
    assert status == 200 and headers["Content-Type"].startswith("text/html")
    assert "<h1>Acme</h1>" in body and 'src="print.js"' in body and "<script>" not in body
    assert "month=2026-09" in body and "view=internal" in body
    assert "Markup" in get(f"{base}/report?client=Acme&month=2026-10&view=internal")[2]
    assert get(f"{base}/print.js")[0] == 200
    with pytest.raises(urllib.error.HTTPError) as e:
        get(f"{base}/report?client=Acme&month=oct")
    assert e.value.code == 400
