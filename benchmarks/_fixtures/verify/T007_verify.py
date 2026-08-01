#!/usr/bin/env python3
"""T007 verify — response.txt 包含服务返回的 JSON。

要求 agent 把服务响应写入 response.txt，verify 检查内容。
"""
import json
import os
import sys

REPO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "repos", "T007_devops_serve")
resp_path = os.path.join(REPO, "response.txt")

if not os.path.exists(resp_path):
    print("response.txt missing")
    sys.exit(1)

content = open(resp_path).read().strip()
print(f"RESPONSE: {content[:200]}")

try:
    obj = json.loads(content)
    ok = obj.get("service") == "tsagent-bench" and obj.get("status") == "ok"
except Exception as e:
    print(f"not json: {e}")
    ok = False

sys.exit(0 if ok else 1)
