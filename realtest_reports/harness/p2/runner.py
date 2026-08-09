"""P2-L runner entry point.

Only the deterministic fixture mode is implemented in this slice. The real
Provider adapter will be added after the evidence pipeline is reviewed.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
from pathlib import Path

from .groups.long_horizon import run_fixture
from .report import build_report


def _commit() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], text=True, stderr=subprocess.DEVNULL
        ).strip()
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def main() -> int:
    parser = argparse.ArgumentParser(description="P2-L long-horizon harness")
    parser.add_argument("--mode", choices=("fixture",), default="fixture")
    parser.add_argument(
        "--results",
        default=os.environ.get("TSAGENT_P2_L_RESULTS", "/private/tmp/p2_l_fixture.json"),
    )
    args = parser.parse_args()
    results = run_fixture()
    report = build_report(
        results,
        source="fixture",
        commit=_commit(),
    )
    target = Path(args.results)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        "P2-L fixture PASS "
        f"({report['summary']['runtime_correctness_pass']}/{report['summary']['total']} Runtime; "
        f"results={target})"
    )
    print("WARNING: fixture evidence is not real Runtime/Provider acceptance")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
