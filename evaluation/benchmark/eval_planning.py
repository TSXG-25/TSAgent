#!/usr/bin/env python3
"""eval_planning — Planning Quality Benchmark（v2.0-A）。

Evaluation Precedes Optimization（ADR-0011）：
    Stage 2 先建立 Dataset + Validator + metrics + benchmark，不改 Agent 行为。

本文件两个职责：
    1. `evaluate_plan(plan, scenario)` → 供 Stage 3 Planner 接入的评估入口
       （plan = planner 输出，scenario = dataset 场景）。
    2. `evaluate_dataset()` → 用 golden plans 自检 dataset + validators
       （golden 对 golden 必须 100% PASS，证明 validators 不误报、dataset 自洽）。

输出：
    - MetricsV2（Planning 维度 + Uncertainty 横切维度）
    - Fail Board（每场景归因：Goal 漏覆盖 / 约束违反 / 结构错误）
"""
import json
import os
import sys

sys.path.insert(0, ".")

from evaluation.metrics_v2 import MetricsV2
from evaluation.benchmark.plan_validator import validate_structural
from evaluation.benchmark.semantic_validator import validate_semantic

PLAN_DIR = "evaluation/datasets/planning"


def load_scenarios() -> list:
    scenarios = []
    for fn in sorted(os.listdir(PLAN_DIR)):
        if not fn.endswith(".json"):
            continue
        with open(os.path.join(PLAN_DIR, fn)) as f:
            scenarios.append(json.load(f))
    return scenarios


def golden_plan(scenario: dict) -> dict:
    """把 dataset 的 golden_tasks 转成 planner 输出格式（TaskList dict）。

    golden task 是"参考计划"，字段子集：id/verb/target/goal/dependencies。
    缺的字段（target_type/success_condition）按契约补默认，保证 Structural 能过。
    """
    tasks = []
    for i, gt in enumerate(scenario.get("golden_tasks", []) or []):
        tt = "file" if (gt.get("target") or "").endswith(".py") else "text"
        tasks.append({
            "id": gt.get("id", f"task-{i + 1}"),
            "verb": gt.get("verb", "read"),
            "target": gt.get("target", ""),
            "target_type": gt.get("target_type", tt),
            "goal": gt.get("goal", ""),
            "description": gt.get("goal", ""),
            "success_condition": f"{gt.get('goal', '')}完成",
            "dependencies": gt.get("dependencies", []),
        })
    return {"tasks": tasks}


def evaluate_plan(plan: dict, scenario: dict) -> dict:
    """评估单个 planner 输出。返回结构化结果（供 eval 汇总 / Fail Board）。

    Abstention 场景（expect_abstention）：Structural 不适用（结构是"有没有"，
    Abstain 本质是"故意没有"），只做 Semantic 的 abstention 检查。
    """
    merep = validate_semantic(plan, scenario)

    if scenario.get("expect_abstention"):
        return {
            "structural_valid": True,  # N/A，语义层判定
            "semantic_valid": merep.valid,
            "checks": merep.checks,
            "attribution": {
                "structural": [],
                "goal_coverage": [],
                "constraints": [],
                "abstention": [
                    e for e in merep.errors if "Abstain" in e or "False Confidence" in e
                ],
            },
        }

    srep = validate_structural(plan)

    # 归因归类（Fail Board）
    attribution = {
        "structural": srep.errors,
        "goal_coverage": merep.missed_targets,
        "constraints": merep.violated_constraints,
        "abstention": [],
    }
    return {
        "structural_valid": srep.valid,
        "semantic_valid": merep.valid,
        "checks": {**srep.checks, **merep.checks},
        "attribution": attribution,
    }


def aggregate(scenario_results: list, scenarios: list) -> MetricsV2:
    """把逐场景评估结果汇总为 MetricsV2（Planning + Uncertainty 横切）。

    Planning 指标只在非 abstention 场景统计；abstention 场景只贡献
    Uncertainty 横切指标（correct_abstention / false_confidence）。
    """
    m = MetricsV2()

    planning_pairs = [
        (r, s) for r, s in zip(scenario_results, scenarios)
        if not s.get("expect_abstention")
    ]
    n = len(planning_pairs)
    if n > 0:
        m.goal_coverage = sum(
            1 for r, _ in planning_pairs if r["checks"].get("goal_coverage", False)
        ) / n
        m.constraint_detection = sum(
            1 for r, _ in planning_pairs if r["checks"].get("constraints", False)
        ) / n
        m.task_completeness = sum(
            1 for r, _ in planning_pairs if r["structural_valid"]
        ) / n
        m.dependency_correctness = sum(
            1 for r, _ in planning_pairs
            if r["checks"].get("deps_exist", True) and r["checks"].get("deps_acyclic", True)
        ) / n
        m.execution_order = sum(
            1 for r, _ in planning_pairs if r["checks"].get("deps_ordered", True)
        ) / n

    # Uncertainty 横切：仅统计有 expect_abstention 的场景
    abst_pairs = [
        (r, s) for r, s in zip(scenario_results, scenarios)
        if s.get("expect_abstention")
    ]
    if abst_pairs:
        n_abs = len(abst_pairs)
        m.correct_abstention = sum(
            1 for r, _ in abst_pairs if r["checks"].get("abstention", False)
        ) / n_abs
        m.false_confidence = 1.0 - m.correct_abstention
    return m


def evaluate_dataset() -> tuple:
    """golden self-check：dataset 的 golden plans 必须 100% 通过 validators。

    Returns:
        (MetricsV2, list[Fail Board entries], bool all_pass)
    """
    scenarios = load_scenarios()
    results = []
    failboard = []
    for s in scenarios:
        r = evaluate_plan(golden_plan(s), s)
        results.append(r)
        if not (r["structural_valid"] and r["semantic_valid"]):
            failboard.append({"id": s["id"], "attribution": r["attribution"]})
    metrics = aggregate(results, scenarios)
    return metrics, failboard, len(failboard) == 0


def main():
    from evaluation.benchmark.contract_verification import verify as verify_contract
    if not verify_contract():
        print("⚠️  Resolver Contract 已变化 —— 先解决 Contract Verification 再评估。")

    print("Planning Quality Benchmark（v2.0-A · golden self-check）\n")
    metrics, failboard, all_pass = evaluate_dataset()
    for k, v in metrics.to_dict().items():
        if v > 0.0:
            print(f"  {k:28s} {v:.3f}")

    print(f"\nGolden self-check: {'PASS' if all_pass else 'FAIL'}")
    if failboard:
        print("Fail Board:")
        for fb in failboard:
            print(f"  ✗ {fb['id']}: {fb['attribution']}")
    return 0 if all_pass else 1


if __name__ == "__main__":
    sys.exit(main())
