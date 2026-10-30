#!/usr/bin/env python3
"""Generate synthetic Claude Code-style session logs for testing agentcost.

Covers six months (2026-05 .. 2026-10) with a growing trend and an Acme
spike in August, so period comparisons have something to show.
"""
import json, random, shutil, uuid
from pathlib import Path

random.seed(42)
OUT = Path("sample_logs")
MONTHS = ["2026-05", "2026-06", "2026-07", "2026-08", "2026-09", "2026-10"]
projects = [
    ("/home/a/projects/acme-portal", "main"),
    ("/home/a/projects/acme-api", "feature/auth"),
    ("/home/a/projects/monorepo", "acme/billing"),
    ("/home/a/projects/swn-dashboard", "main"),
    ("/home/a/projects/internal-tools", "main"),
    ("/home/a/projects/oss-experiment", "main"),  # unassigned on purpose
]
models = ["claude-sonnet-4-5", "claude-sonnet-4-5", "claude-opus-4-1", "claude-haiku-4-5"]

shutil.rmtree(OUT, ignore_errors=True)
for cwd, branch in projects:
    d = OUT / cwd.strip("/").replace("/", "-")
    d.mkdir(parents=True, exist_ok=True)
    for mi, month in enumerate(MONTHS):
        sessions = random.randint(1, 2) + mi // 2  # usage grows over time
        if month == "2026-08" and "acme" in cwd + branch:
            sessions += 3  # release crunch
        for _ in range(sessions):
            sid = str(uuid.uuid4())
            day = random.randint(1, 28)
            with open(d / f"{sid}.jsonl", "w") as f:
                for turn in range(random.randint(5, 40)):
                    mid = f"msg_{uuid.uuid4().hex[:12]}"
                    e = {
                        "type": "assistant", "sessionId": sid, "cwd": cwd, "gitBranch": branch,
                        "requestId": f"req_{uuid.uuid4().hex[:8]}",
                        "timestamp": f"{month}-{day:02d}T{9 + turn % 9:02d}:{turn % 60:02d}:00Z",
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
