"""CLI validator for an H8 JSON result file."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .oracle import validate_records


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("results", type=Path)
    args = parser.parse_args()
    payload = json.loads(args.results.read_text(encoding="utf-8"))
    records = payload.get("cases", payload) if isinstance(payload, dict) else payload
    result = validate_records(list(records))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
