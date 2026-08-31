#!/usr/bin/env python3
"""Read-only preflight for the v2.4C Workflow capability boundary."""

from __future__ import annotations

import argparse
import ast
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
HARNESS_VERSION = "v2.4C-2a-workflow-preflight-v2"


def _git_head() -> str:
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        text=True,
    ).strip()


def _production_selector_symbols() -> list[dict[str, Any]]:
    symbols: list[dict[str, Any]] = []
    names = {
        "WorkflowDecisionSelector",
        "WorkflowSelector",
        "select_workflow_decision",
        "choose_workflow",
    }
    for path in sorted((ROOT / "agent").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.name in names:
                    symbols.append({
                        "file": path.relative_to(ROOT).as_posix(),
                        "line": node.lineno,
                        "symbol": node.name,
                    })
    return symbols


def _selector_has_narrow_signature() -> bool:
    path = ROOT / "agent" / "workflow_selector.py"
    if not path.exists():
        return False
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef) or node.name != "WorkflowDecisionSelector":
            continue
        for child in node.body:
            if isinstance(child, ast.AsyncFunctionDef) and child.name == "select":
                arguments = [argument.arg for argument in child.args.args]
                return arguments == [
                    "self", "goal", "context", "available_workflows",
                ]
    return False


def _selector_preserves_boundaries() -> bool:
    path = ROOT / "agent" / "workflow_selector.py"
    if not path.exists():
        return False
    source = path.read_text(encoding="utf-8")
    forbidden = (
        "agent.planner",
        "agent.executor",
        "agent.workflow.registry",
        "agent.checkpoint",
        "agent.runtime_store",
        "agent.workspace",
    )
    return not any(token in source for token in forbidden)


def _router_is_intent_only() -> bool:
    path = ROOT / "agent" / "router" / "workflow_router.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "route":
            arguments = [argument.arg for argument in node.args.args]
            return arguments == ["self", "intent"]
    return False


def _planner_hardcodes_workflow_bindings() -> bool:
    source = (ROOT / "agent" / "orchestrator" / "planner.py").read_text(
        encoding="utf-8"
    )
    return all(token in source for token in (
        'type="question_path"',
        'id="output-path"',
        "_extract_workflow_output_path(user_input)",
    ))


def _registered_workflow_definitions() -> list[str]:
    values: list[str] = []
    for path in sorted((ROOT / "workflows").glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            function = node.func
            if (
                isinstance(function, ast.Attribute)
                and function.attr == "register"
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)
            ):
                values.append(node.args[0].value)
    return sorted(set(values))


def _workflow_executor_uses_canonical_task_path() -> bool:
    source = (
        ROOT / "agent" / "executor" / "executors" / "workflow.py"
    ).read_text(encoding="utf-8")
    return all(token in source for token in (
        "task = stage.to_task(",
        "self._compiler.compile(",
        "executor_factory.get(plan.executor)",
    ))


def _resume_policy_is_separate() -> bool:
    return all((ROOT / path).exists() for path in (
        "agent/run_resume/coordinator.py",
        "agent/run_resume/resolver.py",
        "agent/checkpoint/validator.py",
    ))


def build_preflight_report() -> dict[str, Any]:
    selectors = _production_selector_symbols()
    narrow_signature = _selector_has_narrow_signature()
    selector_boundaries = _selector_preserves_boundaries()
    intent_only_router = _router_is_intent_only()
    hardcoded_bindings = _planner_hardcodes_workflow_bindings()
    registered = _registered_workflow_definitions()
    canonical_execution = _workflow_executor_uses_canonical_task_path()
    separate_resume = _resume_policy_is_separate()
    blockers: list[dict[str, str]] = []
    if not selectors:
        blockers.append({
            "code": "PRODUCTION_WORKFLOW_SELECTOR_MISSING",
            "category": "P-INT",
            "evidence": (
                "No production entry consumes Goal + projected Context + "
                "AvailableWorkflows and returns WorkflowDecision."
            ),
        })
    if selectors and not narrow_signature:
        blockers.append({
            "code": "PRODUCTION_WORKFLOW_SELECTOR_SIGNATURE_DRIFT",
            "category": "P-INT",
            "evidence": "WorkflowDecisionSelector does not expose the frozen narrow input.",
        })
    if selectors and not selector_boundaries:
        blockers.append({
            "code": "PRODUCTION_WORKFLOW_SELECTOR_BOUNDARY_DRIFT",
            "category": "P-INT",
            "evidence": "WorkflowDecisionSelector imports a forbidden execution/state owner.",
        })

    return {
        "harness_version": HARNESS_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "head": _git_head(),
        "status": "BLOCKED_PRECONDITION" if blockers else "READY_FOR_REAL_BASELINE",
        "configuration": {
            "provider_calls": 0,
            "workflow_execution": False,
            "runtime_mutation": False,
            "source_scan_only": True,
        },
        "discovery": {
            "production_selector_symbols": selectors,
            "production_selector_narrow_signature": narrow_signature,
            "production_selector_boundaries_preserved": selector_boundaries,
            "workflow_router_input": "IntentResult" if intent_only_router else "unknown",
            "planner_hardcoded_workflow_bindings": hardcoded_bindings,
            "registered_workflows": registered,
            "workflow_executor_canonical_task_path": canonical_execution,
            "resume_policy_separate": separate_resume,
        },
        "blockers": blockers,
        "integration_watchlist": ([{
            "code": "WORKFLOW_INSTANTIATION_BOUNDARY_HARDCODED",
            "category": "P-CON",
            "evidence": (
                "PlannerStage constructs code_generation question/output "
                "Artifacts directly instead of consuming canonical bindings."
            ),
        }] if hardcoded_bindings else []),
        "preserved_boundaries": {
            "workflow_executor_rewrite_required": False,
            "resume_policy_rewrite_required": False,
            "planner_change_required_for_preflight": False,
        },
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
