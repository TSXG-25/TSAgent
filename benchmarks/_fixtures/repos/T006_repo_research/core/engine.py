# core/engine.py
"""核心分析引擎。"""
from .config import load_config


class Engine:
    """执行分析管线。"""

    def __init__(self):
        self.config = load_config()

    def analyze(self, data):
        results = {}
        for key, value in data.items():
            results[key] = value * self.config.get("factor", 1)
        return results
