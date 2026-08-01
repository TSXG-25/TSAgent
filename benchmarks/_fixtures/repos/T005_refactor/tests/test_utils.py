# tests/test_utils.py
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from utils import handle_data


def test_normal_rows():
    data = ["alice,30", "bob,25"]
    result = handle_data(data)
    assert result == [{"name": "alice", "age": 30}, {"name": "bob", "age": 25}]


def test_negative_age_clamped():
    data = ["carol,-5"]
    result = handle_data(data)
    assert result == [{"name": "carol", "age": 0}]


def test_malformed_row_skipped():
    data = ["malformed", "dave,40"]
    result = handle_data(data)
    assert result == [{"name": "dave", "age": 40}]


def test_empty_input():
    assert handle_data([]) == []
