# ADR-0009: Non-deterministic Reasoning, Deterministic Validation

- 状态: Accepted
- 日期: 2026-08
- 决策者: TSAgent 架构

---

## 背景

v1.x 建立的三层 Contract（Runtime / Presentation / Resolver）全部以**确定性**为质量基础。
进入 v2.0 Agent Intelligence 后，智能能力（Planning / Decision / Reflection）天然依赖 LLM 的非确定性推理，
与 v1.x 的 Determinism 哲学存在张力：若强制智能层完全确定，则丧失推理能力；若放任非确定，则评估无法回归。

## 决策

> **Agent 可以使用非确定性的推理过程，但系统必须使用确定性的验证标准。**

具体含义：

```
推理过程           → 允许非确定（可因模型、采样不同而不同）
成功与否的判定     → 必须确定（由 Dataset / Verifier / Regression / Metrics 判定）
```

- 智能层（Planner / Executor / Reflector）保留推理自由度：不评估 CoT 写得好不好，不约束模型"怎么想"。
- 评估层必须确定性：一切 PASS / FAIL 由 golden dataset、确定性 verifier、固定 metric 判定，**禁止 LLM 自评**。
- 与 v1.x 的关系：Resolver 的 Determinism 保证"如何理解上下文"稳定；本 ADR 允许智能层推理非确定，
  但验证基线始终确定。二者互补，不冲突。

## 后果

- 新增智能指标时，必须先确定"由谁判定"：只允许确定性判定器（validator / verify / 统计）。
- 任何引入 LLM-as-judge 的评估方案，需要本 ADR 的修订（Deprecated 级变更）。
