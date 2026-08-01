#!/usr/bin/env python3
"""T002 verify — main.py 输出 Hello, TSAgent!"""
import subprocess
import sys
import os

REPO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "repos", "T002_env_recovery")

r = subprocess.run(
    [sys.executable, "main.py"],
    cwd=REPO, capture_output=True, text=True, timeout=60,
)
ok = "Hello, TSAgent" in r.stdout
print("STDOUT:", r.stdout[-300:])
print("STDERR:", r.stderr[-300:])
sys.exit(0 if ok else 1)
