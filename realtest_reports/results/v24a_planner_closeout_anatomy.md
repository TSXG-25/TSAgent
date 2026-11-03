# v2.4A-4 Planner Closeout Discovery

本报告只做 failure anatomy 和 evidence integrity audit，不修改 Planner、Dataset、Oracle、
Runtime 或 attribution 规则。

## 结论

Round 3 机械归因为 8 个 `P-CAP:UNDER_PLAN`，但逐案检查后没有形成清晰的共同
decomposition failure：

| 类型 | Case | 判断 |
| --- | --- | --- |
| Target-match boundary | PA018, PA021, PA023, PA042 | 搜索/解释或比较任务均已存在，失败来自 text target 未命中冻结 alias |
| Prerequisite / target boundary | PA016, PA029 | 必要 read、显式 explain/search 或动作存在，被当前 unmatched-task 规则计为 extra |
| Resume-context projection | PA046 | `completed_units` 未传入生产 Planner，直接输入不足以复现 Oracle 的 resume 状态 |
| Resume-context / fixture observability | PA049 | Dataset 隐含 module B、测试文件和 pytest，用户输入没有这些映射；澄清是可解释行为 |

因此现在没有证据支持再做泛化 Prompt hardening。尤其不能为了降低
`overplanning_rate=35.6%` 删除合理的 explain/read task，从而重新制造真正的 missing goal。

## Round 3 mechanical baseline

- Capability: `37/45 (82.2%)`
- Schema validity: `100%`
- Dependency validity: `100%`
- Executable plan rate: `82.2%`
- Missing task rate: `12.9%`
- Overplanning rate: `35.6%`
- Clarification accuracy: `100%`
- P-CAP: `8`，机械上全部 `UNDER_PLAN`
- Provider error: `PA012`
- Runtime / Oracle failure: `0`

## Overplanning metric audit

当前 Oracle 的定义是：

```text
overplanned = task_count > max_tasks OR 存在未匹配 active goal unit 的 task
```

这个定义把以下情况统一视为 overplanning：

- 用户明确要求的最终 explain/compare task，但 text target 只差自然语言表达；
- 修改源文件前必要的 read task；
- 用户明确要求的 search task，但 target 粒度与 canonical target 不完全一致；
- 现有测试文件的动作被模型写成 write，而不是 Dataset 要求的 modify。

所以官方指标必须继续保留，不能在本报告中改写；但它不能直接证明这些 Task 应从计划中删除。

## Provider path audit

PA005 被标记为 `SUCCESS_WITH_PROVIDER_FALLBACK`，但三条调用全部是 DeepSeek：

```text
structured_bind       SUCCESS
structured_ainvoke    STRUCTURED_OUTPUT_REJECTED
router_ainvoke        SUCCESS
```

这实际是同一 Provider 的 structured-output → raw-text fallback，不是跨 Provider fallback。
Harness 状态名把两者混在一起。raw call evidence 保留不变；后续冻结前应让 evidence schema
明确区分 `same_provider_format_fallback` 和 `cross_provider_fallback`。

## Resume case boundary

`realtest_reports/harness/v24a_planner.py` 直接调用生产 `plan_with_metadata(input, ...)`，没有
注入 Dataset 的 `completed_units`、`resume_policy` 或隐含的 A/B 文件映射。因而 PA046、PA049
不能在当前 direct Planner input 下公平判定为 Planner capability failure。不能把 Dataset 私有
golden state 变成 prompt 中的隐藏答案。

## Decision

```text
systemic Planner decomposition gap proven: NO
last generic Prompt fix justified: NO
clean-checkout freeze ready: NO
```

下一步应先解决 evidence naming 与 P12 context contract，再决定是否值得做最后一轮泛化候选。
不得针对 PA016、PA018、PA021、PA023、PA029、PA042、PA046、PA049 添加单 case 规则。

机器可读明细见
[v24a_planner_closeout_anatomy.json](<realtest_reports/results/v24a_planner_closeout_anatomy.json>)。
