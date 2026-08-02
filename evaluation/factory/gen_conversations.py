#!/usr/bin/env python3
"""生成 E2E Conversation Dataset（20 条，含 Negative Assertion）。

每个 conversation：input + expected{intent/planner/tool_calls/answer_contains/answer_not_contains}
"""
import json
import os
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "datasets" / "conversation" / "chat"

CONVERSATIONS = [
    {"id": "001_greeting", "input": "你好",
     "expected": {"intent": "chat", "planner": False, "tool_calls": 0,
                  "answer_contains": ["你好"], "answer_not_contains": ["搜索", "查不到", "{"]}},
    {"id": "002_hi", "input": "嗨，在吗",
     "expected": {"intent": "chat", "planner": False, "tool_calls": 0,
                  "answer_contains": ["在", "你好"], "answer_not_contains": ["搜索", "{"]}},
    {"id": "003_thanks", "input": "谢谢",
     "expected": {"intent": "chat", "planner": False, "tool_calls": 0,
                  "answer_contains": ["不客气"], "answer_not_contains": ["查不到", "搜索", "{"]}},
    {"id": "004_laugh", "input": "哈哈哈哈哈",
     "expected": {"intent": "chat", "planner": False, "tool_calls": 0,
                  "answer_contains": ["哈哈"], "answer_not_contains": ["搜索", "{"]}},
    {"id": "005_farewell", "input": "晚安",
     "expected": {"intent": "chat", "planner": False, "tool_calls": 0,
                  "answer_contains": ["晚安", "好梦"], "answer_not_contains": ["搜索", "{"]}},
    {"id": "006_bye", "input": "再见",
     "expected": {"intent": "chat", "planner": False, "tool_calls": 0,
                  "answer_contains": ["再见", "下次"], "answer_not_contains": ["搜索", "{"]}},
    {"id": "007_identity", "input": "你是谁",
     "expected": {"intent": "chat", "planner": False, "tool_calls": 0,
                  "answer_contains": ["助手", "可以"], "answer_not_contains": ["查不到", "搜索", "{"]}},
    {"id": "008_capability", "input": "你会做什么",
     "expected": {"intent": "chat", "planner": False, "tool_calls": 0,
                  "answer_contains": ["可以", "帮助", "文件"], "answer_not_contains": ["搜索", "{"]}},
    {"id": "009_love", "input": "我喜欢你",
     "expected": {"intent": "chat", "planner": False, "tool_calls": 0,
                  "answer_contains": ["谢谢"], "answer_not_contains": ["查不到", "搜索", "{"]}},
    {"id": "010_celebrity", "input": "你认识蔡徐坤吗",
     "expected": {"intent": "chat", "planner": False, "tool_calls": 0,
                  "answer_contains": ["认识", "歌手", "不知道"], "answer_not_contains": ["搜索", "{"]}},
    {"id": "011_weather", "input": "今天天气怎么样",
     "expected": {"intent": "knowledge", "tool_calls": ">=1",
                  "answer_not_contains": ["{", "status"]}},
    {"id": "012_weather_city", "input": "杭州天气",
     "expected": {"intent": "knowledge", "tool_calls": ">=1",
                  "answer_contains": ["杭州"], "answer_not_contains": ["{", "status"]}},
    {"id": "013_followup_modify", "input": "那改一下",
     "expected": {"intent": "modify", "planner": True,
                  "answer_not_contains": ["Traceback", "Error"]}},
    {"id": "014_read_file", "input": "读取 output/solution.py",
     "expected": {"intent": "file", "planner": True, "tool_calls": ">=1",
                  "answer_contains": ["solution"], "answer_not_contains": ["{"]}},
    {"id": "015_explain_followup", "input": "解释一下刚才的",
     "expected": {"intent": "explain", "answer_not_contains": ["Traceback"]}},
    {"id": "016_poem", "input": "给我写一首关于春天的诗",
     "expected": {"intent": "creation", "planner": False,
                  "answer_contains": ["春天"], "answer_not_contains": ["{"]}},
    {"id": "017_translate", "input": "翻译 hello 到中文",
     "expected": {"intent": "translate", "planner": False,
                  "answer_contains": ["你好"], "answer_not_contains": ["{"]}},
    {"id": "018_math", "input": "帮我算一下 15+27",
     "expected": {"intent": "math", "planner": False,
                  "answer_contains": ["42"], "answer_not_contains": ["{"]}},
    {"id": "019_search", "input": "搜索人工智能最新进展",
     "expected": {"intent": "knowledge", "tool_calls": ">=1",
                  "answer_not_contains": ["{"]}},
    {"id": "020_repeat", "input": "没看懂，再解释一遍",
     "expected": {"intent": "explain", "answer_not_contains": ["Traceback"]}},
]


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    for c in CONVERSATIONS:
        p = OUT / f"{c['id']}.json"
        p.write_text(json.dumps(c, ensure_ascii=False, indent=2), encoding="utf-8")
    print("conversations:", len(CONVERSATIONS))


if __name__ == "__main__":
    main()
