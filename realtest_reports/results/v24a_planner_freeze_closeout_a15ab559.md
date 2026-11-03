# v2.4A Planner Closeout

状态：**FROZEN — Context / Evidence Contract**

本报告对应 clean checkout 的精确提交 `a15ab559e276db5bf1c6326179efa86e9d7768cb`。
旧工作树中的未提交 WIP 不在本次证据范围内。

## 验收输入

- Dataset：`v2.4A-planner-v1.1`
- Dataset hash：`8c268b5855d109c7a2be940257ae0acf7edc877793dd5914cc020ae380aae023`
- Planner-owned cases：46
- Provider：DeepSeek / `deepseek-v4-flash`
- 自动 case retry：false
- 跨 Provider fallback：false
- raw evidence：[v24a_planner_freeze_clean_a15ab559.json](v24a_planner_freeze_clean_a15ab559.json)

## Clean run

| 指标 | 结果 |
| --- | ---: |
| submitted / evaluable | 46 / 45 |
| mechanical capability | 39/45（86.7%） |
| schema validity | 100% |
| dependency validity | 100% |
| clarification accuracy | 100% |
| missing task rate | 8.2% |
| overplanning rate | 35.6%（诊断指标） |
| Contract/Oracle failure | 0 |
| Runtime/Integration failure | 0 |
| Provider error | PA012，1 次 |

PA046–PA050 使用 Runtime 投影的 continuation context 后全部通过（5/5）。Planner 只
接收已完成任务、剩余 scope、已建立事实和 opaque Artifact identity；没有接收 raw
Checkpoint、完整 Runtime state 或 golden plan。

## Semantic attribution

机械 Oracle 将以下 case 标为 `P-CAP:UNDER_PLAN`：

`PA021, PA022, PA023, PA029, PA030, PA042`

逐例审计显示这些计划都包含用户要求的动作、合理依赖和可执行任务，未命中原因是 frozen
target matcher 对等价自然语言表达的覆盖边界。因此最终归因是：

```text
P-CAP          0
P-MEASUREMENT  PA021 PA022 PA023 PA029 PA030 PA042
P-PROV        PA012
P-CON/P-INT    0
```

这不是把机械结果改写成 45/45；机械分数、raw output 和历史 v2.4A-2d/v2.4A-3 结果
全部保留。它只说明本次没有证据支持新的系统性 Planner decomposition 缺口。

PA005 的证据也已经正交化：

```text
provider_path = SINGLE_PROVIDER
format_path   = STRUCTURED_TO_RAW_FALLBACK
```

它不是跨 Provider fallback。

## 冻结结论

v2.4A 的 continuation projection 和 evidence semantics 已完成并冻结。剩余 mechanical
overplanning/target-expression 差异列入 measurement calibration/watchlist，不通过
case-specific prompt 或修改旧 Oracle 处理。下一阶段可进入 v2.4B Tool Selection / ReAct。
