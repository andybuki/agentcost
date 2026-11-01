import re
from datetime import date
import pytest
import agentcost.analytics as A
from agentcost import monthly
from agentcost.rules import load_config
from conftest import make_entry
from test_analytics import recs

def data(config_path):
    rs = recs(config_path, [make_entry(msg_id="a", ts="2026-10-05T10:00:00Z"),
                            make_entry(msg_id="b", ts="2026-10-20T10:00:00Z", session="s2", cwd="/projects/acme-y"),
                            make_entry(msg_id="c", ts="2026-11-02T10:00:00Z"),
                            make_entry(msg_id="d", ts="2026-10-06T10:00:00Z", cwd="/projects/swn-x")])
    cfg, rules = load_config(config_path)
    return monthly.build(rs, cfg, rules, "Acme", "2026-10")

def test_build_only_this_client_and_month(config_path):
    d = data(config_path)
    assert d["summary"]["turns"] == 2 and d["summary"]["cost"] == 3.0
    assert d["summary"]["billable"] == 4.5 and d["markup"] == 1.5
    assert {p["name"] for p in d["projects"]} == {"acme-x", "acme-y"}
    assert [len(d["sessions"]), d["weeks"][0]["week"]] == [2, "2026-10-05"]

def test_client_version_hides_cost_and_markup(config_path):
    html = monthly.render(data(config_path), generated=date(2026, 11, 1))
    assert "4.50" in html and "Amount" in html
    assert "Markup" not in html and "3.00" not in html and "Margin" not in html
    assert "no prompts, answers or code" in html
    assert re.search(r"<script>.*window\.print", html)  # works as a standalone file

def test_internal_version_shows_cost_markup_margin(config_path):
    html = monthly.render(data(config_path), internal=True)
    assert "API-equivalent cost" in html and "× 1.5" in html and "1.50" in html  # margin

def test_escapes_names(config_path, tmp_path):
    d = data(config_path); d["client"] = "<script>x</script>"
    assert "<script>x</script>" not in monthly.render(d)

def test_month_range():
    assert monthly.month_range("2026-02") == (date(2026, 2, 1), date(2026, 2, 28))
    with pytest.raises(ValueError):
        monthly.month_range("Oktober")

def test_cli_writes_reports(config_path, write_log, tmp_path, capsys):
    from agentcost.report import main
    logs = write_log([make_entry(msg_id="a", ts="2026-10-05T10:00:00Z"),
                      make_entry(msg_id="b", ts="2026-10-05T11:00:00Z", cwd="/projects/internal")])
    out = tmp_path / "reports"
    main(["monthly", "--config", str(config_path), "--logs", str(logs), "--db", str(tmp_path / "d.db"),
          "--cloud-config", str(tmp_path / "c.json"), "--month", "2026-10", "--out-dir", str(out)])
    assert [p.name for p in out.iterdir()] == ["acme-2026-10.html"]  # Internal: nothing to bill
