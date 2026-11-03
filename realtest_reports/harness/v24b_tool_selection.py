#!/usr/bin/env python3
"""Measurement-only preflight for the v2.4B Tool Selection baseline.

The v2.4B baseline must call a production ``Task + State + Observation``
selector.  This preflight deliberately performs no Provider call, tool
execution, output repair, or fallback.  It fails closed when that production
entry point is not present, so a Planner or an executor label cannot be
silently measured as Tool Selection.
"""

from __future__ import annotations

import argparse
import ast
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
DATASET_PATH = ROOT / "evals" / "tool_selection" / "dataset.json"
EXPECTED_DATASET_HASH = (
    "bc0baa5afcf68ba68a787387edd7297a4c22bea6334e1e0afd06c61136952409"
)
HARNESS_VERSION = "v2.4B-2-real-tool-selection-preflight-v1"
SELECTOR_SYMBOLS = frozenset(
    {
        "select_next_action",
        "choose_next_action",
        "NextActionSelector",
    }
)


def _load_oracle():
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from evals.tool_selection.oracle import dataset_hash, load_dataset

    payload = load_dataset(DATASET_PATH)
    return payload, dataset_hash(payload)


def _git_head() -> str | None:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=ROOT,
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def _scan_production_selector() -> dict[str, Any]:
    """Inspect production source without importing runtime/provider modules."""

    found: list[dict[str, Any]] = []
    construction_sites: list[dict[str, Any]] = []
    agent_root = ROOT / "agent"
    for path in sorted(agent_root.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (OSError, SyntaxError):
            continue
        relative = str(path.relative_to(ROOT))
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                if node.name in SELECTOR_SYMBOLS:
                    found.append(
                        {
                            "symbol": node.name,
                            "kind": type(node).__name__,
                            "file": relative,
                            "line": node.lineno,
                        }
                    )
            if not isinstance(node, ast.Call):
                continue
            function = node.func
            if (
                isinstance(function, ast.Attribute)
                and function.attr == "tool_call"
                and isinstance(function.value, ast.Name)
                and function.value.id == "NextAction"
            ):
                construction_sites.append(
                    {
                        "file": relative,
                        "line": node.lineno,
                        "kind": "recording_or_projection_only",
                    }
                )
    return {
        "selector_symbols": found,
        "next_action_construction_sites": construction_sites,
        "entry_point_found": bool(found),
        "scan_mode": "AST; no production imports",
    }


def build_preflight_report() -> dict[str, Any]:
    """Build a truthful B-2 precondition report without calling a Provider."""

    dataset, actual_hash = _load_oracle()
    discovery = _scan_production_selector()
    cases = dataset["cases"]
    if discovery["entry_point_found"]:
        status = "READY_FOR_REAL_BASELINE"
        blocker = None
        case_reports: list[dict[str, Any]] = []
    else:
        status = "BLOCKED_PRECONDITION"
        blocker = {
            "code": "PRODUCTION_NEXT_ACTION_SELECTOR_MISSING",
            "category": "P-INT",
            "message": (
                "No production selector entry point accepts Task + projected "
                "State + Observation and returns one NextAction."
            ),
            "evidence": (
                "Production source contains NextAction.tool_call construction "
                "sites, but no select_next_action, choose_next_action, or "
                "NextActionSelector symbol."
            ),
        }
        case_reports = [
            {
                "case_id": str(case["id"]),
                "case_result": "NOT_EVALUABLE",
                "evaluable": False,
                "provider_status": "NOT_CALLED",
                "raw_provider_output": None,
                "normalized_next_action": None,
                "oracle_result": None,
                "failure_category": "P-INT",
                "failure_subcategory": "PRODUCTION_SELECTOR_MISSING",
                "evidence": [blocker["code"]],
            }
            for case in cases
        ]

    return {
        "harness_version": HARNESS_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "git_head": _git_head(),
        "dataset": {
            "path": str(DATASET_PATH.relative_to(ROOT)),
            "version": dataset["version"],
            "case_count": len(cases),
            "expected_hash": EXPECTED_DATASET_HASH,
            "actual_hash": actual_hash,
            "hash_match": actual_hash == EXPECTED_DATASET_HASH,
        },
        "configuration": {
            "automatic_retry": False,
            "provider_fallback": False,
            "golden_repair": False,
            "tool_execution": False,
            "production_imports": False,
        },
        "production_selector_discovery": discovery,
        "status": status,
        "blocker": blocker,
        "provider": {
            "calls": 0,
            "status": "NOT_CALLED",
            "fallback": False,
        },
        "metrics": None,
        "attribution": {"P-INT": len(cases)} if blocker else {},
        "case_reports": case_reports,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="write the JSON preflight report")
    args = parser.parse_args(argv)
    report = build_preflight_report()
    serialized = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(serialized, encoding="utf-8")
    print(serialized, end="")
    return 0 if report["status"] == "READY_FOR_REAL_BASELINE" else 2


if __name__ == "__main__":
    raise SystemExit(main())
