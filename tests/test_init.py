import json, tomllib
from datetime import date
import pytest
from agentcost.init import build
from agentcost.report import main
from agentcost.rules import attribute, load_config
from conftest import make_entry

def turn(cwd, source="local", ts="2026-10-01T10:00:00Z"):
    return {"cwd": cwd, "ts": ts, "source": source}

def test_build_groups_by_project_and_matches(tmp_path):
    turns = ([turn(r"C:\Users\me\IdeaProjects\agentcost")] * 3
             + [turn("/home/user/agentcost", "cloud", "2026-10-31T09:00:00Z")]
             + [turn("/home/user/bankstm", "cloud")] * 5
             + [turn("/home/user", "cloud")])
    text, n = build(turns, date(2026, 10, 31))
    assert n == 2
    p = tmp_path / "c.toml"
    p.write_text(text)
    cfg, rules = load_config(p)
    assert [r["name"] for r in rules] == ["bankstm", "agentcost"]  # most used first
    assert attribute(rules, r"C:\Users\me\IdeaProjects\agentcost\sub", "") == "agentcost"
    assert attribute(rules, "/home/user/agentcost", "main") == "agentcost"
    assert attribute(rules, "/home/user/bankstm2", "") == "(unassigned)"
    assert "/home/user  (1 turns)" in text  # no-repository sessions listed as comment only
    assert "4 turns in total" in text

def test_build_without_data(tmp_path):
    text, n = build([])
    assert n == 0
    tomllib.loads(text)

def test_init_command_writes_and_refuses_overwrite(tmp_path, write_log, capsys):
    logs = write_log([make_entry(msg_id="a", cwd="/work/acme-shop")])
    target = tmp_path / "clients.toml"
    args = ["init", "--config", str(target), "--logs", str(logs), "--db", str(tmp_path / "d.db"),
            "--cloud-config", str(tmp_path / "c.json")]
    main(args)
    assert "Created" in capsys.readouterr().out
    assert "name = 'acme-shop'" in target.read_text()
    with pytest.raises(SystemExit) as e:
        main(args)
    assert "already exists" in str(e.value)
    main(args + ["--print"])
    assert "[[client]]" in capsys.readouterr().out
    main(args + ["--force"])

def test_subfolders_count_under_parent():
    turns = [turn("/home/user/agentcost")] * 2 + [turn("/home/user/agentcost/cloud/worker")] * 3
    text, n = build(turns)
    assert n == 1 and "worker" not in text and "5 turns in total" in text
