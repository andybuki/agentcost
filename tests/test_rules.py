from agentcost.rules import UNASSIGNED, attribute, load_config, price_for, turn_cost

def test_cwd_rule(config_path):
    _, rules = load_config(config_path)
    assert attribute(rules, "/home/a/projects/swn-dash", "main") == "SWN"

def test_branch_wins_over_cwd(config_path):
    # monorepo belongs to SWN by directory, but acme/* branches go to Acme
    _, rules = load_config(config_path)
    assert attribute(rules, "/home/a/projects/monorepo", "acme/billing") == "Acme"
    assert attribute(rules, "/home/a/projects/monorepo", "main") == "SWN"

def test_first_matching_rule_wins(config_path):
    _, rules = load_config(config_path)
    # matches both Acme (cwd) and SWN (cwd); Acme is listed first
    assert attribute(rules, "/projects/acme-x/projects/swn-y", "") == "Acme"

def test_unassigned(config_path):
    _, rules = load_config(config_path)
    assert attribute(rules, "/home/a/oss", "main") == UNASSIGNED
    assert attribute(rules, "", "") == UNASSIGNED

def test_markup_default_and_override(config_path):
    _, rules = load_config(config_path)
    m = {r["name"]: r["markup"] for r in rules}
    assert m == {"Acme": 1.5, "SWN": 1.0, "Internal": 0.0}

def test_longest_prefix_price(config_path):
    cfg, _ = load_config(config_path)
    assert price_for(cfg["prices"], "claude-sonnet-4-5-20250929")["input"] == 6.0
    assert price_for(cfg["prices"], "claude-sonnet-3-7")["input"] == 3.0
    assert price_for(cfg["prices"], "claude-opus-4-1") is None

def test_turn_cost():
    price = {"input": 3.0, "output": 15.0, "cache_write": 3.75, "cache_read": 0.3}
    t = {"in": 1_000_000, "out": 100_000, "cw": 200_000, "cr": 1_000_000}
    assert turn_cost(t, price) == 3.0 + 1.5 + 0.75 + 0.3

def test_windows_paths_match_slash_rules(config_path):
    _, rules = load_config(config_path)
    assert attribute(rules, r"C:\Users\me\projects\swn-dash", "main") == "SWN"
