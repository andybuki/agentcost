"""Read Claude Code session logs. Only metadata is extracted — never prompt text."""
import json, os
from pathlib import Path

def default_log_dirs():
    """Where Claude Code may write session logs, most specific first."""
    dirs = [Path(d).expanduser() / "projects"
            for d in os.environ.get("CLAUDE_CONFIG_DIR", "").split(",") if d.strip()]
    dirs += [Path("~/.config/claude/projects").expanduser(), Path("~/.claude/projects").expanduser()]
    return list(dict.fromkeys(dirs))

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
    # Claude Code writes "<synthetic>" placeholder messages with zero usage.
    if not usage or msg.get("model") == "<synthetic>":
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
        # Part of "cw" written with the 1-hour TTL (priced 2x input instead of 1.25x).
        "cw1h": (usage.get("cache_creation") or {}).get("ephemeral_1h_input_tokens", 0) or 0,
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

def log_files(log_dirs):
    """All *.jsonl below one directory or a list of directories (missing ones skipped)."""
    if isinstance(log_dirs, (str, Path)):
        log_dirs = [log_dirs]
    files = set()
    for d in log_dirs:
        d = Path(d).expanduser()
        if d.is_dir():
            files.update(d.rglob("*.jsonl"))
    return sorted(files)

def iter_turns(log_dirs):
    seen = set()
    for f in log_files(log_dirs):
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
