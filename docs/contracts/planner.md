# Planner Contract

## 角色

PlannerStage 把用户请求和受限上下文转换为以下之一：

- 可执行 Task 与其 ExecutionPlan；
- 直接回答及 terminal evidence；
- clarification、blocked 或 provider failure；
- 特定场景下由专用 WorkflowExecutor 产生的结果。

Planner 不直接把工具副作用当作成功，也不拥有 Runtime 的终态提交。

## 输入与上下文

ContextBuilder 汇总原始请求、ScopedMemoryView、ConversationTracker、历史 run 结果、已有 artifacts、repository index、workspace 和身份。ReferenceResolver、IntentEngine、freshness/source policy 进一步确定请求类型、引用解析、来源要求和 effect authorization。

PlannerStage 根据这些事实选择 simple chat、research、deterministic task、generic planner 或 workflow route。fresh research 必须有 registry web-search 能力；缺少 source 或 tool 时应产生可见的 blocked 结果。

## 输出与 lowering

Gate A 与 Gate B 必须分别验收。IntentEngine 先判断交付形式，再分类话题：制作工具、网页、项目、脚本或成份报告，即使没有文件路径，也要求 artifact_required=true，并由解析器补入 FILE_MUTATION、强制 requires_execution=true。domain=数学不能把计算器开发降成一次计算。仅解释、步骤或聊天代码示例不要求文件。Gate B 的 verified evidence 不能补救 Gate A 漏识别。

HA-R4 的离线测试只验证已识别语义穿过 Service 后进入真实文件执行，不衡量 Provider 的语言识别准确率，也不验证报告数量或程序功能。真实语义评测须单列原句、未进入提示词的变体和反例；Provider 错误单列。布尔 artifact_required 本身不能表达“两份”的数量要求，不能据此认定整个任务完成。

目标契约现由 `GoalState` 保存原始请求、goal ID/version、requested outcomes、逐项文件交付数量、禁止效果和验收条件。Service 终态 continuation 持久化此契约及验收证据，后续纠正从同 session 最近的终态 Run 恢复身份并递增版本。Finalizer 只接受经 ExecutionVerifier 核验的不同文件路径/摘要；当前文件验收器支持字面必含文本与多文件摘要互异，未支持或未满足的条件保持未完成，不委托 LLM 自评。

ADR-0035：通过命令生成文件时，execute Task 必须用 output_paths 声明预期文件或目录。声明只是待核验清单且受原始请求授权约束；不能把它当作已产生的 Artifact。stdout-only 命令不需要文件声明。

ADR-0034：命名 Python 文件并要求执行，不足以跳过完整语义分析；创建、修改和执行要求必须一起保留。删除/复制/移动的字面快捷路径只接受完整独立操作，复合目标与否定/保留约束交给通用 Planner。有语义文件变更要求但无词法路径绑定时，仅允许匹配原始请求明确路径的语义目标；模型改写不能扩大授权。

确定性文件、转换、追加、合并、代码和命令请求先构造 canonical Task，再由 Compiler 生成 ExecutionPlan。generic planner 的多次尝试仍必须回到同一 Task/Compiler 边界。空计划不得伪装成成功执行。

SIMPLE_CHAT/no-execution 路径可以直接调用 LLM，但其回答不是文件 effect 的 verified evidence。Planner 通过 Runtime state 和 orchestration result 把输出交回 Runtime；最终是否可报告由 output policy、freshness 和 Finalizer 判断。

## Workflow route

特定开发/题目请求可以加载 Workflow 并直接调用 WorkflowExecutor.execute。当前 planner.py 传入的 ExecutionContext 没有 checkpoint_request，因此该路径是普通 Workflow 执行。它不能作为 Service checkpoint resume 已接入的证据。

## Replan 现状

`UniversalAgent` 的 `REPLAN` 状态会委托给 `ExecutionOrchestrator.replan`。普通动作失败只有在失败任务属于只读操作、且错误属于已知的无效果类别（`FILE_NOT_FOUND`、`NO_SEARCH_MATCH`、`HTTP_404`、`INVALID_TOOL_ARGUMENT`）时才进入该状态。结构性失败仍由 `FailurePolicy` 处理；执行、写入及效果未知的失败不会自动重放。

重规划请求携带原始目标、当前版本化目标契约、失败/未完成任务和最近动作观察。生成的替代任务在编译前重新校验原始写入范围与执行授权；越权候选计划以 `EFFECT_SCOPE_VIOLATION` 阻塞。已成功或跳过的任务及其执行计划保留，替代计划覆盖原有未完成任务，避免旧计划与新计划并行重复。已接受的替代计划以不含任务 ID、状态、观察和事实的 SHA-256 指纹记录；空计划或重复计划以 `REPLAN_NO_PROGRESS` 终止。重规划消耗共享 `RunBudget` recovery 配额，最大轮数仍由该预算约束。

该接线由 Runtime 状态机集成测试覆盖；安全失败分类、越权拒绝、已验证写入保留和重复计划终止分别由离线回归测试覆盖。真实 Provider 的重规划质量仍须在独立真实场景评测中验证。

## 证据

当前实现：agent/orchestrator/planner.py、agent/orchestrator/context_builder.py、agent/cognition/intent_engine.py、agent/cognition/reference_resolver.py、agent/compiler。Planner capability 的 Dataset/Oracle 位于 evals/planner，但 self-check 只证明数据与 Oracle 一致，不代表真实 Provider 通过。
