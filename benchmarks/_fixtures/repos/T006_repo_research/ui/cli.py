# ui/cli.py
"""命令行入口。"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core import Engine
from data import load_csv


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else "data.csv"
    rows = load_csv(path)
    engine = Engine()
    result = engine.analyze({r["name"]: r["value"] for r in rows})
    for name, value in result.items():
        print(f"{name}: {value}")


if __name__ == "__main__":
    main()
