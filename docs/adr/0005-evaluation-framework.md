# ADR-0005: Evaluation Framework

- 状态: Accepted
- 日期: 2026-08
- 关联: ADR-0001..0004

---

## 背景

v1 架构已冻结（四模型 / Compiler 四阶段 / 层边界）。本 ADR 将
Benchmark 提升为 Evaluation Framework——系统的工程质量基础设施，
承载 Benchmark / Regression / Architecture Gate / Release Gate。

## 决策

### 固定工程循环（每个 PR 的生命周期）

```
Dataset → Benchmark → Metrics → Regression → Quality Gate → Merge
```

任何变更（代码 / Rule / Prompt / 能力）都必须走完此循环。

### Evaluation 目录

```
evaluation/
    metrics_v1.py        # 统一 Metrics 模型（版本化）
    benchmark/           # runner
    datasets/            # 唯一事实来源（Single Source of Truth）
    regression/          # main vs PR 对比
    history/             # 历史快照（成长曲线）
    factory/             # 参数化任务生成
```

### Metrics（v1）

- PlanningSuccess
- GroundingRecall
- GroundingTop1
- CompileRejectRate
- ExecutionSuccess
- VerificationSuccess
- Latency
- Cost

Metrics 版本化：以后新增指标（PermissionSuccess 等）→ `metrics_v2.py`，
不修改 v1，保证历史可比。

### Quality Budget（PR 合并门槛）

| 指标            | 约束     |
| ------------- | ------ |
| Planning Success | 不得下降 |
| Grounding Recall | 不得下降 |
| Compile Reject  | 不得上升 |
| Latency        | ≤ +10% |
| Cost           | ≤ +5%  |

### Quality Gate 三级

- **PASS**：全部 Budget 满足 → 可合并。
- **WARNING**：允许合并（如 Latency +7% 但 Planning +2%）。
- **FAIL**：禁止合并（如 Planning -3% 或 Compile Reject +5%）。

### Architecture Gate（量化）

> 新的 Runtime Layer 只有在满足以下条件时才允许：
> Benchmark 连续三轮出现同一种失败类型、占比 >20%、且无法通过已有层
> （Rule / Grounding / Planner prompt）解决。

## 影响

- 未来发布 v1.1 / v1.2 / v2 以 Quality Gate + Regression PASS 为准，而非"pytest 通过"。
