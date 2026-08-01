# tests/test_add.py
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from calculator import add


def test_add_positive():
    assert add(1, 2) == 3


def test_add_negative():
    # 当前实现不能正确处理负数，需要修复 core.py
    assert add(-1, -2) == -3


def test_add_zero():
    assert add(0, 5) == 5
