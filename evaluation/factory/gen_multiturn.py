#!/usr/bin/env python3
"""生成多轮 Conversation Dataset（v1.2A Context Resolution 评估用）。"""
import json
import os
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "datasets" / "conversation" / "multiturn"

SCENARIOS = [
    {
        "id": "MT001_weather_followup",
        "turns": [
            {"input": "杭州天气怎么样", "expected": {"target": "杭州", "topic": "weather"}},
            {"input": "上海呢", "expected": {"target": "上海", "topic": "weather"}},
            {"input": "那广州呢", "expected": {"target": "广州", "topic": "weather"}},
            {"input": "谢谢", "expected": {"target": "", "topic": "chat"}},
        ],
    },
    {
        "id": "MT002_modify_continue",
        "turns": [
            {"input": "读取 output/solution.py", "expected": {"target": "output/solution.py", "topic": "file"}},
            {"input": "那改一下", "expected": {"target": "output/solution.py", "topic": "modify"}},
            {"input": "继续", "expected": {"target": "output/solution.py", "topic": "modify"}},
        ],
    },
    {
        "id": "MT003_symbol_reference",
        "turns": [
            {"input": "解释一下 ExecutionOrchestrator", "expected": {"target": "ExecutionOrchestrator", "topic": "symbol"}},
            {"input": "那个函数呢", "expected": {"target": "ExecutionOrchestrator", "topic": "symbol"}},
        ],
    },
    {
        "id": "MT004_plain",
        "turns": [
            {"input": "你好", "expected": {"target": "", "topic": "chat"}},
            {"input": "介绍一下自己", "expected": {"target": "", "topic": "chat"}},
            {"input": "谢谢", "expected": {"target": "", "topic": "chat"}},
        ],
    },
]


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    for s in SCENARIOS:
        (OUT / f"{s['id']}.json").write_text(
            json.dumps(s, ensure_ascii=False, indent=2), encoding="utf-8")
    print("multiturn scenarios:", len(SCENARIOS))


if __name__ == "__main__":
    main()
