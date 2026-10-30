import json
import pytest

CONFIG = """
currency_symbol = "EUR"
currency_rate = 0.5
default_markup = 1.0

[prices."claude-sonnet"]
input = 3.0
output = 15.0
cache_write = 3.75
cache_read = 0.3

[prices."claude-sonnet-4-5"]
input = 6.0
output = 30.0
cache_write = 7.5
cache_read = 0.6

[[client]]
name = "Acme"
cwd = ["/projects/acme-"]
branch = ["^acme/"]
markup = 1.5

[[client]]
name = "SWN"
cwd = ["/projects/swn-", "/projects/monorepo"]

[[client]]
name = "Internal"
cwd = ["/projects/internal"]
markup = 0
"""

@pytest.fixture
def config_path(tmp_path):
    p = tmp_path / "clients.toml"
    p.write_text(CONFIG)
    return p

def make_entry(msg_id="msg_1", request_id="req_1", model="claude-sonnet-3",
               cwd="/projects/acme-x", branch="main", ts="2026-10-01T10:00:00Z",
               session="s1", usage=None, **extra):
    e = {"type": "assistant", "sessionId": session, "cwd": cwd, "gitBranch": branch,
         "requestId": request_id, "timestamp": ts,
         "message": {"id": msg_id, "model": model,
                     "usage": usage if usage is not None else {
                         "input_tokens": 1_000_000, "output_tokens": 0,
                         "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}}}
    e.update(extra)
    return e

@pytest.fixture
def write_log(tmp_path):
    def _write(entries, name="proj/s.jsonl"):
        f = tmp_path / "logs" / name
        f.parent.mkdir(parents=True, exist_ok=True)
        with open(f, "a") as fh:
            for e in entries:
                fh.write((e if isinstance(e, str) else json.dumps(e)) + "\n")
        return tmp_path / "logs"
    return _write
