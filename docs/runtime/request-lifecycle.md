# Request Lifecycle

## Public Service path

一次 CLI 请求的当前路径是：

    main.py
      -> create_default_agent_service
      -> AgentService.start_run
      -> SqliteRuntimeStore.reserve_service_start
      -> ServiceContextFactory 创建 SessionContext / RunContext
      -> RuntimeExecutionLauncher.start
      -> UniversalAgent.run(request_text)

AgentService 在启动前建立 request/run identity、幂等 reservation 和 durable head。RuntimeExecutionLauncher 负责把运行交给 Runtime，并在退出时关闭 Runtime、解除进程内上下文和写入终态 bundle。

客户端通过 stream_events 读取 durable event repository，通过 get_run 读取 RunProjector 的公共 snapshot。sidecar 的 JSONL dispatcher 只做 DTO 转换和方法 allowlist，不绕过 Service。

## Direct API path

agent/api.py 的 TSAgent 是 AgentService 的薄门面，其 run() 与 Service lifecycle 一致（SQLite reservation、service event stream、durable history）。原 SessionRuntime 已删除；UniversalAgent 仅作为 launcher 内部运行时与单元测试入口存在。

## 短路请求

UniversalAgent 在进入 Planner 前处理两类特殊输入：

1. “上一轮输出”类请求从当前 scope 的最近终态 RunOutput 读取结果，不调用 Planner、Tool 或 LLM。
2. 无效输入直接产生 blocked evidence。

其他请求进入正常 INIT -> PLAN -> EXECUTE 流程。request、tenant、user、session 和 run identity 是跨层传递的基本事实；内部 Task、Checkpoint 和 Planner cache 只留在内部层。

## 完成

正常结束时，Runtime evidence 先交给 launcher；launcher 检查 terminal outcome、output、freshness、budget 和 artifact commit facts，再通过 SQLite transition_run_with_event 原子写入终态、run_output、artifacts 和 terminal event。

## 证据

main.py、agent/service/service.py、factory.py、context_factory.py、runtime_launcher.py、local_sidecar/dispatcher.py、agent/runtime.py。
