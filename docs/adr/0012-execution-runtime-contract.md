# ADR-0012: Execution Runtime Contract

- 状态: Accepted
- 日期: 2026-08
- 关联: ADR-0001（核心模型）、ADR-0009（确定性验证）、ADR-0010（行为验收）、ADR-0011（评估先行）

---

## 背景

v2.1A Execution Runtime 收敛时发现：写入类任务的成败曾由 Tool 的返回字符串决定，
导致"Tool 说成功但文件不存在"的假成功（Hallucinated Success）。这是系统性设计缺口，
不是单个 bug。

## 决策

### 执行流水线固定为四阶段

```
ExecutionPlan
        ↓
Executor        负责尝试执行（调用 Tool）
        ↓
ExecutionArtifacts   收集世界状态痕迹（files_written / commands / stdout / stderr）
        ↓
ExecutionVerifier   确认世界是否真的变成期望状态
        ↓
ExecutionResult    success 只能由 Verifier 产生
```

### 三条 Contract（违反任意一条 → PR 不合并）

```
C1  Executor 负责尝试执行，Verifier 负责确认结果。
C2  ExecutionResult.success 只能由 ExecutionVerifier 产生。
    禁止 Tool → 直接 success=True。
C3  Finalizer 不决定成功，只做 ExecutionResult → Natural Language。
    最终答案中"已写入/已生成/完成"等声明必须以 Verifier PASS 为前提。
```

### 验证器注册表

`ExecutionVerifier` 按 `task.verb` 分派到具体验证器：

```
write    → WriteVerification   （目标文件存在且非空；可选内容匹配）
delete   → DeleteVerification  （目标已不存在；预留）
copy/move/patch/python/shell/docker → 后续按相同模式注册
```

验证器是纯函数，以文件系统/世界状态为准，零依赖 Service（与 Resolver 同级纪律）。

### 实现位置

- `agent/executor/verifier.py` — ExecutionVerifier / ExecutionArtifacts / VerificationResult / 验证器注册表
- `agent/executor/executors/tool.py` — Pipeline 末端调用 ExecutionVerifier 生成 ExecutionResult
- `agent/executor/plan_executor.py` — 只收集 `files_written` 痕迹，不判定成败

## 后果

- 新增"写"类能力（Copy/Move/Patch/Delete/Python）只需注册对应 Verifier，无需改 Executor。
- 假成功类 bug 由 Verifier 拦截，Finalizer 只负责如实汇报。
- CI 中 Architecture Verification 需保证：Executor 不产生 success、Verifier 不调用 Tool。
