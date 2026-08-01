# utils.py
"""数据处理模块：handle_data 是一个大函数，需要拆分为小函数。

拆分要求：
- parse_row(row): str -> dict（解析 CSV 行）
- transform_row(row: dict) -> dict（转换字段）
- validate_row(row: dict) -> bool（校验必填字段）
- handle_data(data: list) -> list（编排上述函数）

拆分后 tests/test_utils.py 必须全部通过（行为不变）。
"""


def handle_data(data):
    """处理 CSV 数据行列表，返回处理后的 dict 列表。"""
    result = []
    for line in data:
        parts = line.strip().split(",")
        if len(parts) >= 2:
            name, age = parts[0], parts[1]
            age = int(age)
            if age < 0:
                age = 0
            result.append({"name": name, "age": age})
    return result
