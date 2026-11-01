"""One contract, two collectors: the Python one and the Cloudflare Worker code."""
import json, os, shutil, socket, subprocess, sys, threading, urllib.error, urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from agentcost import cloud_hook
from agentcost.collector import Collector, make_handler
from agentcost.store import connect, load_turns
from agentcost.sync import sync
from conftest import make_entry

ROOT = Path(__file__).resolve().parent.parent
IN, RD = "in-tok", "read-tok"

def _node_ok():
    node = shutil.which("node")
    return bool(node) and subprocess.run(
        [node, "-e", "require('node:sqlite')"], capture_output=True).returncode == 0

@pytest.fixture(params=["python", "worker"])
def collector_url(request, tmp_path):
    if request.param == "python":
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(Collector(tmp_path / "c.db", IN, RD)))
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        yield f"http://127.0.0.1:{httpd.server_address[1]}"
        httpd.shutdown()
    else:
        if not _node_ok():
            pytest.skip("node with node:sqlite not available")
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]
        srv = subprocess.Popen(["node", "test/dev-server.mjs", str(port)], cwd=ROOT / "cloud" / "worker",
                               env={**os.environ, "INGEST_TOKEN": IN, "READ_TOKEN": RD, "NODE_NO_WARNINGS": "1"},
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        assert b"collector on" in srv.stdout.readline()
        yield f"http://127.0.0.1:{port}"
        srv.terminate(); srv.wait()

def call(url, path, body=None, token=IN):
    data = None if body is None else (body if isinstance(body, bytes) else json.dumps(body).encode())
    req = urllib.request.Request(url + path, data=data, method="POST" if data is not None else "GET",
                                 headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())

def turn(i, **extra):
    return {"msg_id": i, "request_id": "r", "ts": "2026-10-30T10:00:00Z", "session": "s",
            "cwd": "/home/user/acme", "branch": "main", "model": "claude-sonnet-4-6",
            "in": 1, "out": 2, "cw": 3, "cr": 4, **extra}

def test_ingest_is_idempotent_and_listable(collector_url):
    assert call(collector_url, "/ingest", {"turns": [turn("a"), turn("b")]}) == (200, {"received": 2, "added": 2})
    assert call(collector_url, "/ingest", {"turns": [turn("a"), turn("c")]}) == (200, {"received": 2, "added": 1})
    status, page = call(collector_url, "/turns?after=0", token=RD)
    assert status == 200 and [t["msg_id"] for t in page["turns"]] == ["a", "b", "c"]
    assert page["turns"][0]["cr"] == 4 and page["more"] is False
    assert call(collector_url, f"/turns?after={page['last']}", token=RD)[1]["turns"] == []

def test_tokens_are_separate(collector_url):
    assert call(collector_url, "/ingest", {"turns": [turn("a")]}, token=RD)[0] == 401
    assert call(collector_url, "/turns?after=0", token=IN)[0] == 401
    assert call(collector_url, "/turns?after=0", token="")[0] == 401

@pytest.mark.parametrize("body", [
    {"turns": [turn("a", **{"in": -1})]}, {"turns": [turn("a", cwd=5)]}, {"turns": [turn("")]},
    {"turns": "x"}, {"turns": [turn("a")] * 1001}, b"{"])
def test_rejects_bad_input(collector_url, body):
    assert call(collector_url, "/ingest", body)[0] == 400

def test_bad_cursor_and_unknown_path(collector_url):
    assert call(collector_url, "/turns?after=-1", token=RD)[0] == 400
    assert call(collector_url, "/nope", token=RD)[0] == 404
    assert call(collector_url, "/", token="")[1] == {"service": "agentcost collector"}

def test_unknown_fields_are_not_stored(collector_url):
    call(collector_url, "/ingest", {"turns": [turn("a", prompt="SECRET")]})
    assert "SECRET" not in json.dumps(call(collector_url, "/turns?after=0", token=RD)[1])

def test_paging(collector_url):
    for start in (0, 1000):
        call(collector_url, "/ingest", {"turns": [turn(f"m{i}") for i in range(start, start + 1000)]})
    call(collector_url, "/ingest", {"turns": [turn("last")]})
    page = call(collector_url, "/turns?after=0", token=RD)[1]
    assert len(page["turns"]) == 1000 and page["more"] is True

def test_hook_to_collector_to_store(collector_url, tmp_path, monkeypatch):
    monkeypatch.setattr(cloud_hook, "STATE", tmp_path / "state.json")
    t = tmp_path / "t.jsonl"
    t.write_text("".join(json.dumps(make_entry(msg_id=f"m{i}", cwd="/home/user/acme-api")) + "\n"
                         for i in range(3)))
    assert cloud_hook.run({"transcript_path": str(t)}, collector_url, IN) == 3
    con = connect(tmp_path / "s.db")
    assert sync(con, collector_url, RD) == 3
    assert sync(con, collector_url, RD) == 0
    turns = load_turns(con)
    assert {(x["cwd"], x["source"]) for x in turns} == {("/home/user/acme-api", "cloud")}

def test_collector_refuses_weak_token_setup(tmp_path):
    with pytest.raises(ValueError):
        Collector(tmp_path / "c.db", "same", "same")
    with pytest.raises(ValueError):
        Collector(tmp_path / "c.db", "", "x")

def test_resend_fills_one_hour_share(collector_url):
    call(collector_url, "/ingest", {"turns": [turn("a")]})
    call(collector_url, "/ingest", {"turns": [turn("a", cw1h=3), turn("b", cw1h=2)]})
    rows = {t["msg_id"]: t for t in call(collector_url, "/turns?after=0", token=RD)[1]["turns"]}
    assert (rows["a"]["cw1h"], rows["b"]["cw1h"]) == (3, 2)
    assert rows["a"]["in"] == 1 and len(rows) == 2

def test_turn_without_one_hour_field_still_accepted(collector_url):
    t = turn("old"); t.pop("cw1h", None)
    assert call(collector_url, "/ingest", {"turns": [t]})[0] == 200
