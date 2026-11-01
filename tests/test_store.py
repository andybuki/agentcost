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

def test_one_hour_share_stored(tmp_path, write_log):
    e = make_entry(usage={"input_tokens": 1, "output_tokens": 1, "cache_creation_input_tokens": 300,
                          "cache_read_input_tokens": 0,
                          "cache_creation": {"ephemeral_5m_input_tokens": 100, "ephemeral_1h_input_tokens": 200}})
    con = connect(tmp_path / "t.db")
    ingest(con, write_log([e]))
    (t,) = load_turns(con)
    assert (t["cw"], t["cw1h"]) == (300, 200)

def test_old_store_is_migrated_and_backfilled(tmp_path, write_log):
    import sqlite3
    e = make_entry(usage={"input_tokens": 1, "output_tokens": 1, "cache_creation_input_tokens": 300,
                          "cache_read_input_tokens": 0, "cache_creation": {"ephemeral_1h_input_tokens": 300}})
    logs = write_log([e])
    db = tmp_path / "old.db"
    con = sqlite3.connect(db)  # store layout before 1-hour pricing
    con.executescript("""
      CREATE TABLE turns (msg_id TEXT NOT NULL, request_id TEXT NOT NULL, ts TEXT NOT NULL,
        session TEXT, cwd TEXT, branch TEXT, model TEXT, tok_in INTEGER NOT NULL DEFAULT 0,
        tok_out INTEGER NOT NULL DEFAULT 0, cache_write INTEGER NOT NULL DEFAULT 0,
        cache_read INTEGER NOT NULL DEFAULT 0, source_file TEXT, ingested_at TEXT,
        source TEXT NOT NULL DEFAULT 'local', PRIMARY KEY (msg_id, request_id));
      CREATE TABLE files (path TEXT PRIMARY KEY, size INTEGER, mtime REAL, offset INTEGER);
    """)
    con.execute("INSERT INTO turns VALUES ('msg_1','req_1','2026-10-01T10:00:00Z','s1','/p','main','m',1,1,300,0,'f','t','local')")
    f = logs / "proj/s.jsonl"
    st = f.stat()
    con.execute("INSERT INTO files VALUES (?,?,?,?)", (str(f), st.st_size, st.st_mtime, st.st_size))
    con.commit(); con.close()
    con = connect(db)
    assert ingest(con, logs) == 0          # no new turns ...
    (t,) = load_turns(con)
    assert t["cw1h"] == 300                # ... but the 1-hour share is filled in
