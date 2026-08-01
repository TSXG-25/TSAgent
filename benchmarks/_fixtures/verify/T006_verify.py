#!/usr/bin/env python3
"""T006 verify — 架构报告包含核心模块与数据流。

verify 读取 runner 写入的最终答案文件（argv[1]）。
"""
import sys
import os

answer = ""
if len(sys.argv) > 1 and os.path.exists(sys.argv[1]):
    with open(sys.argv[1]) as f:
        answer = f.read()

print(f"ANSWER_LEN={len(answer)}")
keywords = ["engine", "loader", "cli", "config"]
hits = [k for k in keywords if k in answer.lower()]
print(f"KEYWORD_HITS={hits}")

has_flow = any(k in answer.lower() for k in ("数据流", "流程", "cli", "engine", "loader"))
ok = len(answer) >= 100 and len(hits) >= 3 and has_flow
sys.exit(0 if ok else 1)
