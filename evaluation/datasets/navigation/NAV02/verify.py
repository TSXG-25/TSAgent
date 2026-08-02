#!/usr/bin/env python3
"""NAV verify: 目标文件 src/utils.py 已被 agent 定位（答案含路径）。"""
import sys
answer = ""
if len(sys.argv) > 1:
    try:
        answer = open(sys.argv[1]).read()
    except Exception:
        pass
ok = "src/utils.py" in answer
sys.exit(0 if ok else 1)
