# Memory Contract

## 写入闭环

MemoryService 的学习路径是：

    interaction / answer evidence
      -> MemoryLearningProvider proposals
      -> MemoryPolicy authorization
      -> MemoryPersistenceBoundary.commit
      -> durable facts、summaries、resolution 或 session history

Provider 只提出候选，不拥有写入权限。STORE、UPDATE、IGNORE、scope、sensitivity、secret、volatile、dedup 和 provenance 由 deterministic policy 决定。对外的“已记住”结论必须由 MemoryCommitEvidence 支持，不能只看 proposal。

## Runtime 生命周期

Runtime 可以在回答前学习显式信息，执行前通过 ScopedMemoryView 检索 session/short-term/long-term context，Planner/Finalizer 记录 exchange，回答后对允许的 explicit personal fact 再学习。CANCELLED/TIMED_OUT 不继续 post-answer learning。

facts 由 SQLite 支持；summary 使用 long-term store；resolution 使用 resolution memory；session/short-term 还有进程内和 JSON 存储。各层均应带 user/tenant scope、evidence 和 source。

## Conversation 与 Memory 的边界

agent/conversation/state.py 的冻结 ConversationState/ConversationTracker 是当前 SessionContext 的会话状态权威，包含 recent_goal、last_instruction、last_answer、turn_count、events 和 runtime_pending。agent/cognition/cognitive_context.py 中同名 ConversationState 是 Intent/Resolution timeline cache，不能替代前者。

## Scope boundary

`SessionContext.memory_namespace` 管理原始消息、short-term 对话和自动摘要；`learning_namespace` 与 learning scope 管理显式授权的 user facts 和 resolutions。Service namespace 分别按 `tenant:session` 与 `tenant:user` 构造。自动生成摘要只存入 session scope，不作为跨 session 用户记忆检索。

MemoryRuntime.reset 分别接收对话 namespace 与 facts namespace/scope；指定 scope 时只清该 scope 的事实。旧的 user-scope 自动摘要不会被新的 session-scoped view 检索；本修复不迁移或删除既有数据。

## 尚存风险

Summary provenance 仍未完全解决：本地会话摘要可含 assistant 陈述，尽管已被限制在原 session，仍可能影响该 session 后续回答；真实模型对非可信记忆/仓库内容的注入抵抗也尚未验收。

## 证据

主要实现：agent/services/memory_service.py、agent/memory/learning_provider.py、learning.py、persistence.py、session.py、short_term.py、long_term.py、resolution.py、lifecycle.py。相关学习、scope、commit evidence 测试应与生产路径测试分开解读。
