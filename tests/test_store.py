import os
from agentcost.store import connect, ingest, load_turns
from conftest import make_entry

def test_ingest_is_idempotent(tmp_path, write_log):
    logs = write_log([make_entry(msg_id="a"), make_entry(msg_id="a"), make_entry(msg_id="b")])
    con = connect(tmp_path / "t.db")
    assert ingest(con, logs) == 2
    assert ingest(con, logs) == 0
    assert len(load_turns(con)) == 2

def test_incremental_append(tmp_path, write_log):
    logs = write_log([make_entry(msg_id="a")])
    con = connect(tmp_path / "t.db")
    ingest(con, logs)
    write_log([make_entry(msg_id="b")])
    assert ingest(con, logs) == 1
    assert [t["msg_id"] for t in load_turns(con)] == ["a", "b"]

def test_partial_last_line_waits(tmp_path, write_log):
    import json
    logs = write_log([make_entry(msg_id="a")])
    f = logs / "proj/s.jsonl"
    line = json.dumps(make_entry(msg_id="b"))
    with open(f, "a") as fh:
        fh.write(line[:20])  # still being written
    con = connect(tmp_path / "t.db")
    assert ingest(con, logs) == 1
    with open(f, "a") as fh:
        fh.write(line[20:] + "\n")
    assert ingest(con, logs) == 1

def test_history_survives_log_deletion(tmp_path, write_log):
    logs = write_log([make_entry(msg_id="a")])
    con = connect(tmp_path / "t.db")
    ingest(con, logs)
    os.remove(logs / "proj/s.jsonl")
    ingest(con, logs)
    assert len(load_turns(con)) == 1

def test_rewritten_file_is_reread(tmp_path, write_log):
    logs = write_log([make_entry(msg_id="a"), make_entry(msg_id="b")])
    con = connect(tmp_path / "t.db")
    ingest(con, logs)
    (logs / "proj/s.jsonl").write_text("")
    write_log([make_entry(msg_id="c")])  # shorter than before
    assert ingest(con, logs) == 1

def test_turns_without_message_id_stay_distinct(tmp_path, write_log):
    e = make_entry(msg_id=None)
    logs = write_log([e, e])
    con = connect(tmp_path / "t.db")
    assert ingest(con, logs) == 2
    assert ingest(con, logs) == 0
