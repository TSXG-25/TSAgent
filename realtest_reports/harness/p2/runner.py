"""P2-L runner entry point.

The fixture mode validates the pipeline without external effects. The real
mode runs one fixed P2-L prompt per selected case through AgentService.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
from pathlib import Path

from .groups.long_horizon import run_fixture
from .groups.long_horizon_real import run_real_case
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
    parser.add_argument("--mode", choices=("fixture", "real"), default="fixture")
    parser.add_argument("--ids", default="", help="comma-separated L case IDs; default is all five")
    parser.add_argument("--snapshot", default=os.environ.get("TSAGENT_SNAPSHOT", ""))
    parser.add_argument(
        "--results",
        default=os.environ.get("TSAGENT_P2_L_RESULTS", "/private/tmp/p2_l_fixture.json"),
    )
    args = parser.parse_args()
    if args.mode == "fixture":
        results = run_fixture()
    else:
        ids = tuple(item.strip() for item in args.ids.split(",") if item.strip())
        if not ids:
            ids = ("L01", "L02", "L03", "L04", "L05")
        results = tuple(
            asyncio.run(
                run_real_case(
                    case_id,
                    snapshot=Path(args.snapshot) if args.snapshot else None,
                )
            )
            for case_id in ids
        )
    report = build_report(
        results,
        source=args.mode,
        commit=_commit(),
    )
    target = Path(args.results)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        f"P2-L {args.mode} run "
        f"({report['summary']['runtime_correctness_pass']}/{report['summary']['total']} Runtime; "
        f"results={target})"
    )
    if args.mode == "fixture":
        print("WARNING: fixture evidence is not real Runtime/Provider acceptance")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
