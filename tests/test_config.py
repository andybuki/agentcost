import tomllib
import pytest
from agentcost.rules import ConfigError, builtin_prices, load_config, price_for, PRICE_FIELDS

def cfg(tmp_path, text):
    p = tmp_path / "c.toml"
    p.write_text(text)
    return p

def test_builtin_prices_are_complete():
    prices = builtin_prices()
    assert prices["claude-opus-5-5"] == {"input": 4.0, "output": 20.0, "cache_write": 5.0, "cache_read": 0.2}
    for model, p in prices.items():
        assert set(p) == set(PRICE_FIELDS), model
        assert p["cache_write"] == pytest.approx(p["input"] * 1.25, abs=0.02), model  # Haiku 3: published $0.30

def test_builtin_prices_cover_real_model_ids(tmp_path):
    c, _ = load_config(cfg(tmp_path, ""))
    for model, expected in [("claude-opus-5-5", 4.0), ("claude-sonnet-4-6", 3.0),
                            ("claude-haiku-4-5-20251001", 1.0), ("claude-opus-4-1-20250805", 15.0),
                            ("claude-opus-4-20250514", 15.0), ("claude-sonnet-4-20250514", 3.0),
                            ("claude-opus-4-5-20251101", 5.0), ("claude-fable-5-1", 10.0),
                            ("claude-3-5-haiku-20241022", 0.8)]:
        assert price_for(c["prices"], model)["input"] == expected, model
    assert price_for(c["prices"], "claude-sonnet-99") is None

def test_user_prices_override_and_add(tmp_path):
    c, _ = load_config(cfg(tmp_path, '[prices."claude-opus-5-5"]\ninput=1\noutput=2\ncache_write=3\ncache_read=4\n'
                                     '[prices."deepseek"]\ninput=0.3\noutput=1\ncache_write=0\ncache_read=0\n'))
    assert price_for(c["prices"], "claude-opus-5-5")["input"] == 1
    assert price_for(c["prices"], "deepseek-chat")["input"] == 0.3
    assert c["user_prices"] == {"claude-opus-5-5", "deepseek"}

@pytest.mark.parametrize("text, message", [
    ('[[client]]\nname = "A"\ncwd = ["acme("]\n', "client 'A' cwd: 'acme(' is not a valid regular expression"),
    ('[[client]]\ncwd = ["x"]\n', "number 1 has no name"),
    ('[[client]]\nname = "A"\n[[client]]\nname = "A"\n', "used twice"),
    ('[[client]]\nname = "A"\nmarkup = "1.3"\n', "client 'A' markup must be a number"),
    ('[[client]]\nname = "A"\ncwd = [1]\n', "must be a list of strings"),
    ('timezone = "Berlin"\n', "timezone 'Berlin' is unknown"),
    ('[prices."m"]\ninput = 1\n', 'is missing output'),
    ('[subscription]\nname = "Max"\n', "needs monthly_fee"),
    ('[subscription]\nmonthly_fee = 180\nsince = "2026-05-01"\n', "since must be a date"),
    ('currency_rate = 0\n', "currency_rate must be a number"),
    ('name = "unterminated\n', "is not valid TOML"),
])
def test_clear_errors(tmp_path, text, message):
    with pytest.raises(ConfigError, match=None) as e:
        load_config(cfg(tmp_path, text))
    assert message in str(e.value)

def test_single_string_pattern_allowed(tmp_path):
    _, rules = load_config(cfg(tmp_path, '[[client]]\nname = "A"\ncwd = "/acme/"\n'))
    assert rules[0]["cwd"][0].pattern == "/acme/"

def test_missing_file(tmp_path):
    with pytest.raises(ConfigError, match="agentcost init"):
        load_config(tmp_path / "nope.toml")

def test_shipped_clients_toml_loads():
    from pathlib import Path
    load_config(Path(__file__).resolve().parent.parent / "clients.example.toml")

def test_cli_reports_config_error(tmp_path, capsys):
    from agentcost.report import main
    p = cfg(tmp_path, '[[client]]\nname = "A"\ncwd = ["("]\n')
    with pytest.raises(SystemExit) as e:
        main(["report", "--config", str(p), "--logs", str(tmp_path), "--db", str(tmp_path / "d.db"),
              "--cloud-config", str(tmp_path / "c.json")])
    assert e.value.code == 2
    err = capsys.readouterr().err
    assert "problem in" in err and "not a valid regular expression" in err and "Traceback" not in err
