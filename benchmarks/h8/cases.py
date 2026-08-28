"""H8 contract cases.

The dataset is deliberately small and deterministic.  It checks ownership of
the Runtime control path; it is not a capability-quality benchmark.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json


DATASET_VERSION = "h8-single-runtime-spine-v1"


@dataclass(frozen=True)
class H8Case:
    case_id: str
    category: str
    description: str
    oracle: str


CASES: tuple[H8Case, ...] = (
    H8Case("H801", "action_failure", "ordinary action failure is an observation", "observe"),
    H8Case("H802", "structural_failure", "structural failure reaches FailurePolicy", "directive"),
    H8Case("H803", "dependency", "production agent code does not import evaluation", "no_import"),
    H8Case("H804", "compiler", "unknown tool cannot pass static checking", "compile_error"),
    H8Case("H805", "workspace", "mutation without scoped workspace is rejected", "unverified"),
    H8Case("H806", "budget", "one RunBudget bounds transitions and recoveries", "bounded"),
    H8Case("H807", "completion", "goal completion requires action and answer evidence", "not_complete"),
    H8Case("H808", "runtime_path", "ordinary observation does not invoke Planner.replan", "no_replan"),
)


def canonical_dataset() -> list[dict[str, str]]:
    return [asdict(case) for case in CASES]


def dataset_hash() -> str:
    payload = json.dumps(
        {"version": DATASET_VERSION, "cases": canonical_dataset()},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


__all__ = ["CASES", "DATASET_VERSION", "H8Case", "canonical_dataset", "dataset_hash"]
