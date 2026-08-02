# ADR-0006: Capability Lifecycle

- 状态: Accepted
- 日期: 2026-08
- 关联: ADR-0001..0005

---

## 背景

v1 架构冻结后，新增能力最容易破坏稳定性（新的平行模型 / 新的执行路径）。
本 ADR 冻结**能力的生命周期**：任何能力必须先有 Dataset，再写实现。

## 决策

### Capability Lifecycle（每个能力的固定生命周期）

```
Idea
 ↓
Dataset          # 先写 task.json + fixture + verify.py + expected metrics
 ↓
Benchmark        # 先跑基线，确认能力缺口可量化
 ↓
Implementation   # 再写 Rule / Tool / Prompt（不动核心架构）
 ↓
Regression      # 对比 main，确认质量不降
 ↓
Merge
 ↓
Freeze
```

禁止："想到功能就写 `XxxManager.py`"。

### 新增能力前的两个问题

1. **它属于哪一个现有模型**（Intent / Task / ExecutionPlan / ExecutionResult）？
2. **它提升哪一个可量化指标**（PlanningSuccess / GroundingRecall / ExecutionSuccess / Latency / Cost 等）？

两个问题都回答不了 → 该能力不进入系统。

### 适用范围

权限系统 / 缓存 / 审批 / 预算 / 多 Agent / 恢复 / 回放……一律先
`evaluation/datasets/<capability>/`，再写实现。

## 影响

- 未来新增能力的入口是 `evaluation/datasets/`，而非 `agent/`。
- v1 DoD（Release Checklist）要求"所有新增能力都有 Dataset"。
