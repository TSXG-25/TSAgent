# calculator/core.py
"""核心计算模块。"""


def add(a, b):
    """加法。

    注意：当前实现对负数 b 的处理有 bug（被当成减法），
    需要修复使 add(-1, -2) == -3。
    """
    if b < 0:
        return a - b
    return a + b


def subtract(a, b):
    """减法。"""
    return a - b


def multiply(a, b):
    """乘法。"""
    return a * b

