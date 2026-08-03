#!/usr/bin/env python3
"""verify.py — LH001 确定性验证（ADR-0009：验证标准必须确定性）。

检查：
1. parser.py 支持大小写不敏感 key 匹配（确定性函数测试）
2. tests/test_parser.py 包含大小写不敏感用例
3. pytest 全部通过（在 fixture 目录内运行）
4. README.md 提到新功能
"""
import subprocess
import sys
import os

FIXTURE = os.path.dirname(os.path.abspath(__file__))


def _read(path):
    p = os.path.join(FIXTURE, path)
    if not os.path.exists(p):
        return ""
    with open(p, encoding="utf-8") as f:
        return f.read()


def verify() -> tuple:
    """Returns (ok, reasons:list[str])"""
    reasons = []

    # 1. 大小写不敏感 + 保留首次拼写（确定性函数测试）
    parser_src = _read("parser.py")
    cfg = {}
    exec(parser_src, {}, cfg)
    parse_config = cfg["parse_config"]
    got = parse_config("HOST=localhost\nhost=override\nPort=8080\n")
    # 大小写不敏感：Host/host 应命中同一个 key（合并为一个）
    host_keys = [k for k in got if k.lower() == "host"]
    if len(host_keys) != 1:
        reasons.append(f"大小写不敏感失败: host_keys={host_keys}")
    elif host_keys[0] != "HOST":
        reasons.append(f"保留原始 key 拼写失败: {host_keys[0]}（期望 HOST）")

    # 2. 测试用例覆盖大小写不敏感
    test_src = _read("tests/test_parser.py")
    if "case" not in test_src.lower():
        reasons.append("tests/test_parser.py 未包含大小写不敏感用例")

    # 3. pytest 全部通过
    r = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "tests"],
        cwd=FIXTURE, capture_output=True, text=True, timeout=120,
    )
    if r.returncode != 0:
        reasons.append(f"pytest 未通过: {r.stdout[-200:]}")

    # 4. README 说明新功能
    readme = _read("README.md")
    if "case" not in readme.lower() and "大小写" not in readme:
        reasons.append("README.md 未说明大小写不敏感功能")

    return (len(reasons) == 0, reasons)


if __name__ == "__main__":
    ok, reasons = verify()
    print("PASS" if ok else "FAIL")
    for r in reasons:
        print("  -", r)
    sys.exit(0 if ok else 1)
