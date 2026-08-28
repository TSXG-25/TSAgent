# TSAgent
面向复杂工程任务的长期运行 Agent。

## Status Dashboard

```
Architecture
──────────────
v1 Frozen (tag: v1.0-arch-freeze)

Evaluation
──────────────
Datasets      14 (8 core + 6 navigation)
Metrics       v1
Regression    evaluation/regression/compare.py
Quality Gate  PASS / WARNING / FAIL

Current Phase
──────────────
v2.0 RC1 — Release Candidate（Stability → Reproducibility → Release Gate）
  RC Scope               暂停 Capability 扩展，完成 Context Boundary / Dependency Lock / CI / Demo
  Offline Regression     149 passed（当前本地基线）
  Tool Regression        17 passed / 3 deselected（网络工具离线排除）
  Contract Verification  PASS
  Reflection / Decision  PASS（10/10 benchmarks）
  Architecture Verify   PASS
  Trend Gate             PASS

v2.0-D — Agent Intelligence: Decision（Policy → Confidence Gate → Next Action，已完成）
  Decision              10/10 = 100%（Recovery Rate 1.000 / Wrong Recovery 0.000）
  Decision Policy       retry/switch/ask/finish 四动作，PolicyRegistry（v2.1 可动态更新）
  Decision Confidence   组合置信（诊断置信 + 重试耗尽 + 动作风险 + 证据完整度）< 0.5 → ASK
  DecisionTrace         决策可解释性（rule/confidence/rejected）→ Wrong Recovery 分析
  接入 Runtime          失败 → Reflection → Decision → next action（策略性停止不无限重试）
  Trend Gate            PASS（planning + reflection + decision 三基线）

v2.0-C — Agent Intelligence: Reflection（Diagnosis → Correction Proposal，已完成）
  Reflection              10/10 = 100%（Diagnosis Accuracy / Correction Proposal / False Dx 0.000）
  Reflection Contract     reflect(event: FailureEvent) 唯一入口（只消费 Fail Board Evidence）
  Correction              Proposal（不执行，Executor 决定采纳）→ 已接入统一失败路径
  Determinism Gate        10 场景 × 100 次 Diagnosis 完全一致
  FixCommit               FIXED(commit) 生命周期闭环（REGRESSION 可关联首次修复）
  Long Horizon            drift 0.333 → 0.0（Reflection 改善目标保持）

v2.0-A — Agent Intelligence: Planning Quality（已完成）
  Planning (real planner)    6/6 = 100%（Goal/Constraint/Task/Dependency/Order/Abstention）
  Planning Dataset           8 场景（含 no_web / scope_only / no_delete / 信息不足 Abstain）
  Structural Validator       可跨领域复用（SQL/Browser/Coding Planner 共享）
  Trend Gate                 Capability Progress Curve（不能下降）
  Fail Board v2              Diagnostic Backbone（Event Sourcing + Evidence + 统一 Root Cause 映射）
  Contract Verification      PASS（v1 三层冻结持续有效）

v1.2C — Capability Expansion（Resolver Contract 横向扩展，已完成）
  Context Resolution       42/42 = 100%（+ ME001 跨会话 Memory）
  Repository Resolution    10/10 = 100%
  Capability Hint          7/7 = 100%
  Capability Reuse Score   4/4 = 100%（Conversation/Repository/Memory/Capability 零新增抽象）
  Contract Verification    PASS（fields + methods + signature + schema 四部分冻结）
  Resolver Determinism     8/8 × 100 runs PASS

Architecture Changes
──────────────
❌ Frozen
New Runtime Layers: only if Architecture Gate triggered (ADR-0005)
```

## 核心模型（v2.0 RC，ADR-0001..0011）

```
Intent → Task → ExecutionPlan → ExecutorFactory → ExecutionResult
                                      │
                                      └→ FailureEvent → Reflection → Decision
```

- 四个模型系统唯一，禁止平行模型。
- Compiler 四阶段：Normalize → Semantic Check → Lower → Static Check（Pure Function）。
- Grounder 只缩搜索空间（Search Space Reduction），不替 Planner 决策。
- Executor 只消费 ExecutionPlan，不知道用户问题。
- Runtime 使用 PlannerContext / ExecutorContext / ReflectionContext 做阶段边界。
- Runtime 是唯一协调者。

## 目录

```
agent/         # Intent / Grounding / Planner / Compiler / Executor / Runtime
evaluation/    # Dataset / Benchmark / Regression / Metrics / History / Factory
docs/adr/      # ADR-0001..0011（架构决策记录 + v2 开发纪律）
benchmarks/    # 既有 benchmark 执行器（runner / report）
.github/       # RC 自动门禁
requirements-lock.txt  # RC 依赖锁
```

RC 架构总览见 [docs/architecture.md](docs/architecture.md)。

## 工程循环（ADR-0005）

```
Dataset → Benchmark → Metrics → Regression → Quality Gate → Merge
```

## 质量指标（Metrics v1）

- PlanningSuccess / GroundingRecall / GroundingTop1
- CompileRejectRate / ExecutionSuccess / VerificationSuccess
- Latency / Cost

Quality Budget：Planning 与 Grounding 不得下降、Compile Reject 不得上升、
Latency ≤+10%、Cost ≤+5%。否则 PR 不合并（ADR-0005）。

## Capability Lifecycle（ADR-0006）

新增能力必须先写 Dataset（`evaluation/datasets/<capability>/`）→ Benchmark → 再实现。
新增前回答两问：属于哪个模型？提升哪个指标？回答不了则不进入系统。

---

## TSAgent v1 Definition of Done

- [x] Architecture Frozen (v1)
- [x] Core Models Frozen（Intent → Task → ExecutionPlan → ExecutionResult）
- [x] Compiler Stable（四阶段 + Pure Function）
- [x] Grounding Stable（Recall 1.00 / Search Space Reduction）
- [x] Evaluation Framework（Dataset → Benchmark → Metrics → Regression → Gate）
- [x] Benchmark Driven（Stabilization 阶段，数据驱动）
- [x] Quality Budget（PASS/WARNING/FAIL 三级）
- [x] Regression Gate（compare.py + history 快照）
- [x] Architecture Gate（量化门槛，ADR-0005）

> v1 结束标志：以上全部落盘。此后进入长期维护，新能力走 Capability Lifecycle。
