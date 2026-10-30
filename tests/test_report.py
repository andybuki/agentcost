from agentcost.parse import iter_turns
from agentcost.report import aggregate, main
from agentcost.rules import UNASSIGNED, load_config
from conftest import make_entry

def run(config_path, logs, month=None):
    cfg, rules = load_config(config_path)
    rows, hdr, unpriced = aggregate(iter_turns(logs), cfg, rules, month)
    return {r[0]: r for r in rows}, hdr, unpriced

def test_cost_currency_and_markup(config_path, write_log):
    # 1M input tokens @ 3 USD, rate 0.5 -> 1.50 EUR; Acme markup 1.5 -> 2.25
    rows, hdr, _ = run(config_path, write_log([make_entry()]))
    assert hdr[-2:] == ["cost_EUR", "billable_EUR"]
    assert rows["Acme"][6:] == [1.5, 2.25]

def test_zero_markup_and_unassigned_not_billable(config_path, write_log):
    logs = write_log([make_entry(msg_id="a", cwd="/projects/internal"),
                      make_entry(msg_id="b", cwd="/elsewhere")])
    rows, _, _ = run(config_path, logs)
    assert rows["Internal"][6:] == [1.5, 0.0]
    assert rows[UNASSIGNED][6:] == [1.5, 0.0]

def test_sessions_counted_per_client(config_path, write_log):
    logs = write_log([make_entry(msg_id=m, session=s) for m, s in
                      [("a", "s1"), ("b", "s1"), ("c", "s2")]])
    rows, _, _ = run(config_path, logs)
    assert rows["Acme"][1] == 2

def test_month_filter(config_path, write_log):
    logs = write_log([make_entry(msg_id="a", ts="2026-09-30T23:00:00Z"),
                      make_entry(msg_id="b", ts="2026-10-01T00:10:00Z")])
    rows, _, _ = run(config_path, logs, "2026-10")
    assert rows["Acme"][2] == 1_000_000

def test_unpriced_models_reported_tokens_kept_cost_zero(config_path, write_log):
    logs = write_log([make_entry(msg_id="a", model="deepseek-chat"), make_entry(msg_id="b")])
    rows, _, unpriced = run(config_path, logs)
    assert unpriced == {"deepseek-chat"}
    assert rows["Acme"][2] == 2_000_000          # usage of both models is shown
    assert rows["Acme"][6:] == [1.5, 2.25]       # only the priced one costs money

def test_rows_sorted_by_cost(config_path, write_log):
    logs = write_log([make_entry(msg_id="a", cwd="/projects/swn-x"),
                      make_entry(msg_id="b", cwd="/projects/swn-x"),
                      make_entry(msg_id="c")])
    cfg, rules = load_config(config_path)
    rows, _, _ = aggregate(iter_turns(logs), cfg, rules)
    assert [r[0] for r in rows] == ["SWN", "Acme"]

def test_cli_writes_csv(config_path, write_log, tmp_path, capsys):
    out = tmp_path / "r.csv"
    main(["--config", str(config_path), "--logs", str(write_log([make_entry()])),
          "--db", str(tmp_path / "t.db"), "--csv", str(out)])
    assert "Total cost: 1.50 EUR" in capsys.readouterr().out
    assert out.read_text().splitlines()[1] == "Acme,1,1000000,0,0,0,1.5,2.25"

def test_missing_config_gives_clear_error(tmp_path, monkeypatch, capsys):
    import pytest
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit) as e:
        main(["doctor", "--logs", str(tmp_path), "--db", str(tmp_path / "t.db")])
    assert e.value.code == 2
    assert "config file not found" in capsys.readouterr().err

def test_config_defaults_to_clients_toml(config_path, write_log, tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(config_path.parent)  # clients.toml lives here
    main(["report", "--logs", str(write_log([make_entry()])), "--db", str(tmp_path / "t.db")])
    assert "Total cost: 1.50 EUR" in capsys.readouterr().out
