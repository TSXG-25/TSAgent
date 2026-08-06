# TSAgent v2.2 永久验证归档

本目录固定保存 v2.2.0 的 Contract、Workflow Resume 和 Run-level P0 证据。
实现基线为 `14af2ad4`；目录中的报告不依赖 `/private/tmp` 或其他临时目录。

## Milestone commits

| Milestone | Commit | Scope |
| --- | --- | --- |
| v2.2A | `6c01d46108b7999a661485bbec4fdfd408fe8ea8` | Run Checkpoint Contract |
| v2.2B | `d345e7d38858343b72aae1d3f9a07d7ffb174156` | Workflow Resume Runtime |
| v2.2C | `14af2ad44ba2e67aef3789ccee50b20e2114d5f1` | Run-Level Multi-Workflow Resume |

## Reports

- [checkpoint_contract.json](./checkpoint_contract.json)：v2.2A 真实 DeepSeek API 边界，20/20 PASS。
- [workflow_resume.json](./workflow_resume.json)：v2.2B 单 Workflow Resume，离线集成 7/7 PASS。
- [run_resume_p0.json](./run_resume_p0.json)：v2.2C C01–C08 真实 DeepSeek P0，8/8 PASS。

## Dataset and benchmark provenance

- Run Resume Dataset version: `v0.1`
- Contract version: `adr-0018-v1`
- Run Resume Dataset hash:
  `512ba9b37feb0701618c2b0eab62ac646502cbbee6c562c351bce80622c1b908`
- Checkpoint Dataset hash:
  `a399651d4f0557502af4fdf68aa2521276582d3df289af0b4f3308ddfd18a160`
- P0 source report SHA-256:
  `c87c8deadf43df5e67d2beffcb383c3520b58b9c5597dceeefc1aa7a6492dd70`
- Archived report SHA-256:
  - `checkpoint_contract.json`: `4c8a204b19dcbcc9f1fd6922272fd005ee4340c5ad35fd905a2578b9254aa945`
  - `workflow_resume.json`: `a5f9cfd80d586f4d251881c66914b9920e91a0e4f82cce0c23d94b3d7d2631cb`
  - `run_resume_p0.json`: `6a7046c7dd69c2d31e298921b313a6880a88c4ca4becc87975434bb6f74fd650`

## P0 gate

```text
C01–C08                         8/8 PASS
Raw E2E Rate                    100%
Runtime Capability Rate         100%
Provider Error Rate             0%
Correct Workflow Resume         100%
Completed Workflow Skip         100%
Duplicate Side Effect Rate      0%
Unsafe Resume Acceptance        0%
Artifact Integrity              100%
Process-Restart Recovery        PASS
```

## Deferred validation

- v2.2A 多 Provider / 多 SDK 实际矩阵尚未建立；当前真实边界证据来自 DeepSeek。
- 真实 API 专用 CI job 尚未强制 `executed == expected`；离线 CI 可因 Provider 不可达而 skip。
- v2.2B 的 Provider timeout/failover 不属于 Workflow Resume Contract，本版本不扩张范围。

## Post-close backlog

- C09：版本不兼容增强证据。
- C10：Provider timeout / failure resilience。
- C11：上游 Workflow 失败隔离。
- C12 二次进程启动已由 C02/C03/C05/C07 覆盖，不重复建设。
- v2.3 Planner、Tool Selection、Replay Compare、SDK/API。

## Scope statement

v2.2.0 不引入 Planner 重规划、并发/分布式 Workflow、Provider Failover 或第二套
Orchestrator。`RunResumeCoordinator` 只负责 Run 定位、原子激活、恢复委派、Artifact
发布和索引提交；Stage/Task 恢复仍由 v2.2B `WorkflowExecutor` 与 `ResumeValidator`
承担。
