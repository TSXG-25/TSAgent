# ADR-0028: Single Runtime Spine Convergence（H8）

- 状态: Accepted — H8 Core Implemented, Offline Verified, and Compatibility Gate Verified
- 范围: H8 Runtime control convergence
- 前置基线: 当前工作树 `261f4cb6` + 未提交 result-driven WIP

## 1. 问题

TSAgent 当前同时存在三套“下一步/失败/结束”控制来源：

1. 新的 `GoalState → NextAction → ActionResult` result-driven loop；
2. 旧的 `RECOVER → replan()` 与 `MAX_REPLAN`；
3. 独立但尚未接入 Runtime 的 `Reflection → Decision` 模块。

这会导致同一个失败同时拥有多个 retry budget、多个终态判断和多个恢复入口。已有
`GoalState`、`AgentInbox`、`NextAction`、`ActionResult` 已经进入工作树，不能继续被视为
实验性孤岛；它们必须成为唯一生产主链，或被明确回退。

## 2. 决定

H8 将生产控制链收敛为：

```text
Goal
  → optional Plan guidance
  → NextAction
  → Execute
  → ActionResult
  → Verify
  → Goal complete / next action
```

普通动作失败是下一轮的 observation，不直接进入 Reflection，也不自动调用 heavyweight
Planner。只有结构性失败进入异常支路：

```text
FailureEvent
  → FailurePolicy
  → Reflection
  → Decision
  → RecoveryDirective
```

Runtime 只依赖 `FailurePolicy` / `RecoveryDirective` 合同，不直接依赖 Reflection 或
Decision 的实现。

## 3. 失败分层

### Action-level failure

例如 `FILE_NOT_FOUND`、`NO_SEARCH_MATCH`、`COMMAND_NONZERO_EXIT`、`HTTP_404`、
`BINARY_FILE`、`INVALID_TOOL_ARGUMENT`。它们必须作为结构化 `ActionResult` 进入
`AgentInbox.next_step`，由下一次 `NextAction` 决定是否继续。

### Structural failure

例如 `TOOL_REGISTRY_UNAVAILABLE`、`CONTRACT_VIOLATION`、`RUNTIME_INVARIANT_BROKEN`、
`STATE_CORRUPTION`、`REPEATED_NO_PROGRESS`、`PERMISSION_BOUNDARY`。它们由
`FailurePolicy` 产生 `RecoveryDirective`，不得绕过该边界直接触发旧的 `replan()`。

## 4. 预算

生产恢复只允许一个统一的 `RunBudget` 管理动作数、目标轮次、结构性恢复次数和运行
时间。Task-level tool retry 可以保留，但不能隐式增加 goal/recovery round。`MAX_REPLAN`
不再是生产控制权；在迁移期间只能作为兼容投影，完成验证后删除。

Cancellation/Timeout 的 durable 状态和 watchdog 仍由 ADR-0023 管理，不并入 H8 的
FailurePolicy。

## 5. 依赖方向

失败事实与分类属于 `agent.failure` 生产包。`agent.reflection`、Runtime 和 Service
只能依赖生产合同；`evaluation` 只能依赖生产合同，禁止生产代码 import `evaluation`。

## 6. Workspace 与 Compiler 边界

生产执行必须显式持有 `RunContext.workspace`。缺少 scoped workspace 是契约错误，不得
退回 process-global `compat.workspace`。Compiler 必须显式 Rule 或绑定动作路径；工具
Registry 不可用或工具不存在时必须失败，禁止 catch-all 当作工具存在。

## 7. 暂不处理

- 不迁移 Cordis / DeepSeek Harness 的插件、HMR、JS Workflow 或 multi-agent 体系；
- 不物理拆分 `runtime_store/sqlite.py`；先通过领域 facade 形成边界；
- 不立即冻结整个 `Task`。后续方向是 immutable planned intent 与 mutable execution
  projection 分离，但不作为 H8 的机械重构目标。

## 8. H8 验收门槛

- 生产 `agent/**` 不 import `evaluation/**`；
- 普通 Action failure 不调用 Reflection/Decision；
- Structural failure 经过唯一 `FailurePolicy`；
- `RECOVER → replan` 不再拥有独立生产控制权；
- 不存在第二套隐式 retry budget；
- Structural `RETRY` / `SWITCH` 只操作已有 Task/NextAction 投影；不得生成新 Planner 计划；
- 缺少 ToolRegistry / unknown tool 不得编译成功；
- 无 scoped workspace 不得回退到 global workspace；
- Goal 未完成不得产生 `COMPLETED`；
- 离线回归与 H8 Dataset 全绿。

H8 的 structural recovery integration regression 还会验证：Planner 被禁止参与
`FailurePolicy` directive 的执行，retry 会将原动作重新置为 pending，recovery budget
耗尽会直接进入 terminal failure。Planner 的 `replan()` 仅保留给独立历史测试/迁移代码，
不再是 Runtime 的生产恢复入口。

历史兼容适配器仍保留在 `agent/compat/**`，供明确标记的迁移/旧 API 使用；但
`agent/` 生产模块不得 import `agent.compat`。Workspace、Diagnostics、Artifact
projection、Conversation tracker/retriever 已改为显式 scoped dependency 或明确的
无资源/隔离语义，并由 Architecture Verification 与 H8 regression gate 约束。兼容
适配器本身不是生产 Runtime 的事实来源。唯一保留的旧入口是
`MemoryRuntime.reset()` 在未传入 tracker 时为旧调用者重置历史全局 tracker；
`SessionRuntime`/`UniversalAgent` 生产路径始终传入 Session-owned tracker，且该
lifecycle 文件被 Architecture Verification 标记为迁移边界。

## 9. 后续 H9

H9 是真实任务能力门禁，不以单纯 pytest 绿代替。至少记录：

- Goal Completion Rate；
- False Completion Rate；
- Recoverable Error Survival；
- Structural Recovery Success；
- LLM Calls / Completed Goal；
- Unnecessary Replan Rate；
- Median Completion Latency。

当前证据边界：H8 deterministic Dataset 为 8/8 PASS。H9 v3 offline manifest
包含 20 条记录，其中 19 条 capability case PASS，H916 因无 fresh source 为
`DEFERRED`；Runtime Correctness 为 20/20，False Completion 为 0，Runtime
Failures 为 0，Provider Errors 为 0。文件类 case 以 workspace 中的 Artifact
内容为真，只有明确要求“贴出输出”的 case 才要求答案包含输出 token。固定
v3 manifest hash 为
`73542bbc53099e63a7220e70b3375938cf012b2d1567b2b84e52ca7657916974`。

真实 Provider 证据必须与 offline 分开。修复 ContextPolicy、显式命令路由、
代码执行路由、执行回答措辞和 Ollama raw-text adapter 后，
Ollama/qwen2.5:14b 的 H903/H904/H905/H906 各有一次真实 PASS。H904
包含一次真实代码生成调用和 `run_python`，输出 `5050`；H906 证明
`filesystem.write → run_python_file`，且无需额外 LLM 内容生成。此前 H904
的超时来自 `langchain_openai → transformers → torch` 导入链，保留在历史
证据中。H912 不向外部 Provider 发送仓库源码，保持 safe-fixture/offline
证据。当前不宣称完整 20-case real capability acceptance。
