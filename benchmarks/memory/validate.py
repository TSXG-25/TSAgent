#!/usr/bin/env python3
"""Benchmark Validation — ADR-0014。

The memory-recall dataset and the continuation dataset are validated
separately. Continuation cases use a structured contract schema; they are not
validated by looking for a keyword in the final natural-language answer.
"""
import os
import sys
from collections import Counter

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from agent.conversation import classify_conversation_intent
from benchmarks.memory.cases import build_cases, build_continuation_cases
from benchmarks.memory.metadata import benchmark_metadata
from benchmarks.memory.runner import (
    canonicalize,
    validate,
    validate_reference_fixture,
    _forbidden_groups,
    _expected_groups,
    _expected_set,
)


VALID_CONTINUATION_CONTRACTS = {
    "CONTINUE_PLAN", "CONTINUE_CHAT", "CONTINUE_REFERENCE",
}
CONTINUE_PLAN_MARKERS = (
    "继续执行", "继续任务", "继续做", "继续处理", "继续完成",
    "完成剩余任务", "恢复任务", "接着做", "接着执行",
)
CONTINUE_CHAT_MARKERS = {
    "继续讲", "继续解释", "继续回答", "接着说", "展开说",
}
CONTRACT_REQUIRED_FIELDS = {
    "CONTINUE_PLAN": {
        "intent", "requires_execution", "progress_required", "verification_required",
    },
    "CONTINUE_CHAT": {
        "intent", "requires_execution", "last_answer_required", "answer_anchor",
    },
    "CONTINUE_REFERENCE": {
        "intent", "requires_execution", "reference_target", "clarify_on_conflict",
    },
}


def _validate_continuation_definition(case, problems: list[str]) -> None:
    contract = case.continuation_contract
    if case.validation_mode != "runtime_contract":
        problems.append(f"{case.id}: continuation case 必须使用 runtime_contract validator")
    if contract not in VALID_CONTINUATION_CONTRACTS:
        problems.append(f"{case.id}: 未知 continuation contract {contract!r}")
        return

    trigger = case.turns[-1].strip().lower() if case.turns else ""
    if contract == "CONTINUE_PLAN" and not any(
        marker.lower() in trigger for marker in CONTINUE_PLAN_MARKERS
    ):
        problems.append(f"{case.id}: CONTINUE_PLAN 使用了歧义触发词 {case.turns[-1]!r}")
    if contract == "CONTINUE_CHAT" and trigger not in CONTINUE_CHAT_MARKERS:
        problems.append(f"{case.id}: CONTINUE_CHAT 触发词未冻结: {case.turns[-1]!r}")

    if contract == "CONTINUE_REFERENCE":
        fixture_problems = validate_reference_fixture(case)
        problems.extend(f"{case.id}: {problem}" for problem in fixture_problems)
        if not case.fixture_target:
            problems.append(f"{case.id}: CONTINUE_REFERENCE 缺少 fixture_target")

    derived = classify_conversation_intent(None, case.turns[-1]).name if case.turns else ""
    if derived != contract:
        problems.append(
            f"{case.id}: contract={contract}，但冻结分类器得到 {derived}"
        )

    expectations = case.contract_expectations or {}
    missing = CONTRACT_REQUIRED_FIELDS[contract] - set(expectations)
    if missing:
        problems.append(f"{case.id}: contract expectations 缺少 {sorted(missing)}")
    if case.expected or case.expected_any_of or case.forbidden_any_of:
        problems.append(
            f"{case.id}: structured continuation 不得使用自然语言 expected/forbidden"
        )


def _validate_dataset(label: str, cases: list) -> tuple[list[str], dict]:
    problems: list[str] = []
    expected_sets: Counter[tuple] = Counter()
    metadata = benchmark_metadata(cases, benchmark_name=label)

    for case in cases:
        if case.validation_mode == "runtime_contract":
            _validate_continuation_definition(case, problems)
        else:
            expected = _expected_set(case)
            if not expected:
                problems.append(f"{case.id}: expected 为空（违反 ADR-0014 C2）")
                continue

            positive_examples = case.positive_examples or [
                f"答案是 {term}。" for term in expected
            ]
            if not positive_examples:
                problems.append(f"{case.id}: 未声明 positive_examples（违反 ADR-0014 C4）")
            for positive in positive_examples:
                passed, _ = validate(case, positive)
                if not passed:
                    problems.append(
                        f"{case.id}: 正例误判 → {positive!r} 被拒绝（false negative）"
                    )
                    break

            negative_examples = case.negative_examples
            if not negative_examples:
                problems.append(f"{case.id}: 未声明 negative_examples（违反 ADR-0014 C4）")
                negative_examples = []
            for wrong in negative_examples:
                passed, _ = validate(case, wrong)
                if passed:
                    problems.append(
                        f"{case.id}: 反例误判 → {wrong!r} 被接受（false positive）"
                    )
                    break

        # C1 uniqueness. Structured cases include their contract expectations;
        # text cases include canonicalized expected/forbidden groups.
        expected_sets[
            (
                case.group,
                case.sub,
                tuple(case.turns),
                tuple(
                    tuple(sorted(canonicalize(term) for term in group))
                    for group in _expected_groups(case)
                ),
                tuple(
                    tuple(sorted(canonicalize(term) for term in group))
                    for group in _forbidden_groups(case)
                ),
                tuple(sorted((case.contract_expectations or {}).items())),
                case.metric_scope,
            )
        ] += 1

    for key, count in expected_sets.items():
        if count > 1:
            group, sub, turns, expected, forbidden, contract, metric_scope = key
            problems.append(
                f"{group}/{sub}: 输入 {turns}、期望 {expected}、禁止项 {forbidden}、"
                f"contract {contract} 完全重复 {count} 次"
            )
    return problems, metadata


def main() -> int:
    memory_cases = build_cases(n_per_sub=10, fill_turns=4)
    continuation_cases = build_continuation_cases(n_per_sub=10)
    datasets = [
        ("memory-fuzz", memory_cases),
        ("conversation-continuation", continuation_cases),
    ]

    all_problems: list[str] = []
    metadata: list[dict] = []
    for label, cases in datasets:
        problems, info = _validate_dataset(label, cases)
        all_problems.extend(f"{label}: {problem}" for problem in problems)
        metadata.append(info)

    if all_problems:
        print(f"Benchmark Validation: FAIL ({len(all_problems)} 个评测器缺陷)")
        for problem in all_problems[:30]:
            print(f"  ✗ {problem}")
        return 1

    for info in metadata:
        print(
            "Benchmark Validation: PASS "
            f"[{info['benchmark_name']}]（{info['case_count']} 个 case；"
            f"version={info['benchmark_version']}；"
            f"dataset_hash={info['dataset_hash']}；"
            f"fixture_manifest_hash={info['fixture_manifest_hash']}）"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
