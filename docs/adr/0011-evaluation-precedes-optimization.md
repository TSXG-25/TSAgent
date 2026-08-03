# ADR-0011: Evaluation Precedes Optimization

- 状态: Accepted
- 日期: 2026-08
- 决策者: TSAgent 架构

---

## 背景

v1.0 → v1.2 最成功的经验是：每个 Capability 都遵循

```
Dataset → Metric → Benchmark → Implementation → Regression
```

而不是"先写功能，再补测试"。Evaluation 不是测试的附庸，而是设计约束
（v1.2C 的 Determinism FAIL 直接推动 `raw_target > LLM target` 的设计变更，正是 Evaluation 先行的证据）。

## 决策

> **任何新的智能能力，在修改 Agent 行为之前，必须先定义 Dataset、Metric、Benchmark 和 Regression。**

执行顺序锁定（以 v2.0-A Planning Quality 为例）：

```
Stage 1  原则入库（ADR-0009 / 0010 / 0011）
Stage 2  Evaluation 先行
           Dataset → Golden Plans → Validator → metrics → eval_benchmark
Stage 3  Implementation（Planner 最后写）
Stage 4  Integration（Long Horizon Baseline + Trend Gate + Regression）
```

约束：

- 未通过 Stage 2 的 Benchmark（含回归基线），不得进入 Stage 3 修改 Agent 行为。
- 新增智能能力必须挂入既有 Regression 入口（所有历史 Dataset 全量回放），禁止独立沙盒不回归。
- 防止"先写功能，再补测试"——功能可以推迟，Evaluation 不能推迟。

## 后果

- 智能层 PR 结构固定为四件套：dataset + metric + benchmark + regression，缺一不合并。
- 行为改动必须携带"评估基线 vs 改动后"的指标对比。
