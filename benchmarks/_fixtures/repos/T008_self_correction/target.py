# target.py
"""目标模块：compute 函数。

陷阱：如果只处理正数输入，test_compute_negative 会继续失败。
需要同时正确处理 0、正数、负数三个分支。
"""


def compute(x):
    """按规则计算。

    规则：
    - x > 0  → x * 2
    - x == 0 → 0
    - x < 0  → x * 2（与正数规则一致，但实现时容易漏掉此分支）
    """
    if x > 0:
        return x * 2
    return 0  # 错误：负数也应返回 x * 2
