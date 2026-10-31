#!/usr/bin/env python3
"""Read-only readiness check for the v2.4D-3 Provider baseline.

The D-3 dataset evaluates a Provider-backed learning decision.  This command
does not call a Provider and does not import Memory modules: it first verifies
that production exposes the decision entry required by the dataset.  The
legacy fact extractor is deliberately reported as a different capability;
it may extract candidates but does not own production learning or persistence.
"""

from __future__ import annotations

import argparse
import ast
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
DATASET_PATH = ROOT / "evals" / "memory_learning" / "dataset.json"
HARNESS_VERSION = "v2.4D-3-memory-learning-provider-preflight-v1"

_PROVIDER_NAMES = frozenset({
    "MemoryLearner",
    "MemoryLearningProvider",
    "MemoryLearningSelector",
})
_PROVIDER_FUNCTION_NAMES = frozenset({
    "select_memory_learning",
    "decide_memory_learning_with_provider",
    "generate_memory_learning_decision",
})
_EXTRACTOR_NAMES = frozenset({
    "extract_facts_with_llm",
    "async_extract_and_save_facts",
})
_PROHIBITED_IMPORT_PREFIXES = (
    "agent.memory.persistence",
    "agent.memory.long_term",
    "agent.memory.resolution",
    "agent.services.memory_service",
    "evaluation",
    "evals",
)


def _git_head() -> str | None:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True,
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def _dataset_metadata() -> dict[str, Any]:
    payload = json.loads(DATASET_PATH.read_text(encoding="utf-8"))
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return {
        "path": DATASET_PATH.relative_to(ROOT).as_posix(),
        "version": payload.get("version"),
        "case_count": len(payload.get("cases", [])),
        "hash": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
    }


def _scan_symbols() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    providers: list[dict[str, Any]] = []
    extractors: list[dict[str, Any]] = []
    for path in sorted((ROOT / "agent").rglob("*.py")):
        relative = path.relative_to(ROOT).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if node.name in _PROVIDER_NAMES or node.name in _PROVIDER_FUNCTION_NAMES:
                providers.append({
                    "file": relative,
                    "line": node.lineno,
                    "symbol": node.name,
                    "kind": "provider_learning_entry",
                })
            elif node.name in _EXTRACTOR_NAMES:
                extractors.append({
                    "file": relative,
                    "line": node.lineno,
                    "symbol": node.name,
                    "kind": "fact_extractor_not_learning_provider",
                })
    return providers, extractors


def _provider_boundary_violations(
    providers: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Check that the production Provider has no persistence/eval imports."""

    violations: list[dict[str, Any]] = []
    provider_files = {str(item["file"]) for item in providers}
    for relative in sorted(provider_files):
        path = ROOT / relative
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            module: str | None = None
            if isinstance(node, ast.ImportFrom):
                module = node.module
            elif isinstance(node, ast.Import):
                for item in node.names:
                    imported = item.name
                    if any(
                        imported == prefix or imported.startswith(prefix + ".")
                        for prefix in _PROHIBITED_IMPORT_PREFIXES
                    ):
                        violations.append({
                            "file": relative,
                            "line": node.lineno,
                            "kind": "prohibited_provider_import",
                            "module": imported,
                        })
                continue
            if module is not None and any(
                module == prefix or module.startswith(prefix + ".")
                for prefix in _PROHIBITED_IMPORT_PREFIXES
            ):
                violations.append({
                    "file": relative,
                    "line": node.lineno,
                    "kind": "prohibited_provider_import",
                    "module": module,
                })
    return violations


def _case_reports(payload: dict[str, Any], reason: str) -> list[dict[str, Any]]:
    return [
        {
            "case_id": case.get("id"),
            "status": "NOT_EVALUABLE",
            "failure_category": "P-INT",
            "reason": reason,
        }
        for case in payload.get("cases", [])
    ]


def build_preflight_report() -> dict[str, Any]:
    dataset_payload = json.loads(DATASET_PATH.read_text(encoding="utf-8"))
    providers, extractors = _scan_symbols()
    boundary_violations = _provider_boundary_violations(providers)
    blockers: list[dict[str, str]] = []
    if not providers:
        blockers.append({
            "code": "PRODUCTION_MEMORY_LEARNING_PROVIDER_MISSING",
            "category": "P-INT",
            "evidence": (
                "Production exposes deterministic decide_memory_learning() and "
                "fact extraction, but no Provider-backed entry consuming "
                "InteractionEvidence + MemoryPolicyProjection."
            ),
        })
    elif len(providers) != 1:
        blockers.append({
            "code": "PRODUCTION_MEMORY_LEARNING_PROVIDER_ENTRY_COUNT_INVALID",
            "category": "P-CON",
            "evidence": f"Expected exactly one Provider entry, found {len(providers)}.",
        })
    if boundary_violations:
        blockers.append({
            "code": "MEMORY_LEARNING_PROVIDER_BOUNDARY_BYPASS",
            "category": "P-CON",
            "evidence": (
                "Provider imports persistence, Memory stores, or evaluation code; "
                "the Provider must remain a decision-only boundary."
            ),
        })

    extractor_note = (
        "extract_facts_with_llm may be present as a non-authoritative candidate "
        "utility; it is not the D-3 decision Provider or a persistence owner."
    )
    not_evaluable_reason = (
        "No production Memory Learning Provider entry exists; running the D-1 "
        "24-case dataset would measure fact extraction or mutate Memory rather "
        "than the declared D-3 capability."
    )
    return {
        "harness_version": HARNESS_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "head": _git_head(),
        "status": "BLOCKED_PRECONDITION" if blockers else "READY_FOR_REAL_BASELINE",
        "configuration": {
            "provider": "deepseek",
            "provider_calls": 0,
            "memory_writes": 0,
            "provider_fallback": False,
            "golden_repair": False,
            "source_scan_only": True,
        },
        "dataset": _dataset_metadata(),
        "production_entries": {
            "provider_learning": providers,
            "fact_extractors": extractors,
            "provider_boundary_violations": boundary_violations,
            "deterministic_policy": [
                {
                    "file": "agent/memory/learning.py",
                    "symbol": "decide_memory_learning",
                    "kind": "deterministic_policy_not_provider",
                },
            ],
        },
        "blockers": blockers,
        "case_reports": (
            []
            if not blockers
            else _case_reports(dataset_payload, not_evaluable_reason)
        ),
        "evidence_semantics": {
            "provider_path": "NOT_CALLED",
            "format_path": "NOT_APPLICABLE",
            "extractor_is_learning_provider": False,
            "deterministic_policy_is_learning_provider": False,
            "provider_retries": 0,
            "cross_provider_fallback": False,
            "golden_repair": False,
        },
        "conclusion": (
            "A production Memory Learning Provider entry is available; the D-3 "
            "real baseline may be run."
            if not blockers
            else f"{not_evaluable_reason} {extractor_note}"
        ),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    serialized = json.dumps(build_preflight_report(), ensure_ascii=False, indent=2) + "\n"
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(serialized, encoding="utf-8")
    print(serialized, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
