#!/usr/bin/env python3
"""Generate synthetic Claude Code-style session logs for testing agentcost."""
import json, random, uuid
from pathlib import Path

random.seed(42)
OUT = Path("sample_logs")
projects = [
    ("/home/a/projects/acme-portal", "main"),
    ("/home/a/projects/acme-api", "feature/auth"),
    ("/home/a/projects/monorepo", "acme/billing"),
    ("/home/a/projects/swn-dashboard", "main"),
    ("/home/a/projects/internal-tools", "main"),
    ("/home/a/projects/oss-experiment", "main"),  # unassigned on purpose
]
models = ["claude-sonnet-4-5", "claude-sonnet-4-5", "claude-opus-4-1", "claude-haiku-4-5"]

for cwd, branch in projects:
    d = OUT / cwd.strip("/").replace("/", "-")
    d.mkdir(parents=True, exist_ok=True)
    for _ in range(random.randint(2, 6)):
        sid = str(uuid.uuid4())
        day = random.randint(1, 30)
        with open(d / f"{sid}.jsonl", "w") as f:
            for turn in range(random.randint(5, 40)):
                mid = f"msg_{uuid.uuid4().hex[:12]}"
                e = {
                    "type": "assistant", "sessionId": sid, "cwd": cwd, "gitBranch": branch,
                    "requestId": f"req_{uuid.uuid4().hex[:8]}",
                    "timestamp": f"2026-10-{day:02d}T{9 + turn % 9:02d}:{turn % 60:02d}:00Z",
                    "message": {
                        "id": mid, "model": random.choice(models),
                        "usage": {
                            "input_tokens": random.randint(5, 3000),
                            "output_tokens": random.randint(100, 4000),
                            "cache_creation_input_tokens": random.randint(0, 20000),
                            "cache_read_input_tokens": random.randint(0, 120000),
                        },
                    },
                }
                f.write(json.dumps(e) + "\n")
                if random.random() < 0.2:  # simulate duplicated streaming line
                    f.write(json.dumps(e) + "\n")
print("sample logs written to", OUT)
