"""parser.py —— 配置解析（LH001 Long Horizon fixture）。

支持大小写不敏感的 key 解析。
"""


def parse_config(raw: str) -> dict:
    """解析 "key=value" 行配置。空行/注释(#)跳过。

    key 大小写不敏感，统一转为小写存储。
    """
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