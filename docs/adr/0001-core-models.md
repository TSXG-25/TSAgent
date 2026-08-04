# ADR-0001: Core Models & Architecture Freeze

- 状态: Accepted
- 日期: 2026-08
- 决策者: TSAgent 架构

---

## 背景

TSAgent 经过多轮重构（orchestrator 拆包、executor 收敛、services 精简、Compile 管线化），
已具备定义长期架构纪律的条件。为避免未来出现第三套 Task、第二套 ExecutionPlan、
另一种 Stage 等模型分裂，本 ADR 冻结核心模型与扩展边界。

## 决策

### 四个核心模型（系统内唯一）

```
Natural Language
        │
        ▼
Intent            # 认知层唯一模型
        │
        ▼
Task              # 规划层唯一模型（AST）
        │
        ▼
ExecutionPlan     # 执行层唯一模型（IR）
        │
        ▼
ExecutionResult   # 运行层唯一模型
```

### 五个组件边界（任何组件不得跨两层）

| Component    | Input          | Output          | 唯一职责     |
| ------------ | -------------- | --------------- | -------- |
| IntentEngine | Text           | Intent          | 理解用户意图   |
| Planner/Workflow | Intent     | Task            | 规划任务     |
| Compiler     | Task           | ExecutionPlan   | 编译任务     |
| Executor     | ExecutionPlan  | ExecutionResult | 执行计划     |
| Finalizer    | ExecutionResult| Answer          | 生成最终回答   |

禁止：
- Compiler 重新规划 Task
- Executor 修改 ExecutionPlan
- Planner 调用 Tool

### Principle 1–10

1. **四个核心模型**：Intent → Task → ExecutionPlan → ExecutionResult。
2. **单一模型原则**：每个阶段只有一种核心数据模型，禁止平行模型。
3. **编译优先**：尽可能把错误前移到编译阶段（Contract / Semantic Check / Static Check），而非运行时。
4. **Compiler 四阶段**：Normalize → Semantic Check → Lower → Static Check，职责不可混用。
   Normalize 不猜 / Semantic 不修 / Lower 不推理 / Static 不执行。
5. **Executor 无推理**：只执行 ExecutionPlan，不修正、不猜测、不重新规划。
6. **Immutable Core Model（核心模型不可变）**：Task / ExecutionPlan 一经产出不可原地修改。
   修改语义通过 `model_copy(update=...)` 产生新对象。为 Cache / Replay / Trace / Debug / Benchmark 铺路。
7. **Compiler Pure Function**：`compile(task, context) -> plan` 无任何副作用。
   禁止写 Memory / Workspace / Artifact / Event / Print / Log Decision。
8. **ExecutionPlan 是唯一 IR**：Executor 只允许消费 ExecutionPlan。
   禁止 `Executor.execute(task)` / `Executor.execute(stage)` / `Executor.execute(dict)`。
9. **Workflow 只是 Task Factory**：Workflow 只定义 Task 模板序列，不知道任何 Tool / Executor / ToolPolicy。
   具体编译交给 Compiler。
10. **Rule 是唯一扩展点**：新增能力 = 新增 Rule（Compiler Lowering），不是新增 Executor / Dispatcher / Compiler。

---

## Architecture Freeze v1.0

自本 ADR 生效后，新增功能默认只能通过以下四种方式扩展：

1. 新增 Rule（Compiler Lowering）
2. 新增 Tool
3. 新增 Workflow（Task Factory）
4. 新增 Prompt

**禁止新增新的核心模型、执行路径、Executor 类型或 Planner 类型。**

## 迁移纪律

1. 先冻结 ADR，再写代码。
2. 每次变更只迁移一个模型或一个组件。
3. 迁移完成立即删除兼容层（旧 dict-to-task 转换、双模型转换、旧接口重导出），不留技术债。
4. Benchmark 是合并门禁：任何架构变更必须通过单元测试 + benchmark 回归。
