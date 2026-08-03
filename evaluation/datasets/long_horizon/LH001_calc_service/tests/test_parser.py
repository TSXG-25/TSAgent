"""tests/test_parser.py —— 解析器测试（LH001 fixture，初始有一个基础用例）。"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from parser import parse_config


def test_basic_parse():
    cfg = parse_config("host=localhost\nport=8080\n")
    assert cfg == {"host": "localhost", "port": "8080"}


def test_comment_and_blank():
    cfg = parse_config("# comment\n\nhost=localhost\n")
    assert cfg == {"host": "localhost"}


def test_ignore_invalid_line():
    cfg = parse_config("no_equals_here\nport=8080\n")
    assert cfg == {"port": "8080"}