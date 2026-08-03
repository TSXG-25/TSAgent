"""LH001 计算服务 —— Long Horizon Benchmark fixture（v2.0-A）。

结构：main.py（入口）→ parser.py（配置解析）→ utils.py（工具）。
目标：增加大小写不敏感的配置解析 + 测试 + 文档。
"""


def load_config(path="config.txt"):
    from parser import parse_config
    from utils import to_dict
    raw = open(path, encoding="utf-8").read()
    return to_dict(parse_config(raw))
