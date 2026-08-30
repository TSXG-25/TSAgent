#!/usr/bin/env python3
"""Read-only preflight for v2.4B Selector-to-Runtime integration."""

from __future__ import annotations

import argparse
import ast
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
HARNESS_VERSION = "v2.4B-4b-runtime-integration-preflight-v1"


def _git_head() -> str:
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        text=True,
    ).strip()


def _selector_consumers() -> list[dict[str, Any]]:
    consumers: list[dict[str, Any]] = []
    for path in sorted((ROOT / "agent").rglob("*.py")):
        if path.name == "next_action_selector.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        relative = path.relative_to(ROOT).as_posix()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == "agent.next_action_selector":
                consumers.append({"file": relative, "line": node.lineno, "kind": "import"})
    return consumers


def _runtime_next_action_is_passthrough() -> bool:
    source = (ROOT / "agent" / "runtime.py").read_text(encoding="utf-8")
    marker = "elif rt_state == RuntimeState.NEXT_ACTION:"
    start = source.index(marker)
    end = source.index("elif rt_state == RuntimeState.OBSERVE:", start)
    branch = source[start:end]
    return (
        "rt_state = RuntimeState.EXECUTE" in branch
        and "select_with_evidence" not in branch
        and ".select(" not in branch
    )


def _tool_executor_executes_whole_plan() -> bool:
    source = (
        ROOT / "agent" / "executor" / "executors" / "tool.py"
    ).read_text(encoding="utf-8")
    return "result = await plan_executor.execute(" in source and "\n            plan," in source


def _compiler_has_multi_step_plan() -> bool:
    rules_root = ROOT / "agent" / "compiler" / "rules"
    for path in sorted(rules_root.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            function = node.func
            name = (
                function.id if isinstance(function, ast.Name)
                else function.attr if isinstance(function, ast.Attribute)
                else ""
            )
            if name != "ExecutionPlan":
                continue
            for keyword in node.keywords:
                if keyword.arg == "steps" and isinstance(keyword.value, ast.List):
                    if len(keyword.value.elts) > 1:
                        return True
    return False


def build_preflight_report() -> dict[str, Any]:
    consumers = _selector_consumers()
    runtime_passthrough = _runtime_next_action_is_passthrough()
    whole_plan = _tool_executor_executes_whole_plan()
    multi_step = _compiler_has_multi_step_plan()
    blockers: list[dict[str, str]] = []
    if not consumers:
        blockers.append({
            "code": "SELECTOR_NOT_CONSUMED_BY_RUNTIME",
            "category": "P-INT",
            "evidence": "No production module imports or calls NextActionSelector.",
        })
    if runtime_passthrough:
        blockers.append({
            "code": "NEXT_ACTION_STATE_IS_PASSTHROUGH",
            "category": "P-INT",
            "evidence": "Runtime NEXT_ACTION transitions directly to EXECUTE.",
        })
    if whole_plan and multi_step:
        blockers.append({
            "code": "NEXT_ACTION_EXECUTION_UNIT_MISMATCH",
            "category": "P-CON",
            "evidence": (
                "Selector returns one NextAction, while ToolExecutor executes a complete "
                "ExecutionPlan and production Compiler rules can emit multiple steps."
            ),
        })

    return {
        "harness_version": HARNESS_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "head": _git_head(),
        "status": "BLOCKED_PRECONDITION" if blockers else "READY_FOR_INTEGRATION",
        "configuration": {
            "provider_calls": 0,
            "tool_execution": False,
            "runtime_execution": False,
            "source_scan_only": True,
        },
        "discovery": {
            "selector_consumers": consumers,
            "runtime_next_action_passthrough": runtime_passthrough,
            "tool_executor_executes_whole_plan": whole_plan,
            "compiler_has_multi_step_plan": multi_step,
        },
        "blockers": blockers,
        "decision_required": (
            "Choose one authoritative execution unit before wiring: preserve "
            "Compiler-owned ExecutionPlan selection or migrate execution to "
            "one selected ExecutionStep per Runtime transition."
            if blockers else None
        ),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    serialized = json.dumps(
        build_preflight_report(),
        ensure_ascii=False,
        indent=2,
    ) + "\n"
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(serialized, encoding="utf-8")
    print(serialized, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
