# ADR-0003: Model Stability

- 状态: Accepted
- 日期: 2026-08
- 关联: ADR-0001, ADR-0002

---

## 背景

四模型（Intent → Task → ExecutionPlan → ExecutionResult）与 Compiler 四阶段
已冻结（ADR-0001/0002）。本 ADR 冻结模型**稳定性**规则，防止 Task 变成 God Object、
防止 Compiler 长回半执行器、防止新的核心模型悄然出现。

## 决策

### 1. Task 结构冻结（防 God Object）

Task 字段分组（总数 ≤ 12）：

```
Identity:   id
Meaning:    verb / goal / target / target_type
Relation:   dependencies
Execution:  policy
```

- `kind` 已 deprecated（被 `target_type` 取代）。
- 任何新需求进入 `policy`：`policy.parallel` / `policy.timeout` / `policy.permission` ...
- **禁止给 Task 直接加字段**。

### 2. Compiler 冻结（像 Clang，不像解释器）

- 只做四阶段：Normalize → Semantic Check → Lower → Static Check。
- 非法输入 → `CompileError`，结束。
- 禁止 Compiler 自动搜索仓库 / 自动改 Prompt / 自动补 target。

### 3. 四模型归属纪律

任何新功能必须回答：**它属于四模型中的哪一个？**

| 能力         | 归属           |
| ---------- | ------------ |
| Cache      | ExecutionPlan |
| Cost       | ExecutionResult |
| Permission | Task.policy   |
| History    | Intent       |

回答不了 → 重新思考设计，不加字段。

### 4. 编译器 Terminology（统一术语，防模型膨胀）

| 当前           | 编译器对应    |
| ------------ | -------- |
| Task         | AST      |
| Compiler     | Compiler（四阶段） |
| ExecutionPlan | IR（唯一） |
| Executor     | Backend  |

> ExecutionPlan 是 IR——不是"又一种 Plan"。

### 5. 路线图（D5 后冻结）

```
D0 Benchmark  → 可测
D1 Execution  → 可跑（执行层修复）
D2 Contract   → 正确性（编译期拦截）
D3 Grounding  → 上下文质量（搜索空间缩减）
D4 Runtime Loop → 运行时控制（Observe→Ground→Plan→Compile→Execute→Repeat）
D5 Optimization → 性能与工程能力（Cache/Parallel/Cost/Replay）
```

**D5 之后不再新增架构层，只允许新增能力（Runtime Extension）：**
Checkpoint / Permission / Human Approval / Long Memory / Cache / Parallel / Cost / Replay / Trace。

生命周期定位：
- D0–D2: Correctness
- D3: Grounding
- D4: Runtime Control
- D5: Optimization

### 6. 数据驱动纪律

> **未来只有 Benchmark 能推动架构变化。**

流程固定为：Benchmark → 发现失败 → 定位属于哪一层 → 修改该层 → Benchmark 提升。
禁止"想到新架构就重构、希望 Benchmark 提升"。
