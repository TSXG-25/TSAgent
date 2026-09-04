#!/usr/bin/env python3
"""Read-only production preflight for v2.4D Memory Learning.

The preflight scans source instead of importing production Memory modules.
Importing those modules initializes local stores and would make a discovery
command mutate the workspace. Runtime/session buffers are inventoried, but
only direct calls to learned-memory storage adapters are a boundary
violation.
"""

from __future__ import annotations

import argparse
import ast
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
DATASET_PATH = ROOT / "evals" / "memory_learning" / "dataset.json"
HARNESS_VERSION = "v2.4D-2-memory-learning-preflight-v1"

_PRODUCTION_MEMORY_FILES = (
    "agent/memory/session.py",
    "agent/memory/short_term.py",
    "agent/memory/long_term.py",
    "agent/memory/preference.py",
    "agent/memory/resolution.py",
    "agent/memory/learning.py",
    "agent/memory/persistence.py",
    "agent/services/memory_service.py",
    "agent/memory/lifecycle.py",
)
_WRITE_SYMBOLS = {
    "add_message",
    "add_user_message",
    "add_assistant_message",
    "add_exchange",
    "_save",
    "record_full_exchange",
}
_LEARNED_STORAGE_SYMBOLS = {
    "save_fact",
    "store_summary",
    "record_resolution",
    "_persist_fact",
    "_persist_summary",
    "_persist_resolution",
}
_LEARNING_SYMBOLS = {
    "InteractionEvidence",
    "MemoryLearningDecision",
    "MemoryLearningPolicy",
    "MemoryPolicyProjection",
    "MemoryPersistenceBoundary",
    "decide_memory_learning",
}
_LEARNED_STORAGE_MODULES = {
    "agent.memory.long_term",
    "agent.memory.resolution",
}
_BOUNDARY_PATH = "agent/memory/persistence.py"


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


def _source(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def _production_symbols() -> list[dict[str, Any]]:
    symbols: list[dict[str, Any]] = []
    for relative in _PRODUCTION_MEMORY_FILES:
        path = ROOT / relative
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if node.name in _LEARNING_SYMBOLS:
                kind = "learning_entry"
            elif node.name in _LEARNED_STORAGE_SYMBOLS:
                kind = "learned_storage_adapter"
            elif node.name in _WRITE_SYMBOLS:
                kind = "transient_or_projection_writer"
            else:
                continue
            symbols.append({
                "file": relative,
                "line": node.lineno,
                "symbol": node.name,
                "kind": kind,
            })
    return symbols


def _write_call_sites() -> list[dict[str, Any]]:
    """Find low-level learned-store imports/calls outside the boundary.

    Calls through MemoryService are intentionally allowed: that facade is the
    application-facing projection and commits through this boundary. A direct
    import of save_fact/_persist_fact or equivalent is not.
    """
    sites: list[dict[str, Any]] = []
    for path in sorted((ROOT / "agent").rglob("*.py")):
        relative = path.relative_to(ROOT).as_posix()
        if relative == _BOUNDARY_PATH:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        direct_names: set[str] = set()
        module_aliases: set[str] = set()

        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module in _LEARNED_STORAGE_MODULES:
                for item in node.names:
                    if item.name in _LEARNED_STORAGE_SYMBOLS:
                        direct_names.add(item.asname or item.name)
                        sites.append({
                            "file": relative,
                            "line": node.lineno,
                            "kind": "direct_learned_store_import",
                            "text": ast.unparse(node)[:180],
                        })
            elif isinstance(node, ast.Import):
                for item in node.names:
                    if item.name in _LEARNED_STORAGE_MODULES:
                        module_aliases.add(item.asname or item.name.rsplit(".", 1)[-1])

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            function = node.func
            if isinstance(function, ast.Name) and function.id in direct_names:
                sites.append({
                    "file": relative,
                    "line": node.lineno,
                    "kind": "direct_learned_store_call",
                    "text": ast.unparse(node)[:180],
                })
            elif (
                isinstance(function, ast.Attribute)
                and function.attr in _LEARNED_STORAGE_SYMBOLS
                and isinstance(function.value, ast.Name)
                and function.value.id in module_aliases
            ):
                sites.append({
                    "file": relative,
                    "line": node.lineno,
                    "kind": "direct_learned_store_call",
                    "text": ast.unparse(node)[:180],
                })
    return sites


def _learning_proposal_call_sites() -> dict[str, list[dict[str, Any]]]:
    """Inventory the production proposal owner and retired extractor path."""

    provider_sites: list[dict[str, Any]] = []
    legacy_sites: list[dict[str, Any]] = []
    for base in (ROOT / "agent", ROOT / "tools"):
        for path in sorted(base.rglob("*.py")):
            relative = path.relative_to(ROOT).as_posix()
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                function = node.func
                name = (
                    function.id
                    if isinstance(function, ast.Name)
                    else function.attr
                    if isinstance(function, ast.Attribute)
                    else ""
                )
                item = {
                    "file": relative,
                    "line": node.lineno,
                    "symbol": name,
                }
                if name == "MemoryLearningProvider":
                    provider_sites.append(item)
                elif name == "extract_facts_with_llm":
                    legacy_sites.append(item)
    return {
        "provider": provider_sites,
        "legacy_extractor": legacy_sites,
    }


def _writer_inventory() -> list[dict[str, Any]]:
    return [
        {
            "layer": "session",
            "file": "agent/memory/session.py",
            "symbols": ["add_message", "add_user_message", "add_assistant_message"],
            "storage": "process-global _sessions dict",
            "classification": "runtime/transient; outside learned-memory gate",
            "scope": "namespace key",
            "delete": "clear_session",
        },
        {
            "layer": "short_term",
            "file": "agent/memory/short_term.py",
            "symbols": ["add_exchange", "_save"],
            "storage": "data/short_term/<namespace>.json",
            "classification": "runtime/transient; generated summary uses boundary",
            "scope": "explicit learning scope on compression",
            "delete": "clear_history",
        },
        {
            "layer": "learning_decision",
            "file": "agent/memory/learning.py",
            "symbols": ["InteractionEvidence", "MemoryPolicyProjection", "decide_memory_learning"],
            "storage": "none",
            "classification": "authorized STORE/UPDATE/IGNORE decision",
            "scope": "typed policy projection",
            "delete": "not owned here",
        },
        {
            "layer": "persistence_boundary",
            "file": "agent/memory/persistence.py",
            "symbols": ["MemoryPersistenceBoundary.commit"],
            "storage": "delegates to existing fact/summary/resolution stores",
            "classification": "single durable learned-memory commit boundary",
            "scope": "decision scope + policy namespace",
            "provenance": "evidence_id/source_kind/source_ref",
            "delete": "MemoryRuntime.reset",
        },
        {
            "layer": "preference_extractor",
            "file": "agent/memory/preference.py",
            "symbols": ["extract_facts_with_llm"],
            "storage": "none",
            "classification": "non-authoritative candidate extraction utility",
            "scope": "none",
            "provenance": "none",
            "delete": "MemoryRuntime.reset",
        },
        {
            "layer": "production_learning_owner",
            "file": "agent/services/memory_service.py",
            "symbols": [
                "learn_from_interaction",
                "learn_summary",
                "learn_resolution",
            ],
            "storage": "MemoryPersistenceBoundary only",
            "classification": "Provider proposal + policy authorization + commit",
            "scope": "ScopedMemoryView.learning_scope",
            "provenance": "Runtime-projected source_ref",
            "delete": "MemoryRuntime.reset",
        },
        {
            "layer": "resolution",
            "file": "agent/memory/resolution.py",
            "symbols": ["_persist_resolution"],
            "storage": "data/resolution_memory/<namespace>.json",
            "classification": "private storage adapter; boundary-only caller",
            "scope": "record field + scoped retrieval",
            "provenance": "evidence_id/source_kind/source_ref",
            "delete": "clear_resolutions",
        },
    ]


def build_preflight_report() -> dict[str, Any]:
    symbols = _production_symbols()
    learning_entries = [item for item in symbols if item["kind"] == "learning_entry"]
    writer_symbols = [
        item for item in symbols
        if item["kind"] in {"transient_or_projection_writer", "learned_storage_adapter"}
    ]
    call_sites = _write_call_sites()
    proposal_sites = _learning_proposal_call_sites()
    long_term = _source("agent/memory/long_term.py")
    preference = _source("agent/memory/preference.py")
    short_term = _source("agent/memory/short_term.py")
    resolution = _source("agent/memory/resolution.py")
    learning = _source("agent/memory/learning.py")
    persistence = _source("agent/memory/persistence.py")
    memory_service = _source("agent/services/memory_service.py")
    runtime_context = _source("agent/runtime_context.py")
    blockers: list[dict[str, str]] = []

    required_learning_symbols = {
        "MemoryLearningDecision",
        "MemoryPolicyProjection",
        "decide_memory_learning",
    }
    discovered_learning_names = {item["symbol"] for item in learning_entries}
    missing_learning = sorted(required_learning_symbols - discovered_learning_names)
    if missing_learning:
        blockers.append({
            "code": "PRODUCTION_MEMORY_LEARNING_ENTRY_MISSING",
            "category": "P-INT",
            "evidence": f"Missing production learning symbols: {', '.join(missing_learning)}.",
        })

    if not any(item["symbol"] == "MemoryPersistenceBoundary" for item in learning_entries):
        blockers.append({
            "code": "PRODUCTION_MEMORY_PERSISTENCE_BOUNDARY_MISSING",
            "category": "P-INT",
            "evidence": "No production MemoryPersistenceBoundary.commit entry was found.",
        })

    if call_sites:
        blockers.append({
            "code": "DIRECT_LEARNED_MEMORY_WRITE_OUTSIDE_BOUNDARY",
            "category": "P-CON",
            "evidence": (
                f"Found {len(call_sites)} direct learned-store import/call sites "
                "outside the boundary."
            ),
        })

    provider_owner_files = {
        item["file"] for item in proposal_sites["provider"]
    }
    if provider_owner_files != {"agent/services/memory_service.py"}:
        blockers.append({
            "code": "PRODUCTION_MEMORY_PROPOSAL_OWNER_INVALID",
            "category": "P-CON",
            "evidence": (
                "MemoryLearningProvider must be instantiated only by "
                "agent/services/memory_service.py."
            ),
        })
    if proposal_sites["legacy_extractor"]:
        blockers.append({
            "code": "LEGACY_FACT_EXTRACTOR_STILL_AUTHORITATIVE",
            "category": "P-CON",
            "evidence": (
                "Production durable learning still calls extract_facts_with_llm()."
            ),
        })

    provenance_tokens = ("scope", "evidence_id", "source_kind", "source_ref")
    if not all(token in long_term for token in provenance_tokens):
        blockers.append({
            "code": "MEMORY_PROVENANCE_CONTRACT_MISSING",
            "category": "P-CON",
            "evidence": "Fact/summary storage adapters do not expose the complete provenance contract.",
        })
    if not all(token in resolution for token in provenance_tokens):
        blockers.append({
            "code": "RESOLUTION_PROVENANCE_CONTRACT_MISSING",
            "category": "P-CON",
            "evidence": "Resolution storage adapter does not expose the complete provenance contract.",
        })

    retrieval_scope_fallback = bool(
        re.search(
            r"similarity_search_with_score\(\s*query\s*,\s*k\s*=\s*k\s*\)",
            long_term,
            re.DOTALL,
        )
    )
    if retrieval_scope_fallback:
        blockers.append({
            "code": "RETRIEVAL_SCOPE_FALLBACK_PRESENT",
            "category": "P-CON",
            "evidence": "Scoped summary retrieval still contains an unfiltered fallback query.",
        })

    scoped_view_present = all(
        token in memory_service
        for token in ("class ScopedMemoryView", "learning_scope", "_ensure_open")
    )
    if not scoped_view_present or "memory_scope" not in runtime_context:
        blockers.append({
            "code": "MEMORY_SCOPE_PROJECTION_MISSING",
            "category": "P-CON",
            "evidence": "Session-owned memory view is missing an explicit learning scope projection.",
        })

    preference_direct = any(
        site["file"] == "agent/memory/preference.py"
        for site in call_sites
    )

    return {
        "harness_version": HARNESS_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "head": _git_head(),
        "status": "BLOCKED_PRECONDITION" if blockers else "READY_FOR_REAL_BASELINE",
        "configuration": {
            "provider_calls": 0,
            "memory_writes": 0,
            "store_imports": 0,
            "source_scan_only": True,
        },
        "dataset": _dataset_metadata(),
        "discovery": {
            "production_memory_learning_entries": learning_entries,
            "production_writer_symbols": writer_symbols,
            "production_write_call_sites": call_sites,
            "production_learning_proposal_call_sites": proposal_sites,
            "writer_inventory": _writer_inventory(),
            "learning_policy_present": "InteractionEvidence" in learning,
            "persistence_boundary_present": "class MemoryPersistenceBoundary" in persistence,
            "scoped_memory_view_present": scoped_view_present,
            "facts_unique_key": "UNIQUE(user_id, scope, category, key)" in long_term,
            "facts_overwrite_semantics": "INSERT OR REPLACE" in long_term,
            "summary_append_semantics": ".add_documents([doc], ids=[record_id])" in long_term,
            "short_term_append_semantics": "history.append({" in short_term,
            "resolution_append_semantics": "entries.append({" in resolution,
            "preference_extractor_directly_persists": preference_direct,
            "retrieval_scope_fallback_without_filter": retrieval_scope_fallback,
            "expiry_is_fact_level": any(
                token in long_term for token in ("expires_at", "ttl", "expires")
            ),
            "delete_owner": "MemoryRuntime.reset",
        },
        "blockers": blockers,
        "watchlist": [
            {
                "code": "MEMORY_EXPIRY_NOT_IMPLEMENTED",
                "category": "P-CON",
                "evidence": "Expiry/TTL remains outside D-2 scope; no expiry engine was introduced.",
            },
        ],
        "preserved_boundaries": {
            "memory_store_modified": True,
            "store_architecture_rewritten": False,
            "runtime_modified": True,
            "provider_called": False,
            "conversation_runtime_reclassified": False,
            "run_output_reclassified": False,
        },
        "case_reports": [],
        "conclusion": (
            "Production Memory Learning decision and persistence boundaries are present; "
            "the deterministic contract baseline may run separately."
            if not blockers
            else
            "Production Memory Learning remains blocked until all boundary preconditions are satisfied."
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
