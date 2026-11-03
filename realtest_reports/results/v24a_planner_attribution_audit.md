# v2.4A-2b Benchmark Attribution Audit

本报告只审计既有真实 Provider baseline 的归因，不修改 Planner、Oracle、Dataset、Runtime，也不重新调用 Provider。原始 baseline JSON/MD 保持不变。

- Audit: v2.4A-2b-attribution-audit-v1
- Source HEAD: 6c1743078d67cf1644c22779f40649fb4b9880d2
- Source baseline SHA-256: `5f1d4a6a02325b5973ba20014b791531959f5720d0c268cf986b0e0ffee6bbb1`
- Dataset hash: `7f5b28f608194a324f4244c860a8ed9101bcb7afa3b68e5129632ebfb0290291`
- Provider: deepseek / deepseek-v4-flash
- Provider calls in audit: **0**

## 结论

严格冻结 Oracle 的官方 raw baseline 仍是 **20/48 = 41.7%**（raw submitted rate 20/50 = 40.0%）。本审计不把归因判断覆盖回这个数字。

当前没有确认的 `P-BOUNDARY`：PA001–PA004 候选确实暴露了 chat/Planner 职责冲突，但 ADR-0029 已明确把 `chat` 写入 Planner 合同，因此暂记 `P-CON:MODE_OWNERSHIP_CONFLICT`，等待合同决策。

对 19 个同时出现 `missing_goal_units` 与 `unexpected_task_ids` 的失败，已逐例人工复核；其中多数是文本同义改写、隐含前置步骤、任务粒度或目标类型的 Oracle 表达边界，不直接解释为 Planner under-plan。明确保留为 Planner capability failure 的是 PA013 与 PA016 的目标漂移。

## 归因汇总

| Attribution | Count | Meaning |
| --- | ---: | --- |
| P-CAP | 2 | 保留为 Planner capability failure |
| P-CON | 7 | 合同/职责或 Dataset 语义边界待决 |
| P-ORACLE | 14 | Oracle 无法可靠表达语义等价，排除严格 target score |
| P-PROV | 2 | Provider/API 失败，排除 capability denominator |
| P-UNCERTAINTY | 5 | 不确定性/澄清策略失败，单独看 policy |

- `P-BOUNDARY` confirmed: **0**
- `missing + unexpected` pairs reviewed: **19**
- Provider error cases: **PA015, PA032**

## 原始 Provider attempt 统计

- Production internal Provider attempts: **53**
- Harness case retry count: **0**
- Structured-output fallback cases: **PA001**
- Call kinds: `{"router_ainvoke": 52, "structured_ainvoke": 1, "structured_bind": 1}`

`structured_bind` 不是网络请求；网络 attempt 只计 `structured_ainvoke`、`router_ainvoke` 和 `router_invoke`。因此 baseline 的 53 次 production internal attempts 不应被误读成 50 个 case 各一次请求。

## Chat ownership evidence

| Evidence | Value |
| --- | --- |
| ADR-0029 declares chat mode | `True` |
| Production entry has expected_mode parameter | `False` |
| Production Planner prompt mentions chat mode | `False` |

结论：不能在本审计中擅自把 PA001–PA004 计为 Planner 已通过；必须先决定是扩展生产 Planner 合同，还是把 chat case 移到上层 Router acceptance。

## Abstention trace

| Case | Detector without context | Baseline provider | Attribution |
| --- | :---: | --- | --- |
| PA022 | `True` | NOT_CALLED | P-UNCERTAINTY:FALSE_CLARIFICATION_POLICY |
| PA034 | `False` | SUCCESS | P-UNCERTAINTY:MISSED_CLARIFICATION |
| PA036 | `False` | SUCCESS | P-UNCERTAINTY:MISSED_CLARIFICATION |
| PA038 | `False` | SUCCESS | P-UNCERTAINTY:MISSED_CLARIFICATION |
| PA039 | `False` | SUCCESS | P-UNCERTAINTY:MISSED_CLARIFICATION |

PA022 是 detector 的 false positive：`它` 触发了模糊引用规则，Provider 未被调用。PA034/PA036/PA038/PA039 则未被 detector 拦截，后续 Provider/fallback 生成了非 abstain 结果。

## Calibrated views（暂定，不替代 raw baseline）

| View | Included | Pass | Rate |
| --- | ---: | ---: | ---: |
| strict_frozen_oracle_view | 48 | 20 | 41.7% |
| planner_decomposition_provisional | 22 | 20 | 90.9% |
| planner_plus_uncertainty_provisional | 27 | 20 | 74.1% |

- `strict_frozen_oracle_view` 是唯一官方 raw baseline 口径。
- `planner_decomposition_provisional = 20/22 = 90.9%` 只表示人工排除 Oracle/合同/不确定性/Provider 层后剩下的 2 个明确 Planner target-drift case；它不是新的 acceptance gate。
- 任何 v2.4A-3 改动前，先解决 chat ownership、Oracle semantic equivalence 和 uncertainty policy 的归属问题。

## 19 missing+unexpected pair review

| Case | Attribution | Confidence | Missing | Unexpected | Review |
| --- | --- | --- | --- | --- | --- |
| PA011 | P-ORACLE:IMPLICIT_PREREQUISITE_AND_EXECUTION_EQUIVALENCE | high | g4 | task-1, task-2, task-3, task-7 | Requested modifications are all present. The extra read prerequisites and executing the concrete test file instead of the text target pytest are not representable as equivalent minimal goal units. |
| PA012 | P-CON:GOAL_SCOPE_AMBIGUITY | medium | g4, g5 | task-3, task-5, task-6 | The plan writes the integration test, modifies both sides of the stated field mismatch, and executes the test file. Whether backend modification is required is not encoded by the frozen goal units, so this is contract ambiguity rather than an Oracle-only lexical miss. |
| PA013 | P-CAP:GOAL_DRIFT | high | g2 | task-1, task-2, task-4 | The test target changes from tests/test_parser.py to test_parser.py. The requested test file identity is not preserved; this is a genuine target drift signal. |
| PA014 | P-ORACLE:IMPLICIT_READ_AND_NEW_FILE_WRITE | high | g2 | task-1, task-3 | The requested route and test are both covered. Reading the existing route and using write for a newly created test file are reasonable task representations absent an explicit prerequisite policy. |
| PA016 | P-CAP:GOAL_DRIFT | high | g2, g3 | task-1, task-3, task-4 | The plan changes tests/test_service.py to test_service.py and changes the requested README documentation target to CACHE.md; both requested target identities drift. |
| PA017 | P-ORACLE:TARGET_TYPE_REPRESENTATION | high | g2 | task-2 | The plan reads agent/runtime.py and explains the run method. The text target carries the symbol and requested call flow, but the Oracle requires target_type=symbol with target=run. |
| PA018 | P-ORACLE:SEARCH_GRANULARITY | high | g1 | task-1, task-2 | Two symbol searches followed by a dependent explanation are a semantically valid expansion of finding the Planner→PlanExecutor call chain; the frozen unit requires one text search. |
| PA021 | P-ORACLE:SEMANTIC_TEXT_TARGET | high | g1, g2 | task-1, task-2 | Python latest version changelog 2026 is a semantic rendering of Python 最新版本的变化; the text matcher does not recognize the translation/expansion. |
| PA023 | P-ORACLE:SEMANTIC_TEXT_TARGET | high | g2 | task-2 | The search target matches exactly and the explanation target adds the requested impact/remediation detail; the missing result is a lexical target false negative. |
| PA024 | P-ORACLE:PARALLEL_SEARCH_DECOMPOSITION | high | g2 | task-1, task-3 | Splitting one combined documentation search into independent PostgreSQL and SQLite searches is semantically aligned and preserves the comparison dependency; the frozen max task count rejects that representation. |
| PA025 | P-ORACLE:SEMANTIC_TEXT_TARGET | high | g1 | task-1 | Adding the explicit 2025-2026 range to the vector-database comparison query preserves the requested research goal; containment matching is too strict for this form. |
| PA026 | P-ORACLE:SEMANTIC_TEXT_TARGET | high | g1 | task-1 | Ollama API official documentation is a concise equivalent of Ollama 官方 API 用法. |
| PA028 | P-ORACLE:SEMANTIC_TEXT_TARGET | high | g1 | task-1 | The plan expands 今天的 AI 新闻 into a concrete dated news/title/source search while retaining the requested write target. |
| PA029 | P-ORACLE:SEMANTIC_TEXT_TARGET_AND_PREREQUISITE | high | g1 | task-1, task-2, task-3 | The search target is an English rendering of the Chinese request, and the two file reads are reasonable prerequisites before the two modifications. |
| PA030 | P-ORACLE:SEMANTIC_TEXT_TARGET_AND_TARGET_TYPE | high | g2, g4 | task-2, task-4 | The search target is an equivalent English query and execute symbol=pytest is a more precise representation than execute text=pytest. |
| PA031 | P-CON:PLANNER_VS_FINAL_RESPONSE_BOUNDARY | medium | g1 | task-1, task-4 | resolve is a plausible rendering of 查找, while explaining the run output is naturally a user-visible final response. The Dataset requires search and omits that response boundary. |
| PA042 | P-ORACLE:SEMANTIC_TEXT_TARGET | high | g1, g2, g3 | task-1, task-2, task-3 | The English search targets and Chinese comparison target preserve all three requested goals and the parallel/dependency structure; the frozen matcher rejects multilingual paraphrase. |
| PA046 | P-ORACLE:RESUME_WRITE_VS_MODIFY | medium | g2 | task-1, task-2 | After the source unit is marked completed, reading the current output and modifying it is a plausible continuation of 继续完成摘要; the Oracle only accepts a fresh write unit. |
| PA049 | P-CON:MISSING_TARGET_CONTEXT | high | g2, g3, g4 | task-1 | The input names abstract A/B only, while the Dataset supplies hidden concrete module targets. Searching for their definition is safer under the production no-guess rule than inventing module_b.py and pytest tasks. |

## All reviewed failures

完整的 expected goal units、actual tasks、Oracle observation 与人工判断保存在同目录 JSON；本节列出额外的非 pair failure。

| Case | Attribution | Disposition |
| --- | --- | --- |
| PA001 | P-CON:MODE_OWNERSHIP_CONFLICT | exclude_from_decomposition_pending_contract_decision |
| PA002 | P-CON:MODE_OWNERSHIP_CONFLICT | exclude_from_decomposition_pending_contract_decision |
| PA003 | P-CON:MODE_OWNERSHIP_CONFLICT | exclude_from_decomposition_pending_contract_decision |
| PA004 | P-CON:MODE_OWNERSHIP_CONFLICT | exclude_from_decomposition_pending_contract_decision |
| PA015 | P-PROV:PROVIDER_INTERNAL | exclude_provider_error |
| PA022 | P-UNCERTAINTY:FALSE_CLARIFICATION_POLICY | retain_as_policy_failure |
| PA032 | P-PROV:PROVIDER_INTERNAL | exclude_provider_error |
| PA034 | P-UNCERTAINTY:MISSED_CLARIFICATION | retain_as_policy_failure |
| PA036 | P-UNCERTAINTY:MISSED_CLARIFICATION | retain_as_policy_failure |
| PA038 | P-UNCERTAINTY:MISSED_CLARIFICATION | retain_as_policy_failure |
| PA039 | P-UNCERTAINTY:MISSED_CLARIFICATION | retain_as_policy_failure |

## Next decision

1. 不进入 v2.4A-3 Planner improvement。
2. 先决定 PA001–PA004 的 chat ownership；当前 ADR 与 production entry point 不一致。
3. 再决定是否发布 Dataset/Oracle v1.1 aliases；不要改写 v1 baseline。
4. PA013/PA016 是当前最清楚的 Planner capability evidence；其余失败先按本报告归因。
