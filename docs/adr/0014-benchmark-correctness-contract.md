# ADR-0014: Benchmark Correctness Contract

- 状态: Accepted（Benchmark Correctness + Cross-module Evidence 冻结）
- 日期: 2026-08
- 关联: ADR-0005（评估框架）、ADR-0009（确定性验证）、ADR-0011（评估先行）、ADR-0013（Conversation Runtime）

---

## 背景

v2.1B Memory Fuzz 出现"评测器问题被误认为 Agent 退化"的污染：

```
期望: 快排
实际: 快速排序        → 校验器判 FAIL，但 agent 答案正确（同义词）
期望: 素数
实际: 质数            → 同义词误判
期望: 平方
实际: 写文件执行任务   → 行为定义不清（CONTINUE_PLAN vs CONTINUE_CHAT）
```

原则（ADR-0011 的延伸）：

> **不要为了通过 Benchmark 去修改 Agent；先确保 Benchmark 测的是真正想要的能力。**

## 决策

### Benchmark 必须满足五条正确性要求

```
C1  Expectation 唯一：一个 case 只有一个合理的答案集合（无歧义）。
C2  Synonym 合法：同义词必须规范化（快排=快速排序，素数=质数…），
    校验器按"任一合法同义项命中"判定。
C3  Behavior 定义明确：对“继续/上下文恢复”等行为，必须声明并引用
    ADR-0013 的 continuation_contract，不得在本 ADR 复制或重新定义运行时语义。
C4  可验证：每个 case 的校验器必须通过“正例通过 / 反例拒绝”的
    Benchmark Validation，否则该 case 不得进入 Agent 评测。
C5  来源可追溯：报告必须记录 benchmark_version、dataset_hash；若 case 依赖
    外部 fixture，还必须记录 fixture_manifest_hash；
    数据、期望或 canonicalization 规则变化后，不得把新旧分数直接并入同一 Trend。
```

### Benchmark Validation（新能力）

任何 Benchmark（Execution / Memory / Planning / Reflection …）在跑 Agent 之前，
先跑校验器自检：

```
对每个 case:
  正例（case-specific positive_examples）→ 必须 PASS
  反例（case-specific negative_examples）→ 必须 FAIL
  forbidden_any_of 命中 → 必须 FAIL
否则 → 该 case 标记为评测器缺陷，排除并修复
```

### Expectation Canonicalization

- 期望字段支持 `expected_any_of` 同义词组；每个内层数组表示同一答案的
  合法说法，外层数组表示可接受的任一答案。
- 支持 `forbidden_any_of`，用于拒绝“包含正确关键词但明确表示未完成/不确定”
  的假阳性答案。
- 匹配前必须执行 Unicode NFKC、大小写/大小写折叠和空白规范化。
- 正例、反例由 case 自己声明；正确的 abstention case 可以把“我不知道”
  声明为正例，不能使用全局反例列表强行判定。
- 报告必须输出 `benchmark_version`、`dataset_hash`、fixture case 使用的
  `fixture_manifest_hash` 和 validator 版本。

## 后果

- 趋势数据（70%→75%→82%）只有在 Benchmark 未放宽时才可比。
- 评测器缺陷（false negative / false positive）在 Agent 跑之前被发现。
- ADR-0013 的验收门槛（conversation ≥90%）基于净化后的 Benchmark 判定。

## Continuation Benchmark Binding

Continuation 的运行时语义唯一来源是 [ADR-0013](0013-conversation-runtime-contract.md)。
ADR-0014 只规定评测绑定：

- 每个 continuation case 必须声明 `MemoryCase.continuation_contract`；
- `CONTINUE_PLAN` 的 case 必须使用明确的计划恢复表达，不能把裸“继续”固定
  成某一语义；
- `CONTINUE_PLAN` 必须使用结构化 Runtime evidence 验证计划恢复、执行推进和
  ExecutionVerifier 成功，不能检查自然语言关键词；
- `CONTINUE_REFERENCE` 必须使用 Resolver/Runtime evidence 验证引用目标；
  `CONTINUE_CHAT` 才验证回答内容及 `last_answer` 延续；
- `plan_resume / chat_resume / reference_resume` 单独报告，不计入
  `recent_goal / previous_instruction` 的 Conversation Recall 趋势；
- 报告必须按 `metric_scope` 与 continuation 子类分别给出分母、通过数、异常数和
  非异常通过率；不得把 plan/chat/reference 或超时异常混成一个 Recall 分数；
- Benchmark 不得为了修复 `unfinished_task = 0%` 而反向修改 Agent 行为，
  应先按 ADR-0013 重新标注 case。

### Fixture-backed Continuation Cases

`CONTINUE_REFERENCE` 等依赖工作区文件的 case 必须声明确定性的静态 fixture：

- `fixture_source` 必须位于 benchmark 自己的 fixture 目录；
- `fixture_target` 必须位于 case workspace 的 `output/` 下；
- materialize 前必须检查 source 存在，函数类引用还必须检查目标 symbol 存在；
- 每个 case 必须在 output 清理之后 materialize，在真实 API 调用之前完成 target
  preflight；
- case 结束后必须 teardown，已存在的用户文件恢复原始字节，不得被 benchmark 删除；
- fixture 缺失、路径越界、语法错误或 symbol 缺失必须标记为
  `INVALID_BENCHMARK`，不得计入 Provider Error 或 Agent Capability Rate；
- fixture 内容通过 `fixture_manifest_hash` 纳入报告 provenance，fixture 修改后
  不得与旧报告直接合并 Trend。

## Cross-module Contract Integration（C5 冻结）

跨模块 Contract 不能只由各模块的 Unit Test 证明。以下边界必须至少各有一项
使用真实对象接线的 Integration Test：

```text
Conversation Runtime → Planner
Planner             → Runtime Context
Runtime             → Executor
```

Integration Test 至少应验证：

1. 通过真实 public boundary 调用，而不是只 mock 被测模块的委托方法；
2. 上游产生的 Contract 字段确实抵达下游（例如
   `conversation_snapshot` / `reference_type` 写入 Planner state）；
3. 缺失委托、错误字段名或错误枚举会让测试失败，而不是被宽泛的
   `try/except` 静默吞掉；
4. 生产环境若允许降级，降级也必须留下可观测的诊断事件，不能把“未执行”
   报告成“已完成”。

本轮复跑记录了该原则的一个真实反例：Planner 调用
`ConversationRetriever.runtime_pending()`，但 Retriever 当时没有该委托；
异常被 Planner 的容错分支吞掉，导致 Conversation Contract 没有写入 state，
离线 Unit Test 全绿而真实 Conversation Benchmark 几乎全部失效。该类问题
必须由跨模块回归测试覆盖，不能依赖单模块测试间接推断。

### C5 静态接口与失败可观测性

- Retriever 等跨模块边界必须提供 `Protocol` 或 ABC；CI 至少运行一次
  `mypy` 或 `pyright`，检查调用方依赖的方法确实属于接口。
- Contract 字段/方法缺失在严格测试环境必须抛出 `ContractIntegrationError`，
  不能被 `except Exception: pass` 隐藏。
- 生产环境允许降级，但必须生成 `FailureEvent`：
  `symptom = contract_violation`，并包含缺失方法/字段、边界位置和实际异常
  作为 Evidence；同时发出可观测诊断事件。
- 生产降级的“继续回答”不等于 Contract 成功，禁止把未注入、未执行或未验证
  的结果报告为已完成。
