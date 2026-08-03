# ADR-0007: Behavior Freeze (v1.1) + Runtime Product Contract

- 状态: Accepted
- 日期: 2026-08
- 关联: ADR-0001..0006

---

## 背景

v1.0 冻结架构（ADR-0001..0004），v1.1 冻结**行为**。
用户可观察行为成为新的稳定接口——实现可演进，行为不可破坏。

## 决策

### Runtime Product Contract（五条 Invariant，测试固化于 tests/test_runtime_invariants.py）

```
Inv1  Exception 不退出 Runtime
Inv2  Tool 结果必须经过 AnswerGenerator / Presentation
Inv3  Workflow 返回统一 ExecutionResult
Inv4  不输出 Traceback
Inv5  Planner 只收 PlanningContext（Planner Isolation）
```

违反任意一条 → PR 不合并。

### Planner Isolation Principle

Planner 永不接触：
- Workspace / Repository
- Raw Conversation / User Messages

Planner 只接收 `PlanningContext`（Runtime 已整理好的世界）。

### Evaluation Contract

任何 Capability 必须拥有：

```
Dataset → Benchmark → Regression → Quality Gate
```

无 Dataset 不能 Merge，无 Regression 不能 Merge（ADR-0006 强化为 Contract）。

### v1.1 完成标准

```
E2E ≥90% · Recovery Rate =100% · Runtime Contract PASS
Presentation Contract PASS · No Critical Fail Board Items
```

No Critical Fail > 20/20（Dataset 增长后 100% 无意义）。

## Evidence

### Trigger
Conversation E2E 首跑：Runtime TypeError 泄漏到 CLI（weather 任务）。
### Observation
Recovery Rate = 0；Traceback 出现在用户界面。
### Decision
增加 Runtime Recovery 层（任何异常 → ExecutionResult(FAILED) → 友好回答 → 继续 Session）。
### Validation
011 天气 TypeError 不再崩溃；Invariant 测试 5/5 PASS；Recovery Rate 提升至接近 100%。
