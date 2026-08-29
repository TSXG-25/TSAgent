# v2.4A-3 Planner Capability Improvement — Comparison

本文件记录 v2.4A-3 的候选改进结果。它不是 v2.4A 冻结报告：候选运行发生在
`6cf9e0d0` 工作树上，运行时、Dataset、Oracle 没有改动，但当时工作树包含其他未提交
WIP，因此必须保留 `evaluation_working_tree_dirty=true`，并在后续需要时做 clean-checkout
复核。

## Scope and controls

- Planner-owned cases: **46**；PA001–PA004 仍由 Routing ownership 管理。
- Dataset: `v2.4A-planner-v1.1`
- Dataset hash: `8c268b5855d109c7a2be940257ae0acf7edc877793dd5914cc020ae380aae023`
- Provider: `deepseek / deepseek-v4-flash`
- Automatic case retry: **false**
- Golden-plan fallback: **false**
- Uncertainty cases excluded from Planner capability score。
- 未修改 Dataset、Oracle、Runtime、attribution 规则；每轮 raw evidence 均保留。

## Baseline → candidate

| Metric | v2.4A-2d baseline | Round 3 candidate | Delta |
| --- | ---: | ---: | ---: |
| Capability | 20/44 (45.5%) | 37/45 (82.2%) | +36.8 pp |
| Schema validity | 90.9% | 100.0% | +9.1 pp |
| Dependency validity | 90.9% | 100.0% | +9.1 pp |
| Executable plan rate | 45.5% | 82.2% | +36.8 pp |
| Missing task rate | 31.8% | 12.9% | −18.8 pp |
| Overplanning rate | 65.9% | 35.6% | −30.4 pp |
| Clarification accuracy | 90.9% | 100.0% | +9.1 pp |
| Average / P95 tasks | 2.70 / 6 | 2.16 / 4 | −0.55 / −2 |
| P-CAP cases | 24 | 8 | −16 |

Round 2 was `35/45 (77.8%)`; its complete metrics are retained in the JSON comparison and
calibrated report. Round 1 was a provisional iteration before the final deterministic uncertainty
calibration and is not used as the final candidate score.

## Attribution

Round 3 candidate:

- `P-CAP`: 8，全部为 `UNDER_PLAN`：`PA016, PA018, PA021, PA023, PA029, PA042, PA046, PA049`。
- `P-PROV`: 1，`PA012`。
- `P-CON/P-ORACLE`: 0。
- `P-INT`: 0。

这说明 v2.4A-3 的两项改动确实降低了原始的 completeness/clarification 缺口，但尚未
满足 ADR gate 的 `missing_task_rate <= 5%` 与 `overplanning_rate <= 10%`。因此当前状态是：

```text
v2.4A-3 candidate evaluated, not frozen
```

PA049 保留为 context-dependent watchlist，不通过隐藏映射或 case-specific 规则补齐。
PA012 的 Provider Error 不归因于 Planner；另有 PA005 的 raw provider status 为
`SUCCESS_WITH_PROVIDER_FALLBACK`，虽然 harness 控制项为 `provider_fallback=false`，该矛盾
被原样记录，需单独做 provider-path audit，不能悄悄改写为纯 single-provider evidence。

## Separate uncertainty evidence

`v24a_uncertainty_after_planner_hardening.json` 独立得到 **26/27**：false abstention `0`、
abstain precision `100%`、recall `91.7%`；剩余 `U007` 是 `NEEDS_POLICY_WORK`。它不进入
Planner capability 分数。

## Evidence files

- [Round 1 raw](<realtest_reports/results/v24a_planner_improvement_round1.json>)
- [Round 2 raw](<realtest_reports/results/v24a_planner_improvement_round2.json>)
- [Round 2 calibrated](<realtest_reports/results/v24a_planner_improvement_round2_calibrated.json>)
- [Round 3 raw](<realtest_reports/results/v24a_planner_improvement_round3.json>)
- [Round 3 calibrated](<realtest_reports/results/v24a_planner_improvement_round3_calibrated.json>)
- [Separate uncertainty run](<realtest_reports/results/v24a_uncertainty_after_planner_hardening.json>)

完整机器可读摘要见
[v24a_planner_improvement_comparison.json](<realtest_reports/results/v24a_planner_improvement_comparison.json>)。
