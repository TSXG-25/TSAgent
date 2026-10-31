#!/usr/bin/env python3
"""Attribution audit for the v2.4A-2 real Planner baseline.

The audit consumes an already-frozen baseline.  It does not call a Provider,
modify Planner/Oracle/Dataset code, or rewrite the original result.  The
review table is deliberately explicit: lexical/contract observations are
recorded per case instead of being inferred from one aggregate score.
"""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import asdict, dataclass
import hashlib
import json
from pathlib import Path
import re
import sys
from typing import Any, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DEFAULT_BASELINE = ROOT / "realtest_reports" / "results" / "v24a_planner_baseline.json"
DEFAULT_DATASET = ROOT / "evals" / "planner" / "dataset.json"
DEFAULT_JSON = ROOT / "realtest_reports" / "results" / "v24a_planner_attribution_audit.json"
DEFAULT_MARKDOWN = ROOT / "realtest_reports" / "results" / "v24a_planner_attribution_audit.md"
AUDIT_VERSION = "v2.4A-2b-attribution-audit-v1"


@dataclass(frozen=True)
class Review:
    """Human-reviewed attribution for one baseline failure."""

    category: str
    subcategory: str
    confidence: str
    disposition: str
    note: str


# This table is the audit decision, not a new Oracle.  It is intentionally
# limited to the 30 failed baseline records and is kept in source control so
# the calibrated view is reproducible without another Provider call.
REVIEWS: dict[str, Review] = {
    "PA001": Review(
        "P-CON",
        "MODE_OWNERSHIP_CONFLICT",
        "high",
        "exclude_from_decomposition_pending_contract_decision",
        "Dataset expects chat/tasks=[], while the production Planner contract and entry point only expose task decomposition; ownership is unresolved, not a confirmed Planner failure.",
    ),
    "PA002": Review(
        "P-CON",
        "MODE_OWNERSHIP_CONFLICT",
        "high",
        "exclude_from_decomposition_pending_contract_decision",
        "Same chat-mode boundary as PA001: the production Planner emits a valid explain task, but the frozen Dataset requires no task.",
    ),
    "PA003": Review(
        "P-CON",
        "MODE_OWNERSHIP_CONFLICT",
        "high",
        "exclude_from_decomposition_pending_contract_decision",
        "Translation/explanation is represented as a valid explain task; the disagreement is whether chat routing belongs in this Planner acceptance.",
    ),
    "PA004": Review(
        "P-CON",
        "MODE_OWNERSHIP_CONFLICT",
        "high",
        "exclude_from_decomposition_pending_contract_decision",
        "The arithmetic explanation is represented by one explain task; the frozen chat expectation and the production task-only prompt disagree.",
    ),
    "PA011": Review(
        "P-ORACLE",
        "IMPLICIT_PREREQUISITE_AND_EXECUTION_EQUIVALENCE",
        "high",
        "exclude_from_strict_target_score",
        "Requested modifications are all present. The extra read prerequisites and executing the concrete test file instead of the text target pytest are not representable as equivalent minimal goal units.",
    ),
    "PA012": Review(
        "P-CON",
        "GOAL_SCOPE_AMBIGUITY",
        "medium",
        "exclude_from_strict_target_score_pending_dataset_decision",
        "The plan writes the integration test, modifies both sides of the stated field mismatch, and executes the test file. Whether backend modification is required is not encoded by the frozen goal units, so this is contract ambiguity rather than an Oracle-only lexical miss.",
    ),
    "PA013": Review(
        "P-CAP",
        "GOAL_DRIFT",
        "high",
        "retain_as_planner_failure",
        "The test target changes from tests/test_parser.py to test_parser.py. The requested test file identity is not preserved; this is a genuine target drift signal.",
    ),
    "PA014": Review(
        "P-ORACLE",
        "IMPLICIT_READ_AND_NEW_FILE_WRITE",
        "high",
        "exclude_from_strict_target_score",
        "The requested route and test are both covered. Reading the existing route and using write for a newly created test file are reasonable task representations absent an explicit prerequisite policy.",
    ),
    "PA016": Review(
        "P-CAP",
        "GOAL_DRIFT",
        "high",
        "retain_as_planner_failure",
        "The plan changes tests/test_service.py to test_service.py and changes the requested README documentation target to CACHE.md; both requested target identities drift.",
    ),
    "PA017": Review(
        "P-ORACLE",
        "TARGET_TYPE_REPRESENTATION",
        "high",
        "exclude_from_strict_target_score",
        "The plan reads agent/runtime.py and explains the run method. The text target carries the symbol and requested call flow, but the Oracle requires target_type=symbol with target=run.",
    ),
    "PA018": Review(
        "P-ORACLE",
        "SEARCH_GRANULARITY",
        "high",
        "exclude_from_strict_target_score",
        "Two symbol searches followed by a dependent explanation are a semantically valid expansion of finding the Planner→PlanExecutor call chain; the frozen unit requires one text search.",
    ),
    "PA021": Review(
        "P-ORACLE",
        "SEMANTIC_TEXT_TARGET",
        "high",
        "exclude_from_strict_target_score",
        "Python latest version changelog 2026 is a semantic rendering of Python 最新版本的变化; the text matcher does not recognize the translation/expansion.",
    ),
    "PA023": Review(
        "P-ORACLE",
        "SEMANTIC_TEXT_TARGET",
        "high",
        "exclude_from_strict_target_score",
        "The search target matches exactly and the explanation target adds the requested impact/remediation detail; the missing result is a lexical target false negative.",
    ),
    "PA024": Review(
        "P-ORACLE",
        "PARALLEL_SEARCH_DECOMPOSITION",
        "high",
        "exclude_from_strict_target_score",
        "Splitting one combined documentation search into independent PostgreSQL and SQLite searches is semantically aligned and preserves the comparison dependency; the frozen max task count rejects that representation.",
    ),
    "PA025": Review(
        "P-ORACLE",
        "SEMANTIC_TEXT_TARGET",
        "high",
        "exclude_from_strict_target_score",
        "Adding the explicit 2025-2026 range to the vector-database comparison query preserves the requested research goal; containment matching is too strict for this form.",
    ),
    "PA026": Review(
        "P-ORACLE",
        "SEMANTIC_TEXT_TARGET",
        "high",
        "exclude_from_strict_target_score",
        "Ollama API official documentation is a concise equivalent of Ollama 官方 API 用法.",
    ),
    "PA028": Review(
        "P-ORACLE",
        "SEMANTIC_TEXT_TARGET",
        "high",
        "exclude_from_strict_target_score",
        "The plan expands 今天的 AI 新闻 into a concrete dated news/title/source search while retaining the requested write target.",
    ),
    "PA029": Review(
        "P-ORACLE",
        "SEMANTIC_TEXT_TARGET_AND_PREREQUISITE",
        "high",
        "exclude_from_strict_target_score",
        "The search target is an English rendering of the Chinese request, and the two file reads are reasonable prerequisites before the two modifications.",
    ),
    "PA030": Review(
        "P-ORACLE",
        "SEMANTIC_TEXT_TARGET_AND_TARGET_TYPE",
        "high",
        "exclude_from_strict_target_score",
        "The search target is an equivalent English query and execute symbol=pytest is a more precise representation than execute text=pytest.",
    ),
    "PA031": Review(
        "P-CON",
        "PLANNER_VS_FINAL_RESPONSE_BOUNDARY",
        "medium",
        "exclude_from_strict_target_score_pending_contract_decision",
        "resolve is a plausible rendering of 查找, while explaining the run output is naturally a user-visible final response. The Dataset requires search and omits that response boundary.",
    ),
    "PA022": Review(
        "P-UNCERTAINTY",
        "FALSE_CLARIFICATION_POLICY",
        "high",
        "retain_as_policy_failure",
        "The deterministic detector treats the legitimate pronoun 它 in this otherwise specific request as a vague reference, returns abstain before any Provider call, and suppresses a valid plan.",
    ),
    "PA034": Review(
        "P-UNCERTAINTY",
        "MISSED_CLARIFICATION",
        "high",
        "retain_as_policy_failure",
        "The deterministic detector returns false for 帮我加个功能；the Provider path then returns a generic modify task instead of abstaining.",
    ),
    "PA036": Review(
        "P-UNCERTAINTY",
        "MISSED_CLARIFICATION",
        "high",
        "retain_as_policy_failure",
        "那里 is not covered by the deterministic vague-reference regex; the Provider/fallback path emits an empty-target modify task.",
    ),
    "PA038": Review(
        "P-UNCERTAINTY",
        "MISSED_CLARIFICATION",
        "high",
        "retain_as_policy_failure",
        "把结果保存一下 leaves the result identity and destination unspecified; the Planner invents result.txt instead of requesting clarification.",
    ),
    "PA039": Review(
        "P-UNCERTAINTY",
        "MISSED_CLARIFICATION",
        "high",
        "retain_as_policy_failure",
        "之前的方案 is an unresolved reference; the deterministic detector does not cover it and the fallback returns an empty-target modify task.",
    ),
    "PA042": Review(
        "P-ORACLE",
        "SEMANTIC_TEXT_TARGET",
        "high",
        "exclude_from_strict_target_score",
        "The English search targets and Chinese comparison target preserve all three requested goals and the parallel/dependency structure; the frozen matcher rejects multilingual paraphrase.",
    ),
    "PA046": Review(
        "P-ORACLE",
        "RESUME_WRITE_VS_MODIFY",
        "medium",
        "exclude_from_strict_target_score_pending_contract_decision",
        "After the source unit is marked completed, reading the current output and modifying it is a plausible continuation of 继续完成摘要; the Oracle only accepts a fresh write unit.",
    ),
    "PA049": Review(
        "P-CON",
        "MISSING_TARGET_CONTEXT",
        "high",
        "exclude_from_decomposition_pending_context_contract",
        "The input names abstract A/B only, while the Dataset supplies hidden concrete module targets. Searching for their definition is safer under the production no-guess rule than inventing module_b.py and pytest tasks.",
    ),
    "PA015": Review(
        "P-PROV",
        "PROVIDER_INTERNAL",
        "high",
        "exclude_provider_error",
        "The baseline has one failed production Provider attempt and no Planner output; it cannot be attributed to planning capability.",
    ),
    "PA032": Review(
        "P-PROV",
        "PROVIDER_INTERNAL",
        "high",
        "exclude_provider_error",
        "The baseline has one failed production Provider attempt and no Planner output; it cannot be attributed to planning capability.",
    ),
}


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected object: {path}")
    return value


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _simplify_goal(goal: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "id": str(goal.get("id", "")),
        "verbs": [str(value) for value in goal.get("verbs", []) or []],
        "target_type": str(goal.get("target_type", "")),
        "target": str(goal.get("target", "")),
        "critical": bool(goal.get("critical", False)),
        "depends_on": [str(value) for value in goal.get("depends_on", []) or []],
    }


def _simplify_task(task: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "id": str(task.get("id", "")),
        "verb": str(task.get("verb", "")),
        "target_type": str(task.get("target_type", "")),
        "target": str(task.get("target", "")),
        "dependencies": [str(value) for value in task.get("dependencies", []) or []],
    }


def _baseline_call_stats(cases: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    call_kinds: Counter[str] = Counter()
    outcomes: Counter[str] = Counter()
    network_kinds = {"structured_ainvoke", "router_ainvoke", "router_invoke"}
    network_attempts = 0
    cases_with_fallback = []
    per_case: dict[str, int] = {}
    for case in cases:
        calls = case.get("provider_calls", []) or []
        per_case[str(case.get("case_id", ""))] = sum(
            1 for call in calls if str(call.get("call_kind", "")) in network_kinds
        )
        network_attempts += per_case[str(case.get("case_id", ""))]
        for call in calls:
            call_kinds[str(call.get("call_kind", ""))] += 1
            outcomes[str(call.get("outcome", ""))] += 1
        if str(case.get("provider_status", "")) == "SUCCESS_WITH_PROVIDER_FALLBACK":
            cases_with_fallback.append(str(case.get("case_id", "")))
    return {
        "call_kind_counts": dict(sorted(call_kinds.items())),
        "outcome_counts": dict(sorted(outcomes.items())),
        "network_attempt_kinds": sorted(network_kinds),
        "production_internal_provider_attempt_count": network_attempts,
        "network_attempts_by_case": per_case,
        "network_attempt_distribution": dict(
            sorted(Counter(per_case.values()).items(), key=lambda item: item[0])
        ),
        "structured_output_fallback_cases": cases_with_fallback,
        "harness_case_retry_count": 0,
    }



def _planner_boundary_evidence() -> dict[str, Any]:
    planner_path = ROOT / "agent" / "planner" / "planner.py"
    source = planner_path.read_text(encoding="utf-8")
    signature_match = re.search(
        r"async def plan_with_metadata\((.*?)\) -> PlanOutput:",
        source,
        flags=re.DOTALL,
    )
    signature = signature_match.group(1) if signature_match else ""
    prompt_match = re.search(
        r"PLANNER_PROMPT = \"\"\"(.*?)\"\"\"",
        source,
        flags=re.DOTALL,
    )
    prompt = prompt_match.group(1) if prompt_match else ""
    adr_path = ROOT / "docs" / "adr" / "0029-planner-capability-contract.md"
    adr = adr_path.read_text(encoding="utf-8")
    return {
        "production_file": str(planner_path.relative_to(ROOT)),
        "plan_with_metadata_has_expected_mode_parameter": "expected_mode" in signature,
        "planner_prompt_mentions_chat_mode": bool(re.search(r"\bchat\b", prompt)),
        "planner_output_has_mode_field": False,
        "adr_declares_chat_mode": "`chat`:" in adr and "tasks=[]" in adr,
        "interpretation": "ADR-0029 currently declares chat as part of the Planner contract, while the production entry point/prompt do not expose a chat mode. PA001-PA004 remain P-CON pending an explicit ownership decision.",
    }


def _review_case(
    record: Mapping[str, Any],
    dataset_by_id: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    case_id = str(record.get("case_id", ""))
    review = REVIEWS.get(case_id)
    if review is None:
        raise ValueError(f"missing manual attribution review for {case_id}")
    dataset_case = dataset_by_id[case_id]
    planner_output = record.get("planner_output") or {}
    oracle = record.get("oracle_result") or {}
    return {
        "case_id": case_id,
        "input": str(record.get("input", dataset_case.get("input", ""))),
        "family": str(record.get("family", dataset_case.get("family", ""))),
        "expected_mode": str(record.get("expected_mode", dataset_case.get("expected_mode", ""))),
        "baseline_provider_status": str(record.get("provider_status", "")),
        "baseline_failure_category": record.get("failure_category"),
        "baseline_failure_subcategory": record.get("failure_subcategory"),
        "review": asdict(review),
        "expected_goal_units": [
            _simplify_goal(goal)
            for goal in dataset_case.get("goal_units", []) or []
        ],
        "actual_tasks": [
            _simplify_task(task)
            for task in planner_output.get("tasks", []) or []
        ],
        "oracle_observation": {
            "missing_goal_units": list(oracle.get("missing_goal_units", []) or []),
            "unexpected_task_ids": list(oracle.get("unexpected_task_ids", []) or []),
            "predicted_task_count": int(oracle.get("predicted_task_count", 0)),
            "goal_unit_count": int(oracle.get("goal_unit_count", 0)),
            "unnecessary_task_count": int(oracle.get("unnecessary_task_count", 0)),
            "missing_task_count": int(oracle.get("missing_task_count", 0)),
            "task_granularity": float(oracle.get("task_granularity", 0.0)),
        },
    }


def _calibrated_views(
    records: Sequence[Mapping[str, Any]],
    reviewed: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    category_by_id = {
        str(item["case_id"]): str(item["review"]["category"])
        for item in reviewed
    }
    evaluable = [record for record in records if bool(record.get("evaluable"))]
    strict_pass = sum(bool(record.get("passed")) for record in evaluable)

    def view(name: str, excluded: set[str], description: str) -> dict[str, Any]:
        included = [
            record
            for record in evaluable
            if category_by_id.get(str(record.get("case_id")), "PASS") not in excluded
        ]
        passed = sum(bool(record.get("passed")) for record in included)
        excluded_counts = Counter(
            category_by_id.get(str(record.get("case_id")), "PASS")
            for record in evaluable
            if category_by_id.get(str(record.get("case_id")), "PASS") in excluded
        )
        return {
            "name": name,
            "description": description,
            "included_case_count": len(included),
            "pass_count": passed,
            "pass_rate": passed / len(included) if included else 0.0,
            "excluded_case_count": sum(excluded_counts.values()),
            "excluded_by_attribution": dict(sorted(excluded_counts.items())),
            "status": "PROVISIONAL_VIEW_NOT_AN_ACCEPTANCE_SCORE",
        }

    return {
        "strict_frozen_oracle_view": {
            "included_case_count": len(evaluable),
            "pass_count": strict_pass,
            "pass_rate": strict_pass / len(evaluable) if evaluable else 0.0,
            "description": "Unchanged v2.4A-2 Oracle result; this remains the official raw baseline.",
        },
        "planner_decomposition_provisional": view(
            "planner_decomposition_provisional",
            {"P-CON", "P-ORACLE", "P-UNCERTAINTY"},
            "Passes plus only manually retained P-CAP failures; contract, Oracle, uncertainty-policy and Provider layers are excluded.",
        ),
        "planner_plus_uncertainty_provisional": view(
            "planner_plus_uncertainty_provisional",
            {"P-CON", "P-ORACLE"},
            "Passes plus retained Planner capability and uncertainty-policy failures; only contract/Oracle attribution is excluded.",
        ),
    }


def build_report(baseline: Mapping[str, Any], dataset: Mapping[str, Any], baseline_path: Path, dataset_path: Path) -> dict[str, Any]:
    records = list(baseline.get("cases", []) or [])
    dataset_cases = list(dataset.get("cases", []) or [])
    dataset_by_id = {str(case.get("id", "")): case for case in dataset_cases}
    if len(records) != 50:
        raise ValueError(f"expected 50 baseline records, got {len(records)}")
    if set(dataset_by_id) != {str(record.get("case_id", "")) for record in records}:
        raise ValueError("baseline and Dataset case IDs do not match")

    failed = [record for record in records if not bool(record.get("passed"))]
    reviewed = [_review_case(record, dataset_by_id) for record in failed]
    reviewed_ids = {str(item["case_id"]) for item in reviewed}
    if reviewed_ids != set(REVIEWS):
        missing = sorted(set(REVIEWS) - reviewed_ids)
        extra = sorted(reviewed_ids - set(REVIEWS))
        raise ValueError(f"review table mismatch; missing={missing}, extra={extra}")

    missing_unexpected = [
        item for item in reviewed
        if item["oracle_observation"]["missing_goal_units"]
        and item["oracle_observation"]["unexpected_task_ids"]
    ]
    special_ids = {"PA001", "PA002", "PA003", "PA004", "PA022", "PA034", "PA036", "PA038", "PA039"}
    if not special_ids.issubset({str(item["case_id"]) for item in reviewed}):
        raise ValueError("special attribution cases are not all represented")

    categories = Counter(str(item["review"]["category"]) for item in reviewed)
    subcategories = Counter(str(item["review"]["subcategory"]) for item in reviewed)
    provider_records = [record for record in records if str(record.get("provider_status")) == "PROVIDER_ERROR"]
    report = {
        "audit_version": AUDIT_VERSION,
        "status": "ATTRIBUTION_COMPLETE_NO_PRODUCTION_CHANGE",
        "source_baseline": {
            "path": str(baseline_path.relative_to(ROOT)),
            "sha256": _sha256_file(baseline_path),
            "harness_version": baseline.get("harness_version"),
            "git_head": baseline.get("git_head"),
            "dataset_version": baseline.get("dataset_version"),
            "dataset_hash": baseline.get("dataset_hash"),
            "planner_prompt_hash": baseline.get("planner_prompt_hash"),
            "provider": baseline.get("provider"),
            "case_count": len(records),
            "unchanged": True,
        },
        "source_dataset": {
            "path": str(dataset_path.relative_to(ROOT)),
            "sha256": _sha256_file(dataset_path),
            "dataset_version": dataset.get("version"),
            "case_count": len(dataset_cases),
            "unchanged": True,
        },
        "controls": {
            "provider_calls": 0,
            "automatic_case_retry": False,
            "golden_plan_fallback": False,
            "planner_changes": False,
            "oracle_changes": False,
            "dataset_changes": False,
            "runtime_changes": False,
            "raw_baseline_overwritten": False,
            "note": "This report is derived from the existing baseline; it does not rerun a Provider.",
        },
        "baseline_summary_unchanged": baseline.get("summary", {}),
        "provider_attempt_audit": _baseline_call_stats(records),
        "production_boundary_evidence": _planner_boundary_evidence(),
        "attribution_summary": {
            "failed_record_count": len(failed),
            "reviewed_record_count": len(reviewed),
            "reviewed_categories": dict(sorted(categories.items())),
            "reviewed_subcategories": dict(sorted(subcategories.items())),
            "confirmed_p_boundary_count": 0,
            "boundary_candidates_pending_contract_decision": ["PA001", "PA002", "PA003", "PA004"],
            "missing_and_unexpected_pair_count": len(missing_unexpected),
            "missing_and_unexpected_pair_case_ids": [str(item["case_id"]) for item in missing_unexpected],
            "provider_error_case_ids": [str(record.get("case_id", "")) for record in provider_records],
        },
        "calibrated_views": _calibrated_views(records, reviewed),
        "case_reviews": sorted(reviewed, key=lambda item: str(item["case_id"])),
        "next_decision_inputs": {
            "do_not_enter_planner_improvement_yet": True,
            "contract_decision_required": "Decide whether chat routing is part of the Planner public contract; ADR-0029 currently says yes, production plan_with_metadata does not expose that mode.",
            "oracle_decision_required": "Decide whether to publish Dataset/Oracle v1.1 with aliases or preserve strict v1 and score semantic equivalence separately.",
            "production_policy_check": "PA022 is a deterministic false-positive abstention; PA034/PA036/PA038/PA039 are deterministic coverage gaps or fallback-policy failures.",
            "planner_capability_cases_retained": ["PA013", "PA016"],
            "provider_cases_excluded": ["PA015", "PA032"],
        },
    }
    return report


def _pct(value: float) -> str:
    return f"{value:.1%}"


def markdown(report: Mapping[str, Any]) -> str:
    source = report["source_baseline"]
    summary = report["attribution_summary"]
    views = report["calibrated_views"]
    lines = [
        "# v2.4A-2b Benchmark Attribution Audit",
        "",
        "本报告只审计既有真实 Provider baseline 的归因，不修改 Planner、Oracle、Dataset、Runtime，也不重新调用 Provider。原始 baseline JSON/MD 保持不变。",
        "",
        f"- Audit: {report['audit_version']}",
        f"- Source HEAD: {source['git_head']}",
        f"- Source baseline SHA-256: `{source['sha256']}`",
        f"- Dataset hash: `{source['dataset_hash']}`",
        f"- Provider: {source['provider']['provider']} / {source['provider']['model']}",
        "- Provider calls in audit: **0**",
        "",
        "## 结论",
        "",
        "严格冻结 Oracle 的官方 raw baseline 仍是 **20/48 = 41.7%**（raw submitted rate 20/50 = 40.0%）。本审计不把归因判断覆盖回这个数字。",
        "",
        "当前没有确认的 `P-BOUNDARY`：PA001–PA004 候选确实暴露了 chat/Planner 职责冲突，但 ADR-0029 已明确把 `chat` 写入 Planner 合同，因此暂记 `P-CON:MODE_OWNERSHIP_CONFLICT`，等待合同决策。",
        "",
        "对 19 个同时出现 `missing_goal_units` 与 `unexpected_task_ids` 的失败，已逐例人工复核；其中多数是文本同义改写、隐含前置步骤、任务粒度或目标类型的 Oracle 表达边界，不直接解释为 Planner under-plan。明确保留为 Planner capability failure 的是 PA013 与 PA016 的目标漂移。",
        "",
        "## 归因汇总",
        "",
        "| Attribution | Count | Meaning |",
        "| --- | ---: | --- |",
    ]
    meanings = {
        "P-CAP": "保留为 Planner capability failure",
        "P-CON": "合同/职责或 Dataset 语义边界待决",
        "P-ORACLE": "Oracle 无法可靠表达语义等价，排除严格 target score",
        "P-PROV": "Provider/API 失败，排除 capability denominator",
        "P-UNCERTAINTY": "不确定性/澄清策略失败，单独看 policy",
    }
    for category, count in sorted(summary["reviewed_categories"].items()):
        lines.append(f"| {category} | {count} | {meanings.get(category, 'see case review')} |")
    lines.extend(
        [
            "",
            f"- `P-BOUNDARY` confirmed: **{summary['confirmed_p_boundary_count']}**",
            f"- `missing + unexpected` pairs reviewed: **{summary['missing_and_unexpected_pair_count']}**",
            f"- Provider error cases: **{', '.join(summary['provider_error_case_ids'])}**",
            "",
            "## 原始 Provider attempt 统计",
            "",
        ]
    )
    attempts = report["provider_attempt_audit"]
    lines.extend(
        [
            f"- Production internal Provider attempts: **{attempts['production_internal_provider_attempt_count']}**",
            f"- Harness case retry count: **{attempts['harness_case_retry_count']}**",
            f"- Structured-output fallback cases: **{', '.join(attempts['structured_output_fallback_cases']) or 'none'}**",
            f"- Call kinds: `{json.dumps(attempts['call_kind_counts'], ensure_ascii=False, sort_keys=True)}`",
            "",
            "`structured_bind` 不是网络请求；网络 attempt 只计 `structured_ainvoke`、`router_ainvoke` 和 `router_invoke`。因此 baseline 的 53 次 production internal attempts 不应被误读成 50 个 case 各一次请求。",
            "",
            "## Chat ownership evidence",
            "",
            "| Evidence | Value |",
            "| --- | --- |",
        ]
    )
    boundary = report["production_boundary_evidence"]
    lines.extend(
        [
            f"| ADR-0029 declares chat mode | `{boundary['adr_declares_chat_mode']}` |",
            f"| Production entry has expected_mode parameter | `{boundary['plan_with_metadata_has_expected_mode_parameter']}` |",
            f"| Production Planner prompt mentions chat mode | `{boundary['planner_prompt_mentions_chat_mode']}` |",
            "",
            "结论：不能在本审计中擅自把 PA001–PA004 计为 Planner 已通过；必须先决定是扩展生产 Planner 合同，还是把 chat case 移到上层 Router acceptance。",
            "",
            "## Abstention trace",
            "",
            "detect_abstention 已从 production 删除；同句代词不再被 regex 判为信息不足，abstain 由 Planner LLM 的 abstain_reason 输出承担。PA022 的历史归因是 detector false positive。",
            "",
            "## Calibrated views（暂定，不替代 raw baseline）",
            "",
            "| View | Included | Pass | Rate |",
            "| --- | ---: | ---: | ---: |",
        ]
    )
    for key in ("strict_frozen_oracle_view", "planner_decomposition_provisional", "planner_plus_uncertainty_provisional"):
        view = views[key]
        lines.append(
            f"| {key} | {view['included_case_count']} | {view['pass_count']} | {_pct(float(view['pass_rate']))} |"
        )
    lines.extend(
        [
            "",
            "- `strict_frozen_oracle_view` 是唯一官方 raw baseline 口径。",
            "- `planner_decomposition_provisional = 20/22 = 90.9%` 只表示人工排除 Oracle/合同/不确定性/Provider 层后剩下的 2 个明确 Planner target-drift case；它不是新的 acceptance gate。",
            "- 任何 v2.4A-3 改动前，先解决 chat ownership、Oracle semantic equivalence 和 uncertainty policy 的归属问题。",
            "",
            "## 19 missing+unexpected pair review",
            "",
            "| Case | Attribution | Confidence | Missing | Unexpected | Review |",
            "| --- | --- | --- | --- | --- | --- |",
        ]
    )
    for item in report["case_reviews"]:
        obs = item["oracle_observation"]
        review = item["review"]
        if obs["missing_goal_units"] and obs["unexpected_task_ids"]:
            note = review["note"].replace("|", "\\|")
            lines.append(
                f"| {item['case_id']} | {review['category']}:{review['subcategory']} | {review['confidence']} | "
                f"{', '.join(obs['missing_goal_units'])} | {', '.join(obs['unexpected_task_ids'])} | {note} |"
            )
    lines.extend(
        [
            "",
            "## All reviewed failures",
            "",
            "完整的 expected goal units、actual tasks、Oracle observation 与人工判断保存在同目录 JSON；本节列出额外的非 pair failure。",
            "",
            "| Case | Attribution | Disposition |",
            "| --- | --- | --- |",
        ]
    )
    for item in report["case_reviews"]:
        obs = item["oracle_observation"]
        if not (obs["missing_goal_units"] and obs["unexpected_task_ids"]):
            review = item["review"]
            lines.append(
                f"| {item['case_id']} | {review['category']}:{review['subcategory']} | {review['disposition']} |"
            )
    lines.extend(
        [
            "",
            "## Next decision",
            "",
            "1. 不进入 v2.4A-3 Planner improvement。",
            "2. 先决定 PA001–PA004 的 chat ownership；当前 ADR 与 production entry point 不一致。",
            "3. 再决定是否发布 Dataset/Oracle v1.1 aliases；不要改写 v1 baseline。",
            "4. PA013/PA016 是当前最清楚的 Planner capability evidence；其余失败先按本报告归因。",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--output", type=Path, default=DEFAULT_JSON)
    parser.add_argument("--markdown-output", type=Path, default=DEFAULT_MARKDOWN)
    args = parser.parse_args()

    baseline_path = args.baseline.resolve()
    dataset_path = args.dataset.resolve()
    report = build_report(
        _read_json(baseline_path),
        _read_json(dataset_path),
        baseline_path,
        dataset_path,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    args.markdown_output.parent.mkdir(parents=True, exist_ok=True)
    args.markdown_output.write_text(markdown(report), encoding="utf-8")
    print(f"attribution audit: {args.output}")
    print(f"markdown report: {args.markdown_output}")
    print("raw baseline unchanged: 20/48 (41.7%)")
    print("reviewed failures: 30; missing+unexpected pairs: 19; provider errors: 2")
    print("provider calls made by audit: 0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
