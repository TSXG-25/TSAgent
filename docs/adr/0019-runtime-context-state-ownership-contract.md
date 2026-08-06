# ADR-0019: Runtime Context and State Ownership Contract（v2.3A）

- 状态: Accepted — v2.3A Implemented and Verified
- 日期: 2026-08
- 关联: ADR-0013（Conversation Runtime）、ADR-0016（Run Checkpoint）、ADR-0018（Run-Level Workflow Resume）

## 一、决策摘要

v2.3A 先解决 Runtime 状态归属，再进入 Durable Store、Service Boundary 或
Cancellation。当前多个可变对象仍由模块级字典、单例或隐式 namespace 承载，适合单用户
CLI，但不满足并发 Session、并发 Run 和进程服务的隔离要求。

本 ADR 冻结三个作用域：

```text
ApplicationContext
├── provider registry
├── configuration
├── shared immutable resources
└── service factories

SessionContext
├── session_id
├── tenant_id / user_id
├── conversation state
└── session-scoped memory view

RunContext
├── run_id / request_id
├── workspace
├── artifact store
├── run-scoped event stream
├── checkpoint and RunResume store views
├── diagnostics
└── lifecycle / close state
```

这三个 Context 是 ownership boundary，不是一个承载所有依赖的 God Context。每个可变
对象必须有一个明确 owner；只读配置和共享不可变资源才允许属于 Application。

## 二、当前问题证据

以下现状是 v2.3A 的迁移对象，不是新的长期合同：

| 现状 | 当前位置 | 风险 |
| --- | --- | --- |
| 全局 Artifact 字典 | `agent/services/artifact_service.py:ArtifactService._store` | `SessionRuntime.reset()` 会清空其他 Run 的 Artifact |
| 全局 EventBus | `agent/event_bus.py:event_bus` | 无 unsubscribe/close，旧 Agent subscriber 永久残留 |
| Runtime 订阅 | `agent/runtime.py:UniversalAgent.__init__` | 每次创建 Agent 都增加 `task_end` subscriber |
| Workspace 全局指针 | `agent/services/workspace_service.py`、`agent/workspace/manager.py` | 当前 workspace 可能被另一个测试或 Run 覆盖 |
| Memory namespace | `MemoryService` 与 ConversationTracker 的模块级存储 | Session 归属依赖 user_id 约定，缺少显式 Session owner |
| Runtime reset | `agent/session_runtime.py:SessionRuntime.reset` | 通过清理全局对象实现生命周期，而非关闭 owned resources |

## 三、作用域合同

### ApplicationContext

ApplicationContext 由进程/服务实例持有，允许包含：

- Provider registry、静态配置和不可变 schema；
- 共享只读缓存或显式线程安全的 immutable resource；
- Session/Run factory。

ApplicationContext 不得持有某个用户、Session 或 Run 的可变 Artifact、Conversation、
Checkpoint 或 Event subscriber。

### SessionContext

SessionContext 是 Session 的唯一身份边界，至少包含：

```text
session_id
tenant_id / user_id
conversation_store/view
memory_store/view
```

Session reset 只能清理该 Session 的 conversation/runtime projection；不得清理其他
Session 或 Run 的状态。Session destroy 必须幂等，且必须关闭由 Session 创建的 Agent
和 subscription。

### RunContext

RunContext 是一次可恢复执行的资源边界，至少包含：

```text
run_id
session_id
request_id
workspace
artifacts
event_stream
checkpoint_store_view
run_resume_store_view
diagnostics
```

同一 `artifact key` 在不同 Run 中必须完全隔离。RunContext close 后：

- 不得继续发布 Run event；
- 不得继续写入 Run Artifact；
- 所有 subscription、temporary workspace handle 和其他 owned handle 必须释放；
- 不得删除 durable workspace、artifact 或 checkpoint；
- 旧 Run 的对象不得被新 Run 重用。

### 生命周期与身份合同

`RunContext` 对应一个逻辑 Run，而不是一次函数调用、一次消息或一次
`UniversalAgent.run()` 调用：

```text
start_run / resume_run
    → 创建当前进程内的 RunContext 实例
    → 绑定稳定的 run_id、workspace 和持久化 Store view
    → 处理同一逻辑 Run 的一个或多个请求
    → close 释放本进程资源
```

同一 Session 中的后续消息必须复用当前逻辑 Run，不能因为再次调用
`UniversalAgent.run()` 自动生成新的 `run_id`。Resume 可以在新进程中创建新的
`RunContext` 实例，但必须绑定原有 `run_id`，并通过兼容的 workspace 与 Store view
继续恢复；它不是一个新的逻辑 Run。

`close()` 与删除状态严格分离：

- `close()` 停止事件发布、关闭 subscription、flush diagnostics、释放文件句柄和
  临时资源，但保留可恢复所需的 durable workspace、artifact 与 checkpoint；
- `destroy()` / `purge()` 是显式的破坏性操作，只有在终态和 retention policy
  允许时才删除持久化状态；
- Session reset 只能重置 Session projection，不能删除仍在运行或可恢复的 Run。

所有外部身份都必须带有显式 scope：

```text
durable memory    = tenant_id + user_id
conversation      = tenant_id + session_id
run               = tenant_id + session_id + run_id
```

Memory 的底层 Store 可以由 Application 共享，但 Session 只能通过带身份过滤的
`memory_view` 访问，不能依赖仅由 `user_id` 组成的隐式全局 namespace。

## 四、服务边界

### Artifact

目标接口为实例作用域：

```python
run_context.artifacts.put(...)
run_context.artifacts.get(...)
```

禁止生产路径继续依赖 `ArtifactService._store` 这种模块级可变字典。Artifact 的
identity、reference 和 digest 必须绑定 `run_id`；跨 Run Artifact reference 默认拒绝。

### EventBus

目标订阅必须返回可关闭句柄：

```python
subscription = run_context.event_stream.subscribe(handler)
subscription.close()
```

必须支持：

- unsubscribe/close；
- subscriber count；
- Session/Run scope；
- close 后拒绝新事件；
- 重复 close 幂等；
- Agent 生命周期结束时自动释放订阅。

### Agent Factory

Agent 不再通过 reset 隐式替换并遗留旧 subscriber，而由 factory 显式创建：

```python
agent = agent_factory.create(
    session_context=session,
    run_context=run,
)
```

生命周期结束时必须执行：

```python
await agent.close()
await run_context.close()
```

CLI、Benchmark 和未来 Service 层都通过 factory 获取同一组 scoped dependencies。

## 五、强制不变量

1. 每个可变对象只有一个 Application、Session 或 Run owner；
2. Runtime、Executor、Conversation、Resume 生产路径不得直接依赖模块级可变
   Artifact/EventBus singleton；
3. Session A reset 不得改变 Session B 的 Conversation、Memory、Artifact、Event 或 Run；
4. 两个 Run 使用相同 artifact key 时，读写和 digest 完全隔离；
5. 同一个 Event 只能送达其声明 scope 内的 subscriber；
6. Agent close、Run close 和 subscription close 必须幂等；
7. 关闭后的 Run 不得继续发布事件、写 Artifact 或消费恢复动作；
8. Session、Run、Workspace、Memory、Checkpoint 的 identity 不匹配时必须显式拒绝，
   不得静默回退到 default/global namespace；
9. `reset()` 只能操作调用方拥有的 scope，不能通过清空全局容器实现隔离；
10. 生产路径中的 scope 依赖必须可由 Architecture Verification 静态检查。

## 六、实现顺序

```text
ADR / Dataset
    ↓
ApplicationContext / SessionContext / RunContext types
    ↓
instance-scoped ArtifactStore and EventStream
    ↓
AgentFactory + explicit close
    ↓
Runtime / Executor / Conversation / Resume dependency migration
    ↓
concurrency / reset / lifecycle integration tests
```

本阶段不实现 SQLite、REST、Cancellation、Provider Failover 或 Multi-Agent。Durable
Store 进入 v2.3B；AgentService 进入 v2.3C；Cancellation/Timeout 进入 v2.3D。

## 七、Dataset / Oracle

第一批确定性 Dataset 位于 `benchmarks/context_isolation/`，共 12 个 case，覆盖：

- 相同 Artifact key 的跨 Run 隔离；
- Session reset 不影响其他 Session；
- Event scope 与旧 Agent subscriber 清理；
- repeated reset/create/destroy 不增长 listener；
- Run close 释放 subscription、workspace handle 和事件入口；
- Run/Session/Artifact reference 错配拒绝；
- 旧 Agent 不得处理新 Session 事件。

Dataset Validator 只验证 case 唯一性、scope 声明、覆盖完整性和 metadata hash；它不把
Dataset PASS 误报为 Runtime 已隔离。生产能力必须由后续 deterministic integration
tests 证明。

## 八、验收门槛

```text
Dataset uniqueness / coverage                 100%
Two-session artifact isolation                100%
Two-run event isolation                       100%
Session reset non-interference                100%
Repeated lifecycle subscriber leak            0
Run close resource leak                       0
Closed Run event/artifact rejection            100%
Context identity mismatch rejection            100%
Global mutable singleton production imports    0
```

不需要真实 LLM。v2.3A 的核心证据是确定性并发、生命周期和静态边界测试。

当前静态边界已达到：Runtime/Executor/ContextBuilder/Grounder/Planner 不直接 import
全局 WorkspaceService、EventBus、ArtifactService 或 Conversation singleton。旧 facade
仍作为显式迁移边界保留，但不再是生产 scoped path 的默认入口；其清理版本和 usage
count 属于后续 A4，不影响 v2.3A 的生产路径门禁。

## 九、v2.3A 最终实现与验收证据

v2.3A 已完成以下实现：

- `agent/runtime_context.py` 提供 `ApplicationContext`、`SessionContext`、
  `RunContext` 及显式 close 级联；
- `ArtifactStore` 改为实例作用域，旧 `ArtifactService` 仅保留兼容 facade；
- `EventBus` 支持 scoped instance、`Subscription.close()`、subscriber count 和
  close 后拒绝发布；
- `Workspace` / `WorkspaceManager` 支持显式 EventBus 注入、close 和 Run-owned
  workspace service；Workspace 事件可绑定到 Run-owned bus，close 不删除 workspace
  根目录；
- `RunDiagnosticsSink` 绑定 `tenant_id/session_id/run_id`，Contract diagnostics 在
  scoped Run 存在时写入 Run-owned sink/event bus；
- `SessionRuntime` 不再通过清空全局 Artifact 实现 reset；`start_run` / `resume_run`
  显式创建或恢复逻辑 Run，连续消息复用同一个 RunContext，`close` 只释放进程内
  资源而不删除 durable recovery state；
- `RunContext` 已保存 `tenant_id`、`session_id`、`run_id` 和显式 memory namespace，
  为 durable memory、conversation 与 Run 的 identity 组合保留边界；
- 默认 CLI 项目根通过 Application/Session 的 `workspace_root` 显式传入，Run-owned
  WorkspaceService 采用 lazy index；普通聊天不会因创建 Run 而重复扫描整个仓库；
- Session 的 memory namespace 已显式化：非持久会话默认使用 `session_id`，持久会话
  必须使用 `tenant_id + user_id` 的组合；Planner/Runtime 使用 SessionContext 的
  Conversation view 和 `ScopedMemoryView`；
- `MemoryRuntime.reset` 支持显式传入 Session-owned ConversationTracker，避免
  Session A reset 触碰另一个 Session 的 tracker；
- ContextBuilder、Planner、Finalizer、Executor 和 Grounder 在已绑定 RunContext 时
  优先使用 scoped workspace/memory；只有无 RunContext 的旧直接调用才保留 legacy
  fallback；
- legacy Workspace/EventBus/Conversation fallback 已集中到 `agent/compat/`，并发出
  `DeprecationWarning`；生产模块不再直接 import 全局 WorkspaceService/EventBus，
  Conversation fallback 也已显式化；
- Context Isolation Dataset 已通过 schema 校验（12 cases，hash
  `a689dbefd7bfd6ed7306c322e264d4e541ca298ed814bb86f055f864d170a91f`），相关确定性
  测试与 mypy 已通过。

### 最终验证

- 全量离线测试：`289 passed, 17 skipped`；跳过项均为 DeepSeek 真实 API 不可达，
  不属于离线能力失败；
- Context Isolation 相关测试：`24 passed`；覆盖强制交错的 Artifact/Event 隔离、
  Session reset 非干扰、close/publish 竞态、close 后同一 `run_id` 恢复及 durable
  workspace/checkpoint view 保留；
- Architecture Verification：`PASS`；生产模块的全局可变 singleton import gate 通过；
- Context Isolation Dataset：`PASS`，12 cases，dataset hash
  `a689dbefd7bfd6ed7306c322e264d4e541ca298ed814bb86f055f864d170a91f`；
- mypy：`Success`，24 个相关 source files；
- `git diff --check`：`PASS`。

### v2.3A 之后的明确边界

- Workspace、Diagnostics、Memory/Conversation 的底层 legacy store/facade 可以继续
  为旧直接调用提供兼容，但必须通过 `agent/compat/` 显式访问，不能成为新的默认入口；
- `close()` 释放进程内资源但不删除可恢复状态，`destroy()` / `purge()` 才是显式删除操作；
- SQLite 事务、Checkpoint/RunResumeIndex/Artifact metadata 的统一持久化、revision/
  fencing 和崩溃恢复属于 v2.3B；
- AgentService、REST、Cancellation、Timeout 和 Provider Failover 不属于 v2.3A。
