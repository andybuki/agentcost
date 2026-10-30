"""Read Claude Code session logs. Only metadata is extracted — never prompt text."""
import json
from pathlib import Path

def parse_line(line):
    """Return a turn dict for an assistant line with usage, else None."""
    try:
        e = json.loads(line)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    if not isinstance(e, dict) or e.get("type") != "assistant":
        return None
    msg = e.get("message") or {}
    usage = msg.get("usage")
    if not usage:
        return None
    return {
        "msg_id": msg.get("id"),
        "request_id": e.get("requestId"),
        "ts": e.get("timestamp", ""),
        "session": e.get("sessionId", ""),
        "cwd": e.get("cwd", ""),
        "branch": e.get("gitBranch", ""),
        "model": msg.get("model", "unknown"),
        "in": usage.get("input_tokens", 0) or 0,
        "out": usage.get("output_tokens", 0) or 0,
        "cw": usage.get("cache_creation_input_tokens", 0) or 0,
        "cr": usage.get("cache_read_input_tokens", 0) or 0,
    }

def read_file(path, offset=0):
    """Parse complete lines of a log file starting at byte `offset`.

    Returns (turns, new_offset). A trailing line without newline is left for the
    next read, since Claude Code may still be writing it. Each turn carries the
    byte offset of its line ("pos") so turns without a message id stay unique.
    """
    turns = []
    with open(path, "rb") as fh:
        fh.seek(offset)
        pos = offset
        for raw in fh:
            if not raw.endswith(b"\n"):
                break
            t = parse_line(raw)
            if t:
                t["pos"] = pos
                turns.append(t)
            pos += len(raw)
    return turns, pos

def log_files(log_dir):
    return sorted(Path(log_dir).expanduser().rglob("*.jsonl"))

def iter_turns(log_dir):
    seen = set()
    for f in log_files(log_dir):
        with open(f, encoding="utf-8") as fh:
            for line in fh:
                t = parse_line(line)
                if not t:
                    continue
                # Streaming can write the same message on several lines.
                key = (t["msg_id"], t["request_id"])
                if key[0] and key in seen:
                    continue
                seen.add(key)
                yield t
