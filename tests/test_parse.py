from agentcost.parse import iter_turns
from conftest import make_entry

def test_extracts_metadata(write_log):
    logs = write_log([make_entry(usage={"input_tokens": 1, "output_tokens": 2,
                                        "cache_creation_input_tokens": 3,
                                        "cache_read_input_tokens": 4})])
    (t,) = iter_turns(logs)
    assert t == {"msg_id": "msg_1", "request_id": "req_1", "ts": "2026-10-01T10:00:00Z", "session": "s1", "cwd": "/projects/acme-x",
                 "branch": "main", "model": "claude-sonnet-3",
                 "in": 1, "out": 2, "cw": 3, "cr": 4}

def test_never_returns_prompt_text(write_log):
    e = make_entry()
    e["message"]["content"] = [{"type": "text", "text": "SECRET"}]
    (t,) = iter_turns(write_log([e]))
    assert "SECRET" not in repr(t)

def test_dedup_streamed_lines(write_log):
    e = make_entry()
    logs = write_log([e, e, e])
    assert len(list(iter_turns(logs))) == 1

def test_dedup_across_files(write_log):
    e = make_entry()
    write_log([e], "a/s.jsonl")
    logs = write_log([e], "b/s.jsonl")
    assert len(list(iter_turns(logs))) == 1

def test_same_message_id_different_request_kept(write_log):
    logs = write_log([make_entry(request_id="r1"), make_entry(request_id="r2")])
    assert len(list(iter_turns(logs))) == 2

def test_missing_message_id_not_deduped(write_log):
    e = make_entry(msg_id=None)
    logs = write_log([e, e])
    assert len(list(iter_turns(logs))) == 2

def test_skips_non_assistant_bad_json_and_no_usage(write_log):
    no_usage = make_entry(msg_id="m2")
    del no_usage["message"]["usage"]
    logs = write_log([
        {"type": "user", "message": {"content": "hi"}},
        "{not json",
        no_usage,
        make_entry(msg_id="m3"),
    ])
    assert [t["model"] for t in iter_turns(logs)] == ["claude-sonnet-3"]

def test_null_token_counts_become_zero(write_log):
    logs = write_log([make_entry(usage={"input_tokens": None, "output_tokens": 5})])
    (t,) = iter_turns(logs)
    assert (t["in"], t["out"], t["cw"], t["cr"]) == (0, 5, 0, 0)

def test_skips_synthetic_messages(write_log):
    logs = write_log([make_entry(msg_id="a", model="<synthetic>"), make_entry(msg_id="b")])
    assert [t["msg_id"] for t in iter_turns(logs)] == ["b"]

def test_several_log_dirs_and_missing_ones(tmp_path, write_log):
    write_log([make_entry(msg_id="a")], "x/s.jsonl")
    other = tmp_path / "other"
    (other / "p").mkdir(parents=True)
    import json
    (other / "p" / "s.jsonl").write_text(json.dumps(make_entry(msg_id="b")) + "\n")
    dirs = [tmp_path / "logs", other, tmp_path / "does-not-exist"]
    assert sorted(t["msg_id"] for t in iter_turns(dirs)) == ["a", "b"]

def test_default_log_dirs_respects_env(monkeypatch, tmp_path):
    from agentcost.parse import default_log_dirs
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    dirs = default_log_dirs()
    assert dirs[0] == tmp_path / "projects"
    assert any(str(d).endswith(".claude/projects") for d in dirs)
