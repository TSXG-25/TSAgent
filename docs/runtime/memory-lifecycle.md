# Memory Lifecycle

## Turn start

Runtime 开始请求后，记录 user message；ContextPolicy 决定是否对显式 interaction 信息做 pre-answer learning。该学习只提交候选，不跳过 MemoryPolicy 或 persistence boundary。

## Retrieval 与 planning

Planner 前通过 ScopedMemoryView 获取 session 原始消息、session-scoped short-term/summary、user-scoped learned facts/resolutions，同时读取 ConversationTracker、历史 run results、artifacts 和 repository context。对话 namespace 按 tenant/session 隔离；显式授权的 learned facts 按 tenant/user 共享。

## Execution 与 finalization

Planner、ExecutionStage 和 Finalizer 使用同一 RunContext 的请求与运行投影。Finalizer/MemoryService 可以记录 full exchange；它不是对失败副作用的补偿，也不把未验证的答案变成 artifact。

回答完成后，Runtime 只对允许的 explicit personal facts 做 post-answer learning。取消或 timeout 不继续这一阶段。学习流程为：

    evidence
      -> MemoryLearningProvider proposal
      -> deterministic policy
      -> MemoryPersistenceBoundary.commit
      -> MemoryCommitEvidence

对外成功语义依赖 commit evidence；provider proposal 本身不表示已持久化。

## 存储层

- facts：SQLite，带 user、scope、category、key、evidence、source、revision。
- long-term summary：Chroma，自动摘要写入 session namespace/scope，不跨 session 检索。
- resolution：namespace 下的 JSON entries，保留 scope/canonical/evidence/source。
- short-term：namespace 下的 JSON/history。
- session：进程内 message buffer。
- conversation：SessionContext 所有的 ConversationTracker。

## Reset 与 scope

MemoryRuntime.reset 可清理一个 session 的对话 buffer、short-term、summary、resolution；facts reset 可独立指定 learning namespace 和 scope。清理当前 session 对话不会隐式清除同一用户其它 session 或 user-scope learned facts。

既有 user-scope summary 不做破坏性迁移，但新的 session view 不再检索它们。Summary provenance 仍需后续处理：会话内自动摘要可能混合用户与助手陈述。

## 证据

agent/runtime.py、agent/services/memory_service.py、agent/memory/learning.py、learning_provider.py、persistence.py、session.py、short_term.py、long_term.py、resolution.py、lifecycle.py、agent/conversation/state.py。
