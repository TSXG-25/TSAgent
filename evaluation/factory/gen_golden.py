#!/usr/bin/env python3
"""生成 Golden Conversation（每次 Release 必跑，监控核心体验退化）。

Golden 比随机测试更珍贵：v1.0 → v1.1 的 Git Diff 直接看回答是否变好。
"""
import json
import os
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "golden"

GOLDEN = [
    {"id": "G001_greeting", "input": "你好",
     "expected": {"intent": "chat", "planner": False, "tool_calls": 0,
                  "answer_contains": ["你好"], "answer_not_contains": ["{", "搜索"]}},
    {"id": "G002_weather", "input": "今天天气怎么样",
     "expected": {"intent": "knowledge", "tool_calls": ">=1",
                  "answer_not_contains": ["{", "status"]}},
    {"id": "G003_read", "input": "读取 benchmarks/_fixtures/repos/T003_bug_fix/src/calc.py",
     "expected": {"intent": "file", "planner": True,
                  "answer_contains": ["divide"], "answer_not_contains": ["{"]}},
    {"id": "G004_love", "input": "我喜欢你",
     "expected": {"intent": "chat", "planner": False, "tool_calls": 0,
                  "answer_contains": ["谢谢", "高兴"], "answer_not_contains": ["查不到", "搜索", "{"]}},
    {"id": "G005_followup", "input": "那帮我修一下刚才那个文件",
     "expected": {"intent": "modify", "answer_not_contains": ["Traceback"]}},
]


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    for g in GOLDEN:
        (OUT / f"{g['id']}.json").write_text(
            json.dumps(g, ensure_ascii=False, indent=2), encoding="utf-8")
    print("golden:", len(GOLDEN))


if __name__ == "__main__":
    main()
