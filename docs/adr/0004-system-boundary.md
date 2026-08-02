# ADR-0004: System Boundary

- 状态: Accepted
- 日期: 2026-08
- 关联: ADR-0001, ADR-0002, ADR-0003

---

## 背景

四模型与 Compiler 四阶段已冻结。本 ADR 冻结**层间依赖规则**，
防止未来重新出现"三套执行体系并存"（D1 教训）或层间旁支。

## 决策

### 依赖图（只允许向下依赖）

```
Runtime ── 唯一协调者（ground / plan / compile / execute）
  │
  ▼
Intent
  │
  ▼
Grounding
  │
  ▼
Planning
  │
  ▼
Compiler
  │
  ▼
Executor
```

### Rule 1 — 只能向下依赖

- Planner 可依赖 `GroundingContext` / `Task`。
- Planner **不可**依赖 `ExecutionPlan` / `Executor`。

### Rule 2 — 不能跨层

- Grounder 绝不能执行 `ToolRegistry.execute()` / `Filesystem.write()` / `Shell.run()`。
- 每层只调用下一层，不跨两层。

### Rule 3 — Runtime 是唯一协调者

- Grounder / Planner / Compiler / Executor **互不调用**。
- 流程编排（ground → plan → compile → execute）全部由 Runtime 负责。
- 未来 Human Approval / Checkpoint / Budget / Interrupt 自然挂在 Runtime。

## 各层边界声明

### Grounding

- 职责：**Grounder reduces the search space, not the decision space。**
- Grounder 给出 Top-N 候选，决策权在 Planner。Grounder 不推理、不替 Planner 做选择。
- 受 `GroundingBudget` 约束：`max_candidates / max_workspace_hits / max_repository_hits / max_latency`。
  500k repo / monorepo 不改 Grounder，只改 budget。

### Compiler

- **Pure Function**：`compile(task, context) -> plan`，同输入永远同输出。
- 不可依赖 LLM / Memory / Conversation / History 等任何动态状态。
- 由此 Replay / Cache / Incremental Compile 自动成立。

### Executor

- **Executor 永远不知道用户问题**。
- 只消费 `ExecutionPlan`，不知道 User Input / Intent / Conversation / Planner Prompt。
- 否则 Executor 会重新开始"自主推理"，退化为 ReAct。

## 生命周期

> 本文档与 ADR-0003 共同构成 **v1 架构冻结**。
> 此后只允许以 Runtime 或既有模型的扩展实现新能力，禁止新增核心模型或执行路径。
