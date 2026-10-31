# Checkpoint / Resume Contract

## Checkpoint 事实

RunCheckpoint 是不可变事实，包含 run/workflow identity、workflow version、active stage/task、execution plan、completed stages、artifacts、verifier/effect records、idempotency、external guards、runtime evidence 和 lineage。Checkpoint 不自行决定“能否恢复”。

ResumeValidator 根据 identity、revision/fence、workflow/version、artifact digest、effect state、external state 和请求动作生成 ResumeDecision。失败必须 fail closed；只有允许的 RESUME_EXACT 或 REPLAY_FROM_STAGE 才能进入 WorkflowExecutor。

## Coordinator 路径

RunResumeCoordinator.resume_active 的职责是加载 run resume index 和 workflow，hydrate artifact，找到 checkpoint，创建 ResumeContext、staging buffer 和 WorkflowCheckpointRequest，调用 WorkflowExecutor，只有验证成功才提交 checkpoint、artifact 和 index。execute_or_resume 还负责把 pending workflow 原子激活。

该路径是可执行的组件路径，并由 restart/resume 测试和 realtest harness 直接使用。

## Service 路径的实际语义

AgentService.resume_run 的当前行为是：

    get run head
      -> 检查非终态
      -> takeover writer/fence
      -> RuntimeExecutionLauncher.resume
      -> 新建 UniversalAgent
      -> runtime.run(request.request_text)

当前没有证据表明这条路径读取某个 RunCheckpoint、选择 workflow/stage、构造 WorkflowCheckpointRequest 或调用 RunResumeCoordinator。ResumeRunRequest 的 checkpoint_id/action 也没有被 Service launcher 消费来执行精确 stage resume。

因此应区分：

| 名称 | 当前含义 | 证据 |
| --- | --- | --- |
| Service resume | durable takeover/re-launch | AgentService、RuntimeExecutionLauncher |
| Workflow resume | checkpoint validated stage continuation | WorkflowExecutor、RunResumeCoordinator、resume tests |
| Service checkpoint resume | 未证明接入 | 未找到 agent 主路径调用 |

ADR-0018 和部分旧架构文档把第三行写得比当前调用链更强，应标为 DOCUMENTED_ONLY，并在正式契约决策后通过新 ADR 校正。

## 取消不是恢复

cancel_run 写入 CancellationIntent，之后由 CancellationView、安全边界和 launcher convergence 进入终态。取消请求不应被解释成 resume，也不应绕过 checkpoint validation。
