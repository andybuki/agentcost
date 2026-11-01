from datetime import date
import pytest
from agentcost import monthly
from agentcost.billing import has_retainer, settle
from agentcost.rules import ConfigError, load_config
from conftest import CONFIG, make_entry
from test_analytics import recs

def test_settle_rules():
    assert settle(4.5, None)["rule"] is None
    assert settle(4.5, {"min_fee": 10}) == {"usage": 4.5, "amount": 10, "adjustment": 5.5, "rule": "min_fee"}
    assert settle(40, {"min_fee": 10, "cap": 25})["amount"] == 25
    assert settle(40, {"min_fee": 10, "cap": 25})["rule"] == "cap"
    assert settle(15, {"min_fee": 10, "cap": 25})["rule"] is None
    f = settle(4.5, {"fixed_fee": 200})
    assert f["amount"] == 200 and f["rule"] == "fixed_fee"
    assert settle(0, {"fixed_fee": 0})["rule"] is None

def test_retainer():
    assert has_retainer({"fixed_fee": 100}) and has_retainer({"min_fee": 5})
    assert not has_retainer({"cap": 5}) and not has_retainer({})

@pytest.mark.parametrize("extra,msg", [
    ("fixed_fee = 100\nmin_fee = 10", "fixed_fee cannot be combined"),
    ("min_fee = 50\ncap = 10", "min_fee (50) is above cap (10)"),
    ('cap = "x"', "client 'Acme' cap must be a number"),
])
def test_config_errors(tmp_path, extra, msg):
    p = tmp_path / "c.toml"
    p.write_text(CONFIG.replace("markup = 1.5\n", f"markup = 1.5\n{extra}\n", 1))
    with pytest.raises(ConfigError, match=msg.replace("(", r"\(").replace(")", r"\)")):
        load_config(p)

def build(tmp_path, extra):
    p = tmp_path / "c.toml"
    p.write_text(CONFIG.replace("markup = 1.5\n", f"markup = 1.5\n{extra}\n", 1))
    rs = recs(p, [make_entry(msg_id="a", ts="2026-10-05T10:00:00Z"),
                  make_entry(msg_id="b", ts="2026-10-06T10:00:00Z", session="s2")])
    cfg, rules = load_config(p)
    return monthly.build(rs, cfg, rules, "Acme", "2026-10")

def test_report_minimum_fee(tmp_path):
    d = build(tmp_path, "min_fee = 50")  # usage 3.00 cost × 1.5 = 4.50
    assert d["billing"]["amount"] == 50 and d["billing"]["usage"] == 4.5
    client = monthly.render(d, generated=date(2026, 11, 1))
    assert "Minimum monthly fee" in client and "50.00" in client and "Usage value" in client
    assert "Margin" not in client and "3.00" not in client  # still no cost in the client view
    internal = monthly.render(d, internal=True)
    assert "47.00" in internal  # margin = 50 - cost 3.00

def test_report_fixed_fee_and_cap(tmp_path):
    assert build(tmp_path, "fixed_fee = 200")["billing"]["amount"] == 200
    assert build(tmp_path, "cap = 2")["billing"]["adjustment"] == pytest.approx(-2.5)

def test_report_without_terms_unchanged(tmp_path):
    html = monthly.render(build(tmp_path, ""))
    assert "Usage value" not in html and "monthly fee" not in html

def test_cli_includes_retainer_client_without_usage(tmp_path, write_log):
    from agentcost.report import main
    p = tmp_path / "c.toml"
    p.write_text(CONFIG.replace("markup = 0\n", "markup = 0\n\n[[client]]\nname = \"Retainer\"\n"
                                "cwd = ['/projects/ret']\nfixed_fee = 300\n"))
    logs = write_log([make_entry(msg_id="a", ts="2026-10-05T10:00:00Z")])
    out = tmp_path / "r"
    main(["monthly", "--config", str(p), "--logs", str(logs), "--db", str(tmp_path / "d.db"),
          "--cloud-config", str(tmp_path / "c.json"), "--month", "2026-10", "--out-dir", str(out)])
    assert sorted(x.name for x in out.iterdir()) == ["acme-2026-10.html", "retainer-2026-10.html"]
