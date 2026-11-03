"""Usage from several devices: device names, hook-setup on a local computer, quiet devices."""
import json, socket, sqlite3
from datetime import date

import pytest

import agentcost.analytics as A
from agentcost import cloud_hook
from agentcost.report import install_hook, main
from agentcost.store import connect, ingest, load_turns
from agentcost.sync import sync
from conftest import make_entry
from test_cloud import FakeCollector, transcript

@pytest.fixture
def collector():
    c = FakeCollector()
    yield c
    c.httpd.shutdown()

@pytest.fixture(autouse=True)
def isolated_hook(tmp_path, monkeypatch):
    monkeypatch.setattr(cloud_hook, "STATE", tmp_path / "hook" / "state.json")
    monkeypatch.setattr(cloud_hook, "CONFIG", tmp_path / "hook" / "config.json")
    monkeypatch.setattr(cloud_hook, "HOOK_DIR", tmp_path / "hook")
    for k in ("AGENTCOST_URL", "AGENTCOST_INGEST_TOKEN", "AGENTCOST_DEVICE", "CLAUDE_CODE_REMOTE",
              "CLAUDE_CONFIG_DIR"):
        monkeypatch.delenv(k, raising=False)

# ---- device name ----

def test_device_name(monkeypatch):
    assert cloud_hook.device_name() == socket.gethostname()
    monkeypatch.setenv("CLAUDE_CODE_REMOTE", "true")
    assert cloud_hook.device_name() == "cloud"
    monkeypatch.setenv("AGENTCOST_DEVICE", "  phone-env  ")
    assert cloud_hook.device_name() == "phone-env"

def test_hook_sends_device_and_sync_stores_it(collector, tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_REMOTE", "true")
    path = transcript(tmp_path, [make_entry(msg_id="a")])
    assert cloud_hook.run({"transcript_path": path}, collector.url, "in") == 1
    assert collector.posts[0][0]["device"] == "cloud"
    con = connect(tmp_path / "s.db")
    sync(con, collector.url, "rd")
    (t,) = load_turns(con)
    assert (t["source"], t["device"]) == ("cloud", "cloud")

def test_local_ingest_records_this_computer(tmp_path, write_log):
    con = connect(tmp_path / "s.db")
    ingest(con, write_log([make_entry()]))
    (t,) = load_turns(con)
    assert t["device"] == socket.gethostname()

def test_old_store_gets_device_column(tmp_path):
    db = tmp_path / "old.db"
    con = sqlite3.connect(db)
    con.execute("""CREATE TABLE turns (msg_id TEXT NOT NULL, request_id TEXT NOT NULL, ts TEXT NOT NULL,
        session TEXT, cwd TEXT, branch TEXT, model TEXT, tok_in INTEGER NOT NULL DEFAULT 0,
        tok_out INTEGER NOT NULL DEFAULT 0, cache_write INTEGER NOT NULL DEFAULT 0,
        cache_read INTEGER NOT NULL DEFAULT 0, cache_write_1h INTEGER NOT NULL DEFAULT 0,
        source_file TEXT, ingested_at TEXT, source TEXT NOT NULL DEFAULT 'local',
        PRIMARY KEY (msg_id, request_id))""")
    con.execute("INSERT INTO turns (msg_id, request_id, ts, source) VALUES ('m','r','2026-10-01T10:00:00Z','cloud')")
    con.commit(); con.close()
    (t,) = load_turns(connect(db))
    assert t["device"] == "" and A.device_label(t) == "cloud"

# ---- hook-setup on a local computer ----

def test_install_hook_is_idempotent_and_keeps_other_hooks(tmp_path):
    settings = tmp_path / "claude" / "settings.json"
    settings.parent.mkdir()
    other = {"hooks": [{"type": "command", "command": "echo hi"}]}
    old = {"hooks": [{"type": "command", "command": "python3 ~/.agentcost-hook/agentcost_hook.py"}]}
    settings.write_text(json.dumps({"model": "x", "hooks": {"Stop": [other, old]}}))
    for _ in range(2):
        hook, _, device = install_hook("https://c.example/", " tok ", "laptop", settings, tmp_path / "hook")
    s = json.loads(settings.read_text())
    assert s["model"] == "x"
    cmds = [h["command"] for e in s["hooks"]["Stop"] for h in e["hooks"]]
    assert cmds[0] == "echo hi" and len(cmds) == 2
    assert cmds[1].endswith(f'"{hook.as_posix()}"') and "\\" not in cmds[1]
    assert hook.read_text() == open(cloud_hook.__file__).read()
    cfg = json.loads((tmp_path / "hook" / "config.json").read_text())
    assert cfg == {"url": "https://c.example", "ingest_token": "tok", "device": "laptop"}

def test_hook_reads_config_file_and_env_wins(tmp_path, monkeypatch):
    install_hook("https://c.example", "tok", "laptop", tmp_path / "settings.json", tmp_path / "hook")
    assert cloud_hook.settings() == ("https://c.example", "tok", "laptop")
    monkeypatch.setenv("AGENTCOST_URL", "https://env.example")
    monkeypatch.setenv("AGENTCOST_DEVICE", "cloud-x")
    assert cloud_hook.settings() == ("https://env.example", "tok", "cloud-x")

def test_hook_setup_backfills_all_transcripts(collector, tmp_path, monkeypatch, capsys):
    claude = tmp_path / "claude"
    for i, proj in enumerate(("a", "b")):
        p = claude / "projects" / proj
        p.mkdir(parents=True)
        (p / "s.jsonl").write_text(json.dumps(make_entry(msg_id=f"m{i}")) + "\n")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(claude))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))  # no transcripts of the real user
    main(["hook-setup", "--url", collector.url, "--token", "in", "--device", "laptop"])
    out = capsys.readouterr().out
    assert "OK: 2 new turns" in out and "'laptop'" in out
    assert {t["device"] for t in collector.rows} == {"laptop"}
    assert (claude / "settings.json").is_file()
    # The installed hook sends nothing twice.
    assert cloud_hook.backfill() == 0
    assert "OK: 0 new turns" in capsys.readouterr().out

def test_check_without_settings_explains(capsys):
    assert cloud_hook.check() == 1
    assert "not set" in capsys.readouterr().out

# ---- quiet devices ----

def rec(device, day, source="cloud"):
    return {"device": device, "source": source, "day": day}

def test_devices_flags_only_devices_behind_the_others():
    devs = A.devices([rec("cloud", date(2026, 11, 3)), rec("cloud", date(2026, 10, 30)),
                      rec("laptop", date(2026, 10, 29), "cloud"), rec("pc", date(2026, 11, 1), "local")])
    by = {d["device"]: d for d in devs}
    assert [d["device"] for d in devs] == ["cloud", "pc", "laptop"]
    assert by["cloud"]["turns"] == 2 and by["cloud"]["first_day"] == date(2026, 10, 30)
    assert (by["pc"]["behind_days"], by["pc"]["quiet"]) == (2, False)
    assert (by["laptop"]["behind_days"], by["laptop"]["quiet"]) == (5, True)
    assert A.devices([]) == []

def test_doctor_lists_devices(config_path, write_log, tmp_path, capsys):
    logs = write_log([make_entry()])
    main(["doctor", "--config", str(config_path), "--logs", str(logs), "--db", str(tmp_path / "t.db"),
          "--cloud-config", str(tmp_path / "none.json")])
    out = capsys.readouterr().out
    assert "Devices (where usage came from):" in out
    assert socket.gethostname() in out
