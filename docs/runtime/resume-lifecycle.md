# Resume Lifecycle

## 当前 Service resume

公共 Service 的 resume 当前是 durable takeover/re-launch：

    AgentService.resume_run
      -> 读取 run head
      -> 拒绝已完成/取消/超时终态
      -> prepare takeover operation
      -> RuntimeExecutionLauncher.resume
      -> 新建 UniversalAgent
      -> runtime.run(request.request_text)

它重新建立 Runtime 和 writer/fence，但当前没有读取 RunCheckpoint、解析 active Workflow stage 或调用 RunResumeCoordinator。ResumeRunRequest 的 checkpoint_id/action 没有形成 stage-level continuation。

## Checkpoint resume 组件

另一条独立路径是：

    RunResumeCoordinator
      -> load resume index/checkpoint
      -> hydrate artifacts
      -> validate_resume
      -> WorkflowCheckpointRequest
      -> WorkflowExecutor
      -> commit checkpoint/artifact/index

WorkflowExecutor 只有在显式收到 checkpoint_request 时才记录 Stage checkpoint，并消费 RESUME_EXACT 或 REPLAY_FROM_STAGE。artifact digest、workflow version、external guard、effect state 或 ResumeContext 不满足时，会返回 blocked result。

## 生产集成结论

RunResumeCoordinator 和 checkpoint 分支有 tests/test_run_resume_runtime.py、tests/test_workflow_checkpoint_runtime.py 及 harness 证据；静态搜索未找到它们从 agent/service、agent/runtime、CLI 或 sidecar 主路径调用的证据。因此只能声称“组件/测试可用”，不能声称“AgentService resume 已精确恢复 checkpoint”。

## 取消与 resume 的关系

取消是 interruption lifecycle，不是 resume action。取消后是否可再次 start/resume，先由 durable run head 和 fence 决定；任何 checkpoint continuation 仍需独立通过 ResumeValidator。

## 待决策

维护者需要选择正式语义：把 Service resume 接到 RunResumeCoordinator 并补主路径验收，或明确 Service resume 只是 re-launch，并同步下调 ADR-0018、README 和旧架构文档的描述。
