# ADR-0015: Session Isolation Contract

- 状态: Accepted
- 日期: 2026-08
- 关联: ADR-0005（评估框架）、ADR-0013（Conversation Runtime）、ADR-0014（Benchmark Correctness）

## 背景

Memory Benchmark 过去只更换 `user_id`，但没有统一的生命周期边界。重复运行同一
case 时，以下状态可能残留：

- session 消息栈与 short-term JSON；
- Chroma 语义摘要与 resolution memory；
- SQLite 用户 Facts；
- Conversation Runtime 快照、事件和 pending 信号；
- 进程内 Runtime ArtifactStore。

这会让“我住在哪里”同时受到旧 case 的北京、上海等事实影响，导致 Recall Rate
不再是可比较的指标。

## 决策

### 1. SessionRuntime 是生命周期入口

```python
session = SessionRuntime.create(
    session_id="case-001",
    user_id="case-001",
)
try:
    answer = await session.run("...")
finally:
    session.destroy()
```

`SessionRuntime.create()` 默认创建隔离会话；`destroy()` 幂等并清理该 namespace。
应用需要持久化时必须显式使用 `persistent=True`，或在同一个实例内持续运行。

### 2. 清理按层控制

```python
session.reset(
    conversation=True,  # session/short-term/summary/resolution/ConversationRuntime
    runtime=True,       # Agent/orchestrator/ArtifactStore
    facts=False,        # SQLite facts 默认保留
)
```

`MemoryRuntime.reset(user_id, ...)` 是 Memory 层唯一的 scoped cleanup API；它只接受
一个 path-safe namespace，不提供删除全局数据的接口。

### 3. Benchmark 默认一 case 一 session

Memory Benchmark 的默认模式是：

```text
create isolated session
    ↓
run all turns of one case
    ↓
destroy / purge namespace
```

跨 case 的长对话、持久化、冲突覆盖和记忆衰减必须使用明确声明的
`persistent` 数据集/运行模式，不得混入 stateless Recall Benchmark。

## 后果

- Benchmark 重跑不会依赖上一次运行留下的同名 user namespace。
- Facts 是否清理显式可见，避免把用户级持久化误当作会话记忆。
- 未来 API/SDK 可以复用同一生命周期抽象；多租户并发仍需进一步把全局
  Workspace/Artifact 缓存改为 session-scoped，不能仅依赖本 ADR 假设线程隔离。
