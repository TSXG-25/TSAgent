# tests/test_target.py
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from target import compute


def test_compute_positive():
    assert compute(2) == 4


def test_compute_zero():
    assert compute(0) == 0


def test_compute_negative():
    # 最容易漏掉的边界：负数
    assert compute(-1) == -2
    assert compute(-3) == -6
