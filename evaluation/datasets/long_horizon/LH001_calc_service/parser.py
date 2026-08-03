"""parser.py —— 配置解析（LH001 Long Horizon fixture）。

当前只支持精确大小写的 key。任务：增加大小写不敏感支持。
"""


def parse_config(raw: str) -> dict:
    """解析 "key=value" 行配置。空行/注释(#)跳过。"""
    result = {}
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        result[key.strip()] = value.strip()
    return result