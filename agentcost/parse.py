"""Read Claude Code session logs. Only metadata is extracted — never prompt text."""
import json
from pathlib import Path

def iter_turns(log_dir):
    seen = set()
    for f in sorted(Path(log_dir).expanduser().rglob("*.jsonl")):
        with open(f, encoding="utf-8") as fh:
            for line in fh:
                try:
                    e = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if e.get("type") != "assistant":
                    continue
                msg = e.get("message") or {}
                usage = msg.get("usage")
                if not usage:
                    continue
                # Streaming can write the same message on several lines.
                key = (msg.get("id"), e.get("requestId"))
                if key[0] and key in seen:
                    continue
                seen.add(key)
                yield {
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
