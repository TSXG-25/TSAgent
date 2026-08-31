#!/usr/bin/env python3
"""Read-only production preflight for v2.4D Memory Learning.

The preflight intentionally scans source instead of importing production
Memory modules.  Importing those modules would initialize local stores and
would make a contract discovery command mutate the workspace.
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
HARNESS_VERSION = "v2.4D-1-memory-learning-preflight-v1"

_PRODUCTION_MEMORY_FILES = (
    "agent/memory/session.py",
    "agent/memory/short_term.py",
    "agent/memory/long_term.py",
    "agent/memory/preference.py",
    "agent/memory/resolution.py",
    "agent/services/memory_service.py",
    "agent/memory/lifecycle.py",
)
_WRITE_SYMBOLS = {
    "add_message",
    "add_user_message",
    "add_assistant_message",
    "add_exchange",
    "store_summary",
    "save_fact",
    "async_extract_and_save_facts",
    "record_resolution",
    "record_full_exchange",
}
_LEARNING_SYMBOLS = {
    "MemoryLearner",
    "MemoryLearningDecision",
    "LearningEligibility",
    "MemoryLearningPolicy",
    "select_memory_learning",
    "decide_memory_learning",
}


def _git_head() -> str | None:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True,
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def _dataset_metadata() -> dict[str, Any]:
    payload = json.loads(DATASET_PATH.read_text(encoding="utf-8"))
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
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
            if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.name in _WRITE_SYMBOLS or node.name in _LEARNING_SYMBOLS:
                    symbols.append({
                        "file": relative,
                        "line": node.lineno,
                        "symbol": node.name,
                        "kind": "learning_entry" if node.name in _LEARNING_SYMBOLS else "writer",
                    })
    return symbols


def _write_call_sites() -> list[dict[str, Any]]:
    sites: list[dict[str, Any]] = []
    call_pattern = re.compile(
        r"\b(?:record_full_exchange|extract_and_save_facts|record_resolution|"
        r"store_summary|save_fact|add_exchange|add_user_message|add_assistant_message)\s*\("
    )
    for path in sorted((ROOT / "agent").rglob("*.py")):
        if "/memory/" in path.as_posix() and path.name in {"session.py", "short_term.py", "long_term.py", "preference.py", "resolution.py"}:
            continue
        source = path.read_text(encoding="utf-8")
        for line_number, line in enumerate(source.splitlines(), 1):
            if call_pattern.search(line) and not re.search(r"^\s*(?:async\s+)?def\s+", line):
                sites.append({
                    "file": path.relative_to(ROOT).as_posix(),
                    "line": line_number,
                    "text": line.strip()[:180],
                })
    return sites


def _writer_inventory() -> list[dict[str, Any]]:
    return [
        {
            "layer": "session",
            "file": "agent/memory/session.py",
            "symbols": ["add_message", "add_user_message", "add_assistant_message"],
            "storage": "process-global _sessions dict",
            "scope": "namespace key",
            "provenance": "none",
            "dedup_conflict": "adjacent same role/content collapse only",
            "expiry": "MAX_MESSAGES capacity trim",
            "delete": "clear_session",
        },
        {
            "layer": "short_term",
            "file": "agent/memory/short_term.py",
            "symbols": ["add_exchange", "_save"],
            "storage": "data/short_term/<namespace>.json",
            "scope": "filename namespace",
            "provenance": "timestamp only",
            "dedup_conflict": "append",
            "expiry": "window/compression threshold only",
            "delete": "clear_history",
        },
        {
            "layer": "long_term_summary",
            "file": "agent/memory/long_term.py",
            "symbols": ["store_summary"],
            "storage": "Chroma long_term collection",
            "scope": "metadata.user_id filter",
            "provenance": "user_id/type/timestamp only",
            "dedup_conflict": "add_documents append",
            "expiry": "none",
            "delete": "clear_summaries",
        },
        {
            "layer": "user_fact",
            "file": "agent/memory/long_term.py",
            "symbols": ["save_fact"],
            "storage": "data/user_facts.db facts table",
            "scope": "user_id column",
            "provenance": "none",
            "dedup_conflict": "UNIQUE + INSERT OR REPLACE",
            "expiry": "none",
            "delete": "clear_facts",
        },
        {
            "layer": "preference_extractor",
            "file": "agent/memory/preference.py",
            "symbols": ["async_extract_and_save_facts"],
            "storage": "delegates to user_fact",
            "scope": "caller namespace",
            "provenance": "LLM/regex result has no evidence id",
            "dedup_conflict": "delegated overwrite",
            "expiry": "none",
            "delete": "not owned here",
        },
        {
            "layer": "resolution",
            "file": "agent/memory/resolution.py",
            "symbols": ["record_resolution"],
            "storage": "data/resolution_memory/<namespace>.json",
            "scope": "filename namespace",
            "provenance": "metadata optional; current caller omits it",
            "dedup_conflict": "append",
            "expiry": "MAX_ENTRIES capacity trim",
            "delete": "clear_resolutions",
        },
    ]


def build_preflight_report() -> dict[str, Any]:
    symbols = _production_symbols()
    learning_entries = [item for item in symbols if item["kind"] == "learning_entry"]
    writer_symbols = [item for item in symbols if item["kind"] == "writer"]
    call_sites = _write_call_sites()
    long_term = _source("agent/memory/long_term.py")
    preference = _source("agent/memory/preference.py")
    short_term = _source("agent/memory/short_term.py")
    resolution = _source("agent/memory/resolution.py")
    memory_service = _source("agent/services/memory_service.py")
    blockers: list[dict[str, str]] = []
    if not learning_entries:
        blockers.append({
            "code": "PRODUCTION_MEMORY_LEARNING_ENTRY_MISSING",
            "category": "P-INT",
            "evidence": (
                "No production MemoryLearner/MemoryLearningDecision entry consumes "
                "InteractionEvidence + MemoryPolicyProjection."
            ),
        })
    if len(writer_symbols) > 1:
        blockers.append({
            "code": "MEMORY_WRITE_AUTHORIZATION_FRAGMENTED",
            "category": "P-CON",
            "evidence": (
                f"Production source exposes {len(writer_symbols)} memory writer symbols "
                "across multiple layers without a learning decision boundary."
            ),
        })
    provenance_complete = all(token in long_term for token in ("evidence_id", "source_ref"))
    if not provenance_complete:
        blockers.append({
            "code": "MEMORY_PROVENANCE_CONTRACT_MISSING",
            "category": "P-CON",
            "evidence": "Summary/fact persistence schemas do not carry evidence_id and source_ref.",
        })
    retrieval_scope_fallback = (
        "similarity_search_with_score(query, k=k)" in long_term
        and "filter={\"user_id\": user_id}" in long_term
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
            "writer_inventory": _writer_inventory(),
            "scoped_memory_view_present": (
                "class ScopedMemoryView" in memory_service
                and "self.namespace" in memory_service
                and "_ensure_open" in memory_service
            ),
            "facts_unique_key": "UNIQUE(user_id, category, key)" in long_term,
            "facts_overwrite_semantics": "INSERT OR REPLACE" in long_term,
            "summary_append_semantics": ".add_documents([doc])" in long_term,
            "short_term_append_semantics": "history.append({" in short_term,
            "resolution_append_semantics": "entries.append({" in resolution,
            "preference_extractor_directly_persists": "from agent.memory.long_term import save_fact" in preference,
            "retrieval_scope_fallback_without_filter": retrieval_scope_fallback,
            "expiry_is_fact_level": any(token in long_term for token in ("expires_at", "ttl", "expires")),
            "delete_owner": "MemoryRuntime.reset",
        },
        "blockers": blockers,
        "watchlist": [
            {
                "code": "MEMORY_NAMESPACE_SCOPE_AMBIGUOUS",
                "category": "P-CON",
                "evidence": "SessionRuntime uses session id or tenant:user as a store namespace; no typed learning scope is projected.",
            },
            {
                "code": "MEMORY_COMMIT_EVIDENCE_MISSING",
                "category": "P-INT",
                "evidence": "save_fact/store_summary swallow persistence exceptions and return no durable commit evidence.",
            },
            {
                "code": "RETRIEVAL_SCOPE_FALLBACK_PRESENT",
                "category": "P-INT",
                "evidence": "Filtered summary retrieval has an unfiltered fallback path.",
            },
        ],
        "preserved_boundaries": {
            "memory_store_modified": False,
            "runtime_modified": False,
            "provider_called": False,
            "conversation_runtime_reclassified": False,
            "run_output_reclassified": False,
        },
        "case_reports": [
            {
                "case_id": f"D{index:03d}",
                "case_result": "NOT_EVALUABLE",
                "evaluable": False,
                "provider_status": "NOT_CALLED",
                "oracle_result": None,
                "failure_category": "P-INT",
                "failure_subcategory": "PRODUCTION_MEMORY_LEARNING_ENTRY_MISSING",
                "evidence": ["PRODUCTION_MEMORY_LEARNING_ENTRY_MISSING"],
            }
            for index in range(1, 25)
        ] if not learning_entries else [],
        "conclusion": (
            "Production Memory Learning is not evaluable until a canonical learning decision "
            "entry and one scoped persistence boundary are implemented."
            if blockers else
            "Production Memory Learning entry is present; proceed only to a separate deterministic baseline."
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
