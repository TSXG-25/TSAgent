# TSAgent 当前架构（实现审计）

2026-09-08 后续修复与审计增补：[ADR-0034](adr/0034-goal-pipeline-human-regressions.md) 和[目标契约执行计划](exec-plans/goal-contract-audit.md)记录本轮人工测试引出的当前差异与未关闭风险。下文“本次审计只修改文档”指此前的架构文档审计，不代表后续没有代码变更。

本文件描述当前仓库能由生产代码和离线测试证明的架构，而不是目标架构。审计基准为 2026-09-08；本次审计只修改文档，没有修改 Agent Runtime、工具、工作流或测试实现。

## 证据口径

- VERIFIED：当前生产调用链或针对性测试直接证明。
- INFERRED：由代码关系推导，但没有找到完整生产入口或针对性测试。
- DOCUMENTED_ONLY：ADR、README 或旧架构文档有描述，但当前生产调用链未被证明。
- LEGACY_OR_UNUSED：实现或测试仍存在，但当前生产入口没有调用。

证据优先级为：已接受 ADR，其次是可执行测试/验收，再次是当前生产调用链，然后是架构文档、实现说明、README、注释和历史/demo。实现、可达、生产集成、权威契约是四个不同判断，不能互相替代。

## 一、当前主路径

CLI 和 local sidecar 的主路径如下：

    CLI 或 local sidecar
      -> AgentService
      -> ServiceContextFactory
      -> SessionContext / RunContext
      -> DurableRuntimeStoreView（SQLite）
      -> RuntimeExecutionLauncher
      -> UniversalAgent.run
      -> ExecutionOrchestrator
      -> PlannerStage
      -> ExecutionStage
      -> Finalizer
      -> SQLite run head / events / output / artifacts

主要证据文件：

- agent/service/service.py：公开的 start_run、get_run、resume_run、cancel_run。
- agent/service/runtime_launcher.py：把 Runtime 生命周期和进度映射到 durable store。
- agent/runtime_context.py：ApplicationContext、SessionContext、RunContext 及其所有权边界。
- agent/runtime.py：单一 Runtime 状态机。
- agent/orchestrator/planner.py、executor.py、finalizer.py：三个阶段实现。
- agent/runtime_store/sqlite.py、view.py：SQLite 事实、revision、fence、事件和输出。

这条路径中，AgentService 是 CLI、sidecar 和未来外部服务使用的公共服务边界。它不直接执行 Planner 或 Tool；它负责身份、生命周期、取消、持久化和投影。

## 二、入口与上下文

### CLI 与 sidecar

main.py 创建默认 AgentService，读取用户输入，调用 start_run，再通过 stream_events 和 get_run 观察结果。agent/service/local_sidecar/dispatcher.py 只暴露 allowlist 内的 health、start_run、get_run、resume_run、cancel_run、list_artifacts、read_events、shutdown，并转发到 AgentService。

### 直接 API

agent/api.py 的 TSAgent 是 AgentService 的薄门面：构造时打开 SQLite durable runtime store，run() 走 start_run → stream_events → get_run 并返回 snapshot.output.text。原 agent/session_runtime.py 的 SessionRuntime 已删除；所有公开执行入口统一经过 AgentService，因此每个公开 run 都带 durable 会话历史。

### 上下文层次

ApplicationContext 持有应用配置、workspace、SQLite store 和 session 集合。SessionContext 持有 tenant/user/session 身份、ConversationTracker、ScopedMemoryView 和逻辑 run。RunContext 持有单次运行的 writer/fence、DurableRuntimeStoreView、CancellationView、scoped WorkspaceService、ArtifactStore 和 EventBus。

AgentState 是 Runtime cache，不是 durable source of truth。Run head、revision、事件、output、artifact、effect/finalization 事实在 store 或事件流中；ExecutionContext 则是一次 Task/Workflow 执行时的可变容器，包含 artifacts、facts、history、variables 和 budget。

## 三、Runtime 状态机

UniversalAgent 的状态枚举包含 INIT、PLAN、EXECUTE、NEXT_ACTION、NEXT_TASK、OBSERVE、RECOVER、REPLAN、FINISH、FAIL。当前有效循环是：

    INIT -> PLAN -> EXECUTE
                     |-> NEXT_ACTION -> EXECUTE
                     |-> OBSERVE -> NEXT_ACTION、REPLAN 或 FAIL
                     |-> REPLAN -> EXECUTE 或 FAIL
                     |-> RECOVER -> OBSERVE、EXECUTE 或终态
                     -> NEXT_TASK -> FINISH

每个边界都检查预算、取消和 Runtime 上下文。PLAN 调用 PlannerStage，EXECUTE 调用 ExecutionStage，REPLAN 在同一循环内委托 PlannerStage.replan，NEXT_TASK 调用 Finalizer。只有已知无效果的只读失败可重规划；写入/执行失败及效果未知不自动重放。结构性故障由 FailurePolicy 与既有任务恢复路径处理。

另外有两个短路路径：

1. 请求要求上一次输出时，Runtime 从 durable RunOutput 读取最近终态运行，不调用 Planner、Tool 或 LLM。
2. 输入无效时，Runtime 直接产生 blocked evidence。

## 四、Planner 与路由

PlannerStage 先由 ContextBuilder 汇总请求、ScopedMemoryView、ConversationTracker、历史 run 结果、artifacts、repository index 和 workspace；ReferenceResolver、IntentEngine、freshness/source policy 再确定请求语义和允许的执行边界。

当前已存在的路由包括：

- SIMPLE_CHAT 或 no-execution：直接调用 LLM 并记录 exchange，不创建工具任务。
- fresh research：Planner 按需加载 web 工具模块，并检查 ToolRegistry 的实时 availability/reason；缺少 `duckduckgo-mcp-server` 时在规划前显式 blocked，不把“已注册”误作“可启动”。web-search / web-fetch 通过 MCP Python SDK 启动本地 DuckDuckGo MCP stdio server，调用 `search` / `fetch_content`；不需要 API key，但实际查询仍需要网络。DuckDuckGo MCP 不提供精确时间筛选，时间范围只作为查询提示并在工具结果中明确披露。
- 文件、转换、追加、合并、代码或命令类任务：构造 Task 并编译 ExecutionPlan。
- 特定开发/题目请求：可进入专用 Workflow 路由。
- 其他请求：使用 plan_with_metadata，多次尝试后仍无可执行计划则澄清或 blocked。

专用 Workflow 路由在 planner.py 中直接创建 ExecutionContext 并调用 WorkflowExecutor.execute。该调用没有传入 checkpoint_request，因此它使用 WorkflowExecutor 的默认非 checkpoint 路径。Workflow 的 checkpoint 能力在独立协调器和测试路径存在，但不能从这条 Planner 路径推断为已接入主 Service resume。

## 五、Execution 与效果事实

ExecutionStage 先处理 EffectAuthorization 和 execution ownership，再决定使用编译任务还是动态 NextActionSelector。静态任务沿着以下单一路径执行：

    Task -> Compiler -> ExecutionPlan -> ExecutorFactory.get
         -> ToolExecutor 或 LLMExecutor
         -> PlanExecutor / tool registry
         -> ExecutionVerifier
         -> ActionResult / effect evidence

动态 owner 使用 NextActionSelector 选择 ask、answer 或工具动作；工具动作之后仍通过统一 ExecutorFactory 和 verifier。工具执行上下文带有 scoped workspace、ArtifactStore、EventBus、CancellationView、run results 和 conversation projection。

ToolExecutor 只有在 plan executor 成功并经过 ExecutionVerifier 后才把 ActionResult 标为 verified。文件写入还会验证 workspace 内的实际存在和内容；普通 LLM 输出不自动成为 verified effect。最终 output 是否可报告，由 Runtime evidence、output/freshness policy 和 Finalizer 一起决定。

## 六、Failure、Reflection 与 Decision

当前失败分流是：

    普通 Action failure
      -> ExecutionStage / observe_failure
      -> 有界 retry / REPLAN / FAILED terminal（依据错误码与效果事实）

    Structural failure
      -> FailurePolicy.resolve
      -> reflect(FailureEvent)
      -> decide(DecisionInput)
      -> RecoveryDirective
      -> Runtime/Orchestrator 执行 retry、switch、ask 或终态

Reflection 和 Decision 不是未使用代码：它们通过 agent/failure/policy.py 的 structural failure 分支从生产 Runtime 可达。普通 Action failure 不进入 Reflection/Decision；仍由同一个 Runtime 状态机选择后续动作。只读失败或携带明确“无效果”状态的白名单失败可进入有界 REPLAN；效果未知的写入/执行既不原样重试，也不重规划。Runtime exception 走 _recover，产生通用失败 evidence 并结束当前运行；它不是重新规划。

Structural recovery 只处理已有任务：retry 会把失败任务重新置为 pending，switch 会选择已有 pending 任务，ask/其他动作进入终态。Recovery budget 和 run/goal budget 共同限制循环。

## 七、Durable run、取消与终态

AgentService.start_run 先在 SQLite 中 reserve service start，再启动 Runtime。RuntimeExecutionLauncher 将 RUNNING、stage/task progress、取消观察、terminal event、run_output 和已验证 artifact facts 原子地写入 store。RunProjector 从 durable snapshot 和事件读取公共视图。

AgentService.cancel_run 只记录用户取消意图，不声称执行已停止。CancellationCoordinator、CancellationView、safe boundary 和 launcher 的 interruption convergence 共同把请求收敛为 CANCELLED、TIMED_OUT 或其他明确终态。Provider/LLM 等可中断等待使用 await_interruptibly；副作用步骤在安全边界观察取消。

## 八、Resume 的实际分叉

### Service resume：VERIFIED，但不是 checkpoint resume

AgentService.resume_run 会读取 run head、检查终态、准备 takeover writer/fence，然后由 RuntimeExecutionLauncher.resume 再次构造 UniversalAgent 并调用 runtime.run(request.request_text)。当前 Service DTO 的 checkpoint_id/action 没有被 launcher 用来恢复某个 Workflow stage；request_text 默认可以为空。

这条路径证明的是 durable run 的重新接管/重新运行，不是从 RunCheckpoint 继续执行。

### Workflow checkpoint/resume：实现存在，主路径未证明接入

WorkflowExecutor 在收到 checkpoint_request 时会创建或恢复 RunCheckpoint，在 Stage 边界记录 checkpoint，并用 validate_resume 检查 workflow/version、artifacts、effects、external guards 和 ResumeAction。agent/run_resume/coordinator.py 能加载 index、hydrate artifacts、创建 WorkflowCheckpointRequest、调用 WorkflowExecutor 并提交 checkpoint/index。

当前静态生产调用搜索没有找到 AgentService、Runtime、CLI 或 sidecar 调用 RunResumeCoordinator、resume_active、execute_or_resume 或构造 WorkflowCheckpointRequest 的路径。tests/test_run_resume_runtime.py 以及 realtest_reports 的 harness 会直接调用该协调器；这证明组件和测试场景，不证明公共 Service resume 已接线。

因此：

| 能力 | 当前结论 |
| --- | --- |
| SQLite run head、revision、fence、事件、output、artifact | VERIFIED |
| AgentService 的 takeover/re-launch resume | VERIFIED |
| RunCheckpoint / ResumeValidator / WorkflowExecutor checkpoint 分支 | VERIFIED（组件/测试） |
| RunResumeCoordinator 接入 AgentService.resume_run | DOCUMENTED_ONLY / 未找到生产调用 |
| 主路径按 checkpoint stage 精确恢复 | 未证明 |

README 和 ADR-0018 对 resume via checkpoint lineage 的描述比当前 Service 调用链更强，应视为待校正的文档债务。

## 九、Memory 与 Conversation

MemoryService 的写入闭环是：

    请求/结果 evidence
      -> MemoryLearningProvider 提议
      -> deterministic MemoryPolicy 授权
      -> MemoryPersistenceBoundary.commit
      -> facts / summaries / resolution

Provider 只提出候选，不直接写库。成功报告应依赖 MemoryCommitEvidence，而不是仅依赖 provider proposal。Runtime 可能在回答前学习显式信息，在执行前检索上下文，回答后再对显式 personal facts 进行学习；取消和超时不会继续做 post-answer learning。

模型可见的会话历史只有一个来源：SQLite durable run log。agent/context/conversation.py 的 derive_conversation 是唯一的纯投影，把 DurableRuntimeStoreView.conversation_results() 的终态 run 投成 user/assistant 消息对（含 [产物]/[观察]/[状态] 行，16000 字符预算）。原进程内 session buffer（agent/memory/session.py）与 short-term JSON（agent/memory/short_term.py）已删除；Runtime/Finalizer 不再调用 record_full_exchange，终态 run 本身就是记录。

agent/conversation/state.py 的冻结 ConversationState/ConversationTracker 是当前 SessionContext 使用的会话权威，保存 recent_goal、last_instruction、last_answer、turn_count 和 runtime_pending。agent/cognition/cognitive_context.py 也有名为 ConversationState 的对象，但它是 Intent/Resolution timeline cache，不是同一个语义模型。

ServiceContextFactory 给同一 tenant/user 的多个 session 使用 memory namespace tenant:user、scope user。resolution JSON 的部分路径以 namespace 而不是 session 为键；因此 durable/user memory 与 session-owned ConversationTracker 可能不一致。这是当前已确认的 scope debt，不在本次审计中修复。

## 十、Desktop 边界

apps/desktop 的 clientFactory 默认 mode=mock；local mode 必须提供 tenant/user 和 TauriSidecarBridge。local client 只通过 sidecar JSONL DTO 访问 AgentService，不导入 Runtime 或 SQLite。仓库没有 Tauri/Rust host，因此 local client 的完整真实桌面 host 流程仍需外部环境验证；不能把 frontend build 或 mock 测试当成真实桌面验收。

## 十一、权威性与下一步

当前应把以下内容视为权威边界：

- Service 生命周期和 durable facts：agent/service 与 agent/runtime_store。
- Runtime 状态和阶段转移：agent/runtime.py、agent/orchestrator。
- Execution effect truth：agent/executor、ExecutionVerifier、Artifact/Workspace facts。
- checkpoint resume 规则：agent/checkpoint、agent/run_resume，但其主路径接入仍未证明。
- Memory commit：agent/services/memory_service.py 与 agent/memory/persistence.py。
- 公共契约：接受的 ADR 与对应的当前测试；过时叙述需要通过后续 ADR 校正。

下一项最有价值的工程工作是先决定 Service resume 的正式契约：若目标是 checkpoint 精确恢复，需要明确由 Service 按 run/workflow 选择并调用 RunResumeCoordinator，随后补充主路径验收；若目标只是 re-launch，应相应降级 ADR/README 的表述。二者不应继续共用一个 resume 名称而不区分语义。
