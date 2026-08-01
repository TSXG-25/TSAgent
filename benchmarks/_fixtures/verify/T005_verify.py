#!/usr/bin/env python3
"""T005 verify — 测试全绿 + 确实拆分了函数。"""
import ast
import subprocess
import sys
import os

REPO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "repos", "T005_refactor")

r = subprocess.run(
    [sys.executable, "-m", "pytest", "tests/test_utils.py", "-q", "--no-header"],
    cwd=REPO, capture_output=True, text=True, timeout=60,
)
print(r.stdout[-500:] if r.stdout else "")
tests_ok = r.returncode == 0

# 检查 utils.py 是否拆分为 parse_row / transform_row / validate_row
try:
    src = open(os.path.join(REPO, "utils.py")).read()
    tree = ast.parse(src)
    funcs = {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
    split_ok = {"parse_row", "transform_row", "validate_row", "handle_data"} <= funcs
except Exception as e:
    print(f"parse failed: {e}")
    split_ok = False

print("tests_ok:", tests_ok, "split_ok:", split_ok)
sys.exit(0 if (tests_ok and split_ok) else 1)
