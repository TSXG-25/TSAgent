# core/config.py
"""配置加载。"""
import json


def load_config(path="config.json"):
    try:
        with open(path) as f:
            return json.load(f)
    except FileNotFoundError:
        return {"factor": 2}
