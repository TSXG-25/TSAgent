# tests/test_calc.py
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src import divide, parse_number


def test_divide_normal():
    assert divide(10, 2) == 5


def test_divide_by_zero():
    assert divide(1, 0) is None


def test_parse_number():
    assert parse_number("42") == 42
