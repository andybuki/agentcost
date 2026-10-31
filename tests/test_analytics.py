import tomllib
from datetime import date
import agentcost.analytics as A
from agentcost.rules import load_config
from conftest import make_entry

def recs(config_path, entries):
    cfg, rules = load_config(config_path)
    turns = [{"msg_id": e["message"]["id"], "request_id": e["requestId"], "ts": e["timestamp"],
              "session": e["sessionId"], "cwd": e["cwd"], "branch": e["gitBranch"],
              "model": e["message"]["model"], "in": 1_000_000, "out": 0, "cw": 0, "cr": 0}
             for e in entries]
    return A.enrich(turns, cfg, rules)[0]

def test_local_timezone_moves_day(tmp_path):
    p = tmp_path / "c.toml"
    p.write_text('timezone = "Europe/Berlin"\n[prices."m"]\ninput=1\noutput=1\ncache_write=1\ncache_read=1\n')
    cfg, rules = load_config(p)
    t = {"ts": "2026-09-30T22:30:00Z", "model": "m", "cwd": "/x/proj", "branch": "",
         "session": "s", "in": 1, "out": 0, "cw": 0, "cr": 0}
    (r,) = A.enrich([t], cfg, rules)[0]
    assert r["day"] == date(2026, 10, 1)
    assert r["project"] == "proj"

def test_compare_and_delta(config_path):
    rs = recs(config_path, [
        make_entry(msg_id="a", ts="2026-09-10T10:00:00Z"),
        make_entry(msg_id="b", ts="2026-10-10T10:00:00Z"),
        make_entry(msg_id="c", ts="2026-10-11T10:00:00Z"),
        make_entry(msg_id="d", ts="2026-09-12T10:00:00Z", cwd="/projects/swn-x"),
    ])
    cur = A.select(rs, date(2026, 10, 1), date(2026, 10, 31))
    prev = A.select(rs, date(2026, 9, 1), date(2026, 9, 30))
    rows = {r["name"]: r for r in A.compare(cur, prev, "client")}
    assert rows["Acme"]["cost"] == 3.0 and rows["Acme"]["cmp_cost"] == 1.5
    assert rows["Acme"]["delta_pct"] == 1.0
    assert rows["SWN"]["cost"] == 0 and rows["SWN"]["delta"] == -1.5  # only in B
    assert rows["Acme"]["share"] == 1.0

def test_select_client_filter(config_path):
    rs = recs(config_path, [make_entry(msg_id="a"), make_entry(msg_id="b", cwd="/projects/swn-x")])
    assert [r["client"] for r in A.select(rs, date(2026, 1, 1), date(2026, 12, 31), "SWN")] == ["SWN"]

def test_previous_period():
    assert A.previous_period(date(2026, 10, 1), date(2026, 10, 31)) == (date(2026, 9, 1), date(2026, 9, 30))
    assert A.previous_period(date(2026, 3, 1), date(2026, 3, 31)) == (date(2026, 2, 1), date(2026, 2, 28))
    assert A.previous_period(date(2026, 7, 1), date(2026, 9, 30)) == (date(2026, 4, 1), date(2026, 6, 30))
    assert A.previous_period(date(2026, 1, 1), date(2026, 2, 28)) == (date(2025, 11, 1), date(2025, 12, 31))
    assert A.previous_period(date(2026, 10, 5), date(2026, 10, 11)) == (date(2026, 9, 28), date(2026, 10, 4))

def test_year_before_leap_day():
    assert A.year_before(date(2028, 2, 29), date(2028, 3, 1)) == (date(2027, 2, 28), date(2027, 3, 1))

def test_buckets():
    assert A.buckets(date(2026, 10, 1), date(2026, 10, 3), "day") == ["2026-10-01", "2026-10-02", "2026-10-03"]
    assert A.buckets(date(2026, 9, 30), date(2026, 10, 12), "week") == ["2026-09-28", "2026-10-05", "2026-10-12"]
    assert A.buckets(date(2026, 5, 15), date(2026, 7, 1), "month") == ["2026-05", "2026-06", "2026-07"]
    assert A.auto_bucket(date(2026, 10, 1), date(2026, 10, 31)) == "day"
    assert A.auto_bucket(date(2026, 1, 1), date(2026, 12, 31)) == "month"

def test_timeseries_folds_into_other_with_stable_order(config_path):
    rs = recs(config_path, [make_entry(msg_id="a", ts="2026-10-01T10:00:00Z"),
                            make_entry(msg_id="b", ts="2026-10-02T10:00:00Z", cwd="/projects/swn-x"),
                            make_entry(msg_id="c", ts="2026-10-02T11:00:00Z", cwd="/elsewhere")])
    ts = A.timeseries(rs, date(2026, 10, 1), date(2026, 10, 2), "day", "client",
                      order=["SWN", "Acme", "(unassigned)"], top=2)
    assert [s["name"] for s in ts["series"]] == ["SWN", "Acme", "Other"]
    assert ts["series"][1]["values"] == [1.5, 0.0]

def test_subscription_prorated():
    cfg = {"subscription": {"monthly_fee": 300}}
    assert A.subscription_fee(cfg, date(2026, 9, 1), date(2026, 9, 30)) == 300
    assert A.subscription_fee(cfg, date(2026, 9, 1), date(2026, 9, 10)) == 100
    assert round(A.subscription_fee(cfg, date(2026, 9, 16), date(2026, 10, 31)), 6) == 450
    assert A.subscription_fee({}, date(2026, 9, 1), date(2026, 9, 30)) is None

def test_unassigned_snippet_is_valid_toml_and_matches(config_path):
    import re
    for cwd in ["/home/a/projects/oss.experiment (v2)", "/home/a/it's"]:
        rs = recs(config_path, [make_entry(cwd=cwd)])
        (row,) = A.unassigned(rs)
        rule = tomllib.loads(row["snippet"])["client"][0]
        assert re.search(rule["cwd"][0], cwd)

def test_subscription_since():
    cfg = {"subscription": {"monthly_fee": 300, "since": date(2026, 9, 21)}}
    assert A.subscription_fee(cfg, date(2026, 9, 1), date(2026, 9, 30)) == 100
    assert A.subscription_fee(cfg, date(2026, 8, 1), date(2026, 8, 31)) == 0

def test_windows_project_name_and_snippet(config_path):
    import re
    rs = recs(config_path, [make_entry(cwd=r"C:\Users\me\IdeaProjects\agentcost")])
    assert rs[0]["project"] == "agentcost"
    (row,) = A.unassigned(rs)
    assert row["cwd"] == "C:/Users/me/IdeaProjects/agentcost"
    config_path.write_text(config_path.read_text() + "\n" + row["snippet"])
    from agentcost.rules import load_config as lc, attribute
    _, rules = lc(config_path)
    assert attribute(rules, r"C:\Users\me\IdeaProjects\agentcost", "main") == "agentcost"
    assert attribute(rules, r"c:\users\me\ideaprojects\agentcost\sub", "") == "agentcost"
    assert attribute(rules, r"C:\Users\me\IdeaProjects\agentcost2", "") == "(unassigned)"

def test_no_snippet_for_sessions_without_repository(config_path):
    rs = recs(config_path, [make_entry(cwd="/home/user")])
    (row,) = A.unassigned(rs)
    assert row["snippet"] is None and rs[0]["project"] == "(no repository)"

def test_sessions_list(config_path):
    rs = recs(config_path, [make_entry(msg_id="a", session="s1", ts="2026-10-01T10:00:00Z"),
                            make_entry(msg_id="b", session="s1", ts="2026-10-01T12:00:00Z", model="claude-sonnet-4-5"),
                            make_entry(msg_id="c", session="s2", ts="2026-10-02T09:00:00Z", cwd="/projects/swn-x")])
    rows = A.sessions(rs)
    assert [r["session"] for r in rows] == ["s2", "s1"]
    s1 = rows[1]
    assert s1["turns"] == 2 and s1["project"] == "acme-x" and s1["source"] == "local"
    assert s1["models"] == ["claude-sonnet-3", "claude-sonnet-4-5"]
    assert s1["start"] < s1["end"]
