# src/calc.py
"""计算模块（含 bug：divide 除零时未处理）。"""


def divide(a, b):
    """除法。除零时应返回 None（当前实现会抛 ZeroDivisionError）。"""
    return a / b


def parse_number(text: str):
    """解析数字。"""
    return int(text)
