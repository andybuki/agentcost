import json, os, shutil, socket, subprocess, sys, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from agentcost import cloud_hook
from agentcost.parse import parse_line
from agentcost.report import main, setup_script
from agentcost.store import connect, load_turns
from agentcost.sync import load_cloud_config, save_cloud_config, sync, sync_if_configured, SyncError
from conftest import make_entry

ROOT = Path(__file__).resolve().parent.parent

class FakeCollector:
    """In-process collector with the worker's HTTP contract."""
    def __init__(self, fail=False, page=1000):
        self.rows, self.posts, self.fail, self.page = [], [], fail, page
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a): pass
            def _send(self, status, obj):
                body = json.dumps(obj).encode()
                self.send_response(status); self.send_header("Content-Length", str(len(body)))
                self.end_headers(); self.wfile.write(body)
            def do_POST(self):
                if outer.fail:
                    return self._send(500, {"error": "down"})
                if self.headers.get("Authorization") != "Bearer in":
                    return self._send(401, {"error": "unauthorized"})
                turns = json.loads(self.rfile.read(int(self.headers["Content-Length"])))["turns"]
                outer.posts.append(turns)
                keys = {(r["msg_id"], r["request_id"]) for r in outer.rows}
                for t in turns:
                    if (t["msg_id"], t["request_id"]) not in keys:
                        outer.rows.append({**t, "seq": len(outer.rows) + 1})
                self._send(200, {"received": len(turns)})
            def do_GET(self):
                if self.headers.get("Authorization") != "Bearer rd":
                    return self._send(401, {"error": "unauthorized"})
                after = int(self.path.split("after=")[1])
                rows = [r for r in outer.rows if r["seq"] > after][:outer.page]
                self._send(200, {"turns": rows, "last": rows[-1]["seq"] if rows else after,
                                 "more": len(rows) == outer.page})

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

@pytest.fixture
def collector():
    c = FakeCollector()
    yield c
    c.httpd.shutdown()

@pytest.fixture(autouse=True)
def hook_state(tmp_path, monkeypatch):
    monkeypatch.setattr(cloud_hook, "STATE", tmp_path / "hook-state.json")

def transcript(tmp_path, entries, name="t.jsonl"):
    p = tmp_path / name
    with open(p, "a") as f:
        for e in entries:
            f.write((e if isinstance(e, str) else json.dumps(e)) + "\n")
    return str(p)

# ---- hook ----

def test_hook_metadata_matches_parser():
    line = json.dumps(make_entry())
    assert cloud_hook.metadata(line) == {k: v for k, v in parse_line(line).items()}

def test_hook_sends_only_new_lines_and_never_text(tmp_path, collector):
    e = make_entry(msg_id="a")
    e["message"]["content"] = [{"type": "text", "text": "SECRET PROMPT"}]
    path = transcript(tmp_path, [e, {"type": "user", "message": {"content": "SECRET"}}])
    assert cloud_hook.run({"transcript_path": path}, collector.url, "in") == 1
    assert cloud_hook.run({"transcript_path": path}, collector.url, "in") == 0
    transcript(tmp_path, [make_entry(msg_id="b")])
    assert cloud_hook.run({"transcript_path": path}, collector.url, "in") == 1
    assert [r["msg_id"] for r in collector.rows] == ["a", "b"]
    assert "SECRET" not in json.dumps(collector.posts)

def test_hook_retries_after_failure(tmp_path, collector):
    path = transcript(tmp_path, [make_entry(msg_id="a")])
    collector.fail = True
    with pytest.raises(Exception):
        cloud_hook.run({"transcript_path": path}, collector.url, "in")
    collector.fail = False
    assert cloud_hook.run({"transcript_path": path}, collector.url, "in") == 1

def test_hook_waits_for_partial_line(tmp_path, collector):
    path = transcript(tmp_path, [make_entry(msg_id="a")])
    with open(path, "a") as f:
        f.write(json.dumps(make_entry(msg_id="b"))[:30])
    assert cloud_hook.run({"transcript_path": path}, collector.url, "in") == 1

def test_hook_is_silent_without_config_or_on_errors(tmp_path, monkeypatch, capsys):
    path = transcript(tmp_path, [make_entry()])
    assert cloud_hook.run({"transcript_path": path}, None, None) == 0
    monkeypatch.setenv("AGENTCOST_URL", "http://127.0.0.1:9")  # nothing listens here
    monkeypatch.setenv("AGENTCOST_INGEST_TOKEN", "in")
    monkeypatch.setattr(sys, "stdin", __import__("io").StringIO(json.dumps({"transcript_path": path})))
    assert cloud_hook.main() == 0
    assert capsys.readouterr().err == ""

# ---- sync ----

def test_sync_pages_and_is_incremental(tmp_path, collector):
    collector.page = 2
    path = transcript(tmp_path, [make_entry(msg_id=m) for m in "abcde"])
    cloud_hook.run({"transcript_path": path}, collector.url, "in")
    con = connect(tmp_path / "s.db")
    assert sync(con, collector.url, "rd") == 5
    assert sync(con, collector.url, "rd") == 0
    transcript(tmp_path, [make_entry(msg_id="f")])
    cloud_hook.run({"transcript_path": path}, collector.url, "in")
    assert sync(con, collector.url, "rd") == 1
    assert {t["source"] for t in load_turns(con)} == {"cloud"}

def test_sync_wrong_token_explains(tmp_path, collector):
    con = connect(tmp_path / "s.db")
    with pytest.raises(SyncError, match="wrong read token"):
        sync(con, collector.url, "nope")

def test_sync_if_configured(tmp_path, collector):
    con = connect(tmp_path / "s.db")
    cfg = tmp_path / "cloud.json"
    assert sync_if_configured(con, cfg) == (0, None)
    save_cloud_config(collector.url + "/", "rd", cfg)
    assert load_cloud_config(cfg)["url"] == collector.url
    assert sync_if_configured(con, cfg) == (0, None)
    save_cloud_config("http://127.0.0.1:9", "rd", cfg)
    added, err = sync_if_configured(con, cfg)
    assert added == 0 and "not reachable" in err

def test_local_and_cloud_turns_together(tmp_path, collector, write_log, config_path, capsys):
    path = transcript(tmp_path, [make_entry(msg_id="cloud1", cwd="/home/user/acme-api")])
    cloud_hook.run({"transcript_path": path}, collector.url, "in")
    logs = write_log([make_entry(msg_id="local1")])
    cfg = tmp_path / "cloud.json"
    save_cloud_config(collector.url, "rd", cfg)
    main(["report", "--config", str(config_path), "--logs", str(logs), "--db", str(tmp_path / "r.db"),
          "--cloud-config", str(cfg)])
    out = capsys.readouterr().out
    assert "Acme" in out and "Total cost: 3.00 EUR" in out  # one local + one cloud turn

def test_old_store_gets_source_column(tmp_path):
    import sqlite3
    db = tmp_path / "old.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE turns (msg_id TEXT NOT NULL, request_id TEXT NOT NULL, ts TEXT NOT NULL,"
                " session TEXT, cwd TEXT, branch TEXT, model TEXT, tok_in INTEGER, tok_out INTEGER,"
                " cache_write INTEGER, cache_read INTEGER, source_file TEXT, ingested_at TEXT,"
                " PRIMARY KEY (msg_id, request_id))")
    con.execute("INSERT INTO turns VALUES ('a','r','2026-01-01T00:00:00Z','s','/x','main','m',1,1,1,1,'f','t')")
    con.commit(); con.close()
    assert load_turns(connect(db))[0]["source"] == "local"

# ---- setup script ----

def test_setup_script_installs_hook_idempotently(tmp_path):
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    (home / ".claude" / "settings.json").write_text(json.dumps({"hooks": {"Stop": [
        {"hooks": [{"type": "command", "command": "other.sh"}]}]}, "model": "x"}))
    script = tmp_path / "setup.sh"
    script.write_text(setup_script())
    for _ in range(2):
        subprocess.run(["bash", str(script)], env={**os.environ, "HOME": str(home)}, check=True)
    hook = home / ".agentcost-hook" / "agentcost_hook.py"
    assert hook.read_text() == (ROOT / "agentcost" / "cloud_hook.py").read_text()
    s = json.loads((home / ".claude" / "settings.json").read_text())
    cmds = [h["command"] for e in s["hooks"]["Stop"] for h in e["hooks"]]
    assert cmds == ["other.sh", "python3 ~/.agentcost-hook/agentcost_hook.py"]
    assert s["model"] == "x"

def test_hook_check_mode(tmp_path, collector, monkeypatch, capsys):
    home = tmp_path / "home"
    proj = home / ".claude" / "projects" / "p"
    proj.mkdir(parents=True)
    (proj / "s.jsonl").write_text(json.dumps(make_entry()) + "\n")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(sys, "argv", ["hook", "check"])
    monkeypatch.delenv("AGENTCOST_URL", raising=False)
    assert cloud_hook.main() == 1
    assert "not set" in capsys.readouterr().out
    monkeypatch.setenv("AGENTCOST_URL", collector.url)
    monkeypatch.setenv("AGENTCOST_INGEST_TOKEN", "wrong")
    assert cloud_hook.main() == 1
    assert "failed" in capsys.readouterr().out
    monkeypatch.setenv("AGENTCOST_INGEST_TOKEN", "in")
    assert cloud_hook.main() == 0
    assert "OK: 1 new turns" in capsys.readouterr().out
