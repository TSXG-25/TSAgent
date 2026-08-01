#!/usr/bin/env python3
"""T004 verify — server.py 有 /health 端点且 / 行为不变。

独立启动服务实例，请求两个端点，验证 JSON 结构。
"""
import json
import subprocess
import sys
import os
import time
import urllib.request

REPO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "repos", "T004_feature_add")
PORT = 8141

proc = subprocess.Popen(
    [sys.executable, "server.py"],
    cwd=REPO, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
)
try:
    # 等待服务就绪
    for _ in range(20):
        time.sleep(0.5)
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{PORT}/", timeout=2)
            break
        except Exception:
            continue
    else:
        print("server did not start")
        sys.exit(1)

    # 检查 /health
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/health", timeout=5) as resp:
            health = json.loads(resp.read().decode())
            ok = health.get("status") == "ok"
    except Exception as e:
        print(f"/health failed: {e}")
        ok = False

    # 检查 / 行为不变
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/", timeout=5) as resp:
            root = json.loads(resp.read().decode())
            ok = ok and root.get("message") == "hello"
    except Exception as e:
        print(f"/ failed: {e}")
        ok = False

    sys.exit(0 if ok else 1)
finally:
    proc.terminate()
    try:
        proc.wait(timeout=3)
    except Exception:
        proc.kill()
