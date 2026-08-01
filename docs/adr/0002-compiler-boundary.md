# ADR-0002: Compiler Boundary

- 状态: Accepted
- 日期: 2026-08
- 关联: ADR-0001

---

## 背景

Compiler 是整个执行链的"编译期"关卡。为避免职责逐渐回流
（重新规划、猜测 target、写副作用），本 ADR 冻结 Compiler 的边界。

## 决策

### Compiler 四阶段（职责不可混用）

```
Task
 │
 ▼
Normalize       # 只做文本规范化：去引号/空格/slash/大小写。不猜。
 │
 ▼
Semantic Check  # 校验 target_type/target 合法性。不修。非法 → 拒绝（CompileError）。
 │
 ▼
Lower           # 按 target_type 分派 Rule 生成 ExecutionPlan。不推理。
 │
 ▼
Static Check    # ExecutionPlan 契约检查：tool 存在 / $var 已定义 / outputs 非空 / SSA 无重复输出。
 │
 ▼
ExecutionPlan
```

### Compiler 唯一输入

```
compile(task: Task, context: CompilerContext) -> ExecutionPlan
```

- `CompilerContext` 持有 workspace / registry / repository 等环境引用（全部可选）。
- 禁止 `compile(task, workspace=..., memory=..., repository=..., knowledge=...)` 参数膨胀。

### Compiler 纯函数

- 同一 `(task, context)` 输入 → 同一输出。
- 无副作用：禁止写 Memory / Workspace / Artifact / Event / Print / Log Decision。

### 零容忍

- Compiler 不修正、不猜测、不补全。
- 非法 Task → `CompileError`（编译期拒绝）。
- 静态检查失败 → `CompileError`（运行时不该再发现工具缺失/变量未定义）。

## 影响

- ToolSelector 退化为 Lowering 层（rules = lowering rules）。
- 所有非法输出在编译期被拦截，benchmark 失败点清晰可量化，不被兜底机制掩盖。
