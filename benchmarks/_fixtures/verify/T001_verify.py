#!/usr/bin/env python3
"""T001 verify — tests/test_add.py 全部通过。"""
import subprocess
import sys
import os

REPO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "repos", "T001_file_location")

r = subprocess.run(
    [sys.executable, "-m", "pytest", "tests/test_add.py", "-q", "--no-header"],
    cwd=REPO, capture_output=True, text=True, timeout=60,
)
print(r.stdout[-500:] if r.stdout else "")
print(r.stderr[-500:] if r.stderr else "")
sys.exit(0 if r.returncode == 0 else 1)
