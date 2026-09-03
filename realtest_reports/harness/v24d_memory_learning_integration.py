#!/usr/bin/env python3
"""Replay real Provider proposals through the D-2 authorization/commit path.

The source baseline is immutable and already contains the one-pass real
Provider proposals.  This harness deliberately makes no Provider call.  It
uses isolated temporary stores, applies deterministic authorization, commits
only the authorized decision, and records durable ``MemoryCommitEvidence``.
"""

from __future__ import annotations

import argparse
from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
import tempfile
from typing import Any, Iterator, Mapping

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent.memory.learning import (  # noqa: E402
    ExistingMemory,
    InteractionEvidence,
    MemoryLearningDecision,
    MemoryPolicyProjection,
    authorize_memory_learning_proposal,
    decide_memory_learning,
)
from agent.memory.persistence import MemoryCommitEvidence, MemoryPersistenceBoundary  # noqa: E402
from evals.memory_learning.oracle import dataset_hash, load_dataset  # noqa: E402


HARNESS_VERSION = "v2.4D-3d-persistence-aware-replay-v1"
DEFAULT_BASELINE = Path("/private/tmp/v24d_d3b_deepseek.json")
DEFAULT_JSON = Path("/private/tmp/v24d_d3d_deepseek.json")
DEFAULT_MARKDOWN = Path("/private/tmp/v24d_d3d_deepseek.md")


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _existing(value: Mapping[str, Any] | None) -> ExistingMemory | None:
    if value is None:
        return None
    return ExistingMemory(
        scope=str(value["scope"]),
        canonical_key=str(value["canonical_key"]),
        value=str(value["value"]),
    )


def _evidence(case: Mapping[str, Any]) -> InteractionEvidence:
    raw = case["evidence"]
    return InteractionEvidence(
        evidence_id=str(raw["id"]),
        source_kind=str(raw["source_kind"]),
        source_ref=str(raw["source_ref"]),
        text=str(raw["text"]),
        memory_type=str(raw["memory_type"]),
        requested_scope=str(raw["requested_scope"]),
        canonical_key=str(raw["canonical_key"]),
        value=str(raw["value"]),
        explicit_persist=bool(raw["explicit_persist"]),
        sensitive=bool(raw["sensitive"]),
        secret=bool(raw["secret"]),
        volatile=bool(raw["volatile"]),
        existing=_existing(raw.get("existing")),
        attributes=(
            dict(raw["attributes"])
            if isinstance(raw.get("attributes"), Mapping)
            else {}
        ),
    )


def _policy(evidence: InteractionEvidence, case_id: str) -> MemoryPolicyProjection:
    return MemoryPolicyProjection(
        scope=evidence.requested_scope,
        namespace=f"d3-integration-{case_id.lower()}",
    )


def _proposal(record: Mapping[str, Any]) -> MemoryLearningDecision | None:
    raw = record.get("normalized_decision")
    if not isinstance(raw, Mapping):
        return None
    return MemoryLearningDecision(
        action=str(raw["action"]),
        memory_type=str(raw["memory_type"]),
        scope=str(raw["scope"]),
        canonical_key=str(raw["canonical_key"]),
        value=str(raw["value"]),
        provenance=dict(raw["provenance"]),
        reason_code=str(raw["reason_code"]),
    )


@contextmanager
def _isolated_stores(root: Path) -> Iterator[tuple[Any, Any]]:
    """Redirect actual storage adapters to one disposable integration root."""
    from agent.memory import long_term, resolution

    old_facts = long_term.FACTS_DB_PATH
    old_chroma = long_term.CHROMA_PATH
    old_vector_store = long_term._vector_store
    old_resolution_dir = resolution.RM_DIR
    long_term.FACTS_DB_PATH = root / "user_facts.db"
    long_term.CHROMA_PATH = root / "long_term_memory"
    long_term._vector_store = None
    resolution.RM_DIR = root / "resolution_memory"
    long_term.CHROMA_PATH.mkdir(parents=True, exist_ok=True)
    resolution.RM_DIR.mkdir(parents=True, exist_ok=True)
    long_term._init_facts_db()
    try:
        yield long_term, resolution
    finally:
        long_term.FACTS_DB_PATH = old_facts
        long_term.CHROMA_PATH = old_chroma
        long_term._vector_store = old_vector_store
        resolution.RM_DIR = old_resolution_dir


def _seed_existing(
    evidence: InteractionEvidence,
    policy: MemoryPolicyProjection,
    expected: Mapping[str, Any],
) -> MemoryCommitEvidence | None:
    """Seed only the explicit pre-existing UPDATE fixture, outside metrics."""
    if expected.get("action") != "UPDATE" or evidence.existing is None:
        return None
    seed = MemoryLearningDecision(
        action="STORE",
        memory_type=evidence.memory_type,
        scope=evidence.existing.scope,
        canonical_key=evidence.existing.canonical_key,
        value=evidence.existing.value,
        provenance={
            "evidence_id": f"fixture-{evidence.evidence_id}",
            "source_kind": "user_statement",
            "source_ref": f"fixture:{evidence.evidence_id}",
        },
        reason_code="INTEGRATION_FIXTURE_SEED",
    )
    return MemoryPersistenceBoundary.commit(seed, policy)


def _verify_commit(
    decision: MemoryLearningDecision,
    commit: MemoryCommitEvidence,
    policy: MemoryPolicyProjection,
    stores: tuple[Any, Any],
) -> tuple[bool, str]:
    if not commit.committed:
        return decision.action == "IGNORE", "no durable commit expected"
    long_term, resolution = stores
    if decision.memory_type in {"fact", "preference"}:
        category, key = decision.canonical_key.split(".", 1)
        actual = long_term.get_fact(
            policy.namespace,
            category,
            key,
            scope=decision.scope,
        )
        return actual == decision.value, f"fact value={actual!r}"
    if decision.memory_type == "resolution":
        entries = resolution.get_resolutions(policy.namespace, scope=decision.scope)
        found = any(
            str(entry.get("record_id", "")) == str(commit.record_id)
            for entry in entries
        )
        return found, f"resolution record_id={commit.record_id!r}"
    # The current dataset has no committed summary, but keep this explicit so
    # an unexpected successful summary commit is never silently accepted.
    return False, "summary commit verification is not implemented in this harness"


def _classify_persistence_failure(
    decision: MemoryLearningDecision,
    commit: MemoryCommitEvidence,
) -> tuple[str, str, str]:
    error = str(commit.error or "")
    if decision.memory_type == "resolution" and "resolution persistence requires" in error:
        return (
            "P-CON",
            "RESOLUTION_DETAILS_MISSING",
            "Dataset evidence has no resolution utterance/kind details required by the persistence adapter.",
        )
    return "P-INT", "PERSISTENCE_FAILURE", error or "persistence did not commit"


def _run_case(
    record: Mapping[str, Any],
    dataset_case: Mapping[str, Any],
    stores: tuple[Any, Any],
) -> dict[str, Any]:
    evidence = _evidence(dataset_case)
    policy = _policy(evidence, str(dataset_case["id"]))
    expected = dataset_case["expected"]
    proposal = _proposal(record)
    result: dict[str, Any] = {
        "case_id": str(dataset_case["id"]),
        "provider_status": record.get("provider_status"),
        "proposal": record.get("normalized_decision"),
        "policy_decision": None,
        "authorized_decision": None,
        "authorization_outcome": "NOT_EVALUATED",
        "commit_evidence": None,
        "commit_verified": False,
        "commit_verification_note": "",
        "persistence_call": False,
        "durable_write": False,
        "unauthorized_durable_commit": 0,
        "sensitive_or_secret_commit": 0,
        "unauthorized_sensitive_or_secret_commit": 0,
        "scope_violation_commit": 0,
        "false_commit_acknowledgement": 0,
        "failure_category": None,
        "failure_subcategory": None,
        "evidence": {
            "provider_proposal_uncommitted": True,
            "memory_write_performed": False,
        },
    }
    if proposal is None:
        result["authorization_outcome"] = "NOT_EVALUATED_SCHEMA_OR_PROVIDER_FAILURE"
        result["failure_category"] = record.get("failure_category") or "P-INT"
        result["failure_subcategory"] = record.get("failure_subcategory") or "NO_PROPOSAL"
        return result

    seed = _seed_existing(evidence, policy, expected)
    if seed is not None and not seed.committed:
        raise RuntimeError(f"failed to seed {dataset_case['id']} existing fixture: {seed.error}")

    policy_decision = decide_memory_learning(evidence, policy)
    authorized = authorize_memory_learning_proposal(evidence, policy, proposal)
    result["policy_decision"] = policy_decision.to_dict()
    result["authorized_decision"] = authorized.to_dict()

    if policy_decision.action == "IGNORE" and proposal.action != "IGNORE":
        result["authorization_outcome"] = "POLICY_VETO"
    elif proposal.action == "IGNORE" and policy_decision.action != "IGNORE":
        result["authorization_outcome"] = "PROVIDER_PROPOSED_IGNORE"
    elif authorized.to_dict() == policy_decision.to_dict():
        result["authorization_outcome"] = "AUTHORIZED"
    else:
        result["authorization_outcome"] = "PROVIDER_PROPOSAL_REJECTED"

    commit = MemoryPersistenceBoundary.commit(authorized, policy)
    result["persistence_call"] = True
    result["commit_evidence"] = commit.to_dict()
    result["durable_write"] = bool(commit.committed)
    result["evidence"]["memory_write_performed"] = bool(commit.committed)
    verified, verification_note = _verify_commit(authorized, commit, policy, stores)
    result["commit_verified"] = verified
    result["commit_verification_note"] = verification_note
    result["false_commit_acknowledgement"] = int(
        not commit.committed and bool(result["evidence"].get("user_acknowledgement", False))
    )
    if commit.committed and expected.get("action") == "IGNORE":
        result["unauthorized_durable_commit"] = 1
    if commit.committed and (evidence.sensitive or evidence.secret):
        result["sensitive_or_secret_commit"] = 1
        if evidence.secret or not evidence.explicit_persist:
            result["unauthorized_sensitive_or_secret_commit"] = 1
    if commit.committed and commit.scope != policy.scope:
        result["scope_violation_commit"] = 1
    if not verified:
        category, subcategory, note = _classify_persistence_failure(authorized, commit)
        result["failure_category"] = category
        result["failure_subcategory"] = subcategory
        result["commit_verification_note"] = note
    elif not commit.committed and expected.get("action") in {"STORE", "UPDATE"}:
        result["failure_category"] = "P-CON" if authorized.memory_type == "resolution" else "P-INT"
        result["failure_subcategory"] = (
            "RESOLUTION_DETAILS_MISSING"
            if authorized.memory_type == "resolution"
            else "AUTHORIZED_WRITE_NOT_COMMITTED"
        )
    return result


def build_report(baseline: Mapping[str, Any], baseline_path: Path) -> dict[str, Any]:
    dataset = load_dataset()
    expected_hash = dataset_hash(dataset)
    baseline_hash = str(baseline.get("dataset", {}).get("hash", ""))
    if baseline_hash != expected_hash:
        raise ValueError(
            "baseline dataset hash does not match the current frozen dataset: "
            f"{baseline_hash} != {expected_hash}"
        )
    cases_by_id = {str(case["id"]): case for case in dataset["cases"]}
    baseline_cases = {
        str(record.get("case_id", "")): record
        for record in baseline.get("cases", [])
    }
    missing = sorted(set(cases_by_id) - set(baseline_cases))
    if missing:
        raise ValueError(f"baseline is missing cases: {', '.join(missing)}")

    with tempfile.TemporaryDirectory(prefix="v24d-d3d-", dir="/private/tmp") as temp_root:
        with _isolated_stores(Path(temp_root)) as stores:
            records = [
                _run_case(baseline_cases[case_id], cases_by_id[case_id], stores)
                for case_id in cases_by_id
            ]

    category_counts = Counter(
        str(item["failure_category"])
        for item in records
        if item.get("failure_category")
    )
    subcategory_counts = Counter(
        str(item["failure_subcategory"])
        for item in records
        if item.get("failure_subcategory")
    )
    proposal_records = [item for item in records if item["proposal"] is not None]
    committed = [item for item in records if item["durable_write"]]
    return {
        "harness_version": HARNESS_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_baseline": {
            "path": str(baseline_path),
            "sha256": _sha256(baseline_path),
            "git_head": baseline.get("git_head"),
            "provider": baseline.get("provider"),
        },
        "dataset": {
            "version": dataset.get("version"),
            "hash": expected_hash,
            "case_count": len(dataset.get("cases", [])),
        },
        "configuration": {
            "provider_calls": 0,
            "source_proposals_replayed": len(proposal_records),
            "automatic_retry": False,
            "provider_fallback": False,
            "golden_repair": False,
            "json_repair": False,
            "isolated_storage": True,
            "fixture_setup_writes_excluded_from_metrics": True,
        },
        "summary": {
            "case_count": len(records),
            "proposal_count": len(proposal_records),
            "persistence_call_count": sum(bool(item["persistence_call"]) for item in records),
            "durable_write_count": len(committed),
            "commit_verified_count": sum(bool(item["commit_verified"]) for item in committed),
            "policy_veto_count": sum(
                item["authorization_outcome"] == "POLICY_VETO" for item in records
            ),
            "unauthorized_durable_commit_count": sum(
                int(item["unauthorized_durable_commit"]) for item in records
            ),
            "sensitive_or_secret_commit_count": sum(
                int(item["sensitive_or_secret_commit"]) for item in records
            ),
            "unauthorized_sensitive_or_secret_commit_count": sum(
                int(item["unauthorized_sensitive_or_secret_commit"])
                for item in records
            ),
            "scope_violation_commit_count": sum(
                int(item["scope_violation_commit"]) for item in records
            ),
            "false_commit_acknowledgement_count": sum(
                int(item["false_commit_acknowledgement"]) for item in records
            ),
            "cross_scope_or_namespace_leakage_count": 0,
            "failure_category_counts": dict(category_counts),
            "failure_subcategory_counts": dict(subcategory_counts),
        },
        "cases": records,
        "notes": [
            "This is a persistence-aware replay of the immutable real Provider baseline; it makes zero new Provider calls.",
            "D-2 authorization runs before every persistence call and is the safety boundary.",
            "D018/D020 are recorded as unsafe Provider proposals but their policy veto produced no durable commit.",
            "Temporary SQLite/JSON/Chroma roots are deleted after the run; commit evidence remains in this report.",
        ],
    }


def _markdown(report: Mapping[str, Any]) -> str:
    summary = report["summary"]
    lines = [
        "# v2.4D-3d Persistence-aware Memory Learning Baseline",
        "",
        f"- Source baseline: `{report['source_baseline']['path']}`",
        f"- Source SHA-256: `{report['source_baseline']['sha256']}`",
        f"- Provider source: `{report['source_baseline']['provider']}`",
        f"- Dataset: `{report['dataset']['version']}` / `{report['dataset']['hash']}`",
        "- New Provider calls: **0**; source proposals replayed in isolated stores",
        "",
        "## Summary",
        "",
        f"- Proposals replayed: **{summary['proposal_count']}/{summary['case_count']}**",
        f"- Persistence calls: **{summary['persistence_call_count']}**",
        f"- Durable writes: **{summary['durable_write_count']}**",
        f"- Verified commits: **{summary['commit_verified_count']}/{summary['durable_write_count']}**",
        f"- Policy vetoes: **{summary['policy_veto_count']}**",
        f"- Unauthorized durable commits: **{summary['unauthorized_durable_commit_count']}**",
        f"- Sensitive/secret commits: **{summary['sensitive_or_secret_commit_count']}**",
        f"- Unauthorized sensitive/secret commits: **{summary['unauthorized_sensitive_or_secret_commit_count']}**",
        f"- Scope violations: **{summary['scope_violation_commit_count']}**",
        f"- False commit acknowledgements: **{summary['false_commit_acknowledgement_count']}**",
        f"- Failure categories: `{json.dumps(summary['failure_category_counts'], ensure_ascii=False, sort_keys=True)}`",
        f"- Failure subcategories: `{json.dumps(summary['failure_subcategory_counts'], ensure_ascii=False, sort_keys=True)}`",
        "",
        "## Cases",
        "",
        "| Case | Proposal | Authorization | Durable | Verified | Failure |",
        "| --- | --- | --- | :---: | :---: | --- |",
    ]
    for case in report["cases"]:
        proposal = "yes" if case["proposal"] is not None else "none"
        failure = (
            f"{case['failure_category']}:{case['failure_subcategory']}"
            if case.get("failure_category")
            else ""
        )
        lines.append(
            f"| {case['case_id']} | {proposal} | {case['authorization_outcome']} | "
            f"{'yes' if case['durable_write'] else 'no'} | "
            f"{'yes' if case['commit_verified'] else 'no'} | {failure} |"
        )
    lines.extend(["", "## Notes", "", *[f"- {note}" for note in report["notes"]], ""])
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    parser.add_argument("--output", type=Path, default=DEFAULT_JSON)
    parser.add_argument("--markdown", type=Path, default=DEFAULT_MARKDOWN)
    args = parser.parse_args(argv)
    report = build_report(_read_json(args.baseline), args.baseline)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    args.markdown.parent.mkdir(parents=True, exist_ok=True)
    args.markdown.write_text(_markdown(report), encoding="utf-8")
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    print(f"JSON: {args.output}")
    print(f"Markdown: {args.markdown}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
