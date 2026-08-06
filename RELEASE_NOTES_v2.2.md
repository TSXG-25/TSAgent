# TSAgent v2.2.0 Release Notes

发布日期：2026-08-06

## 发布基线

v2.2.0 的 Runtime 实现基线为：

```text
14af2ad4 v2.2C: Run-Level Workflow Resume 收口
```

发布说明和证据归档作为该基线之上的 documentation-only release metadata 提交，
不包含额外 Runtime 改动，也不包含工作树中的并行 WIP。

## 版本演进

| Milestone | Commit | 内容 |
| --- | --- | --- |
| v2.2A | `6c01d461` | Run Checkpoint Contract、Codec、Guard、Validator、Dataset |
| v2.2B | `d345e7d3` | Workflow Checkpoint、Exact Resume、Stage Replay、副作用安全 |
| v2.2C | `14af2ad4` | Run-level Multi-Workflow Resume、进程重启、P0 E2E |

## Highlights

### v2.2A — Checkpoint Truth Model

- 不可变 `RunCheckpoint` 与追加式 Checkpoint Store。
- Canonical JSON、digest、版本兼容性和 ExternalStateGuard。
- 禁止 live object、Exception、HTTP response、file handle 进入 checkpoint。
- DeepSeek 真实 API 边界测试：20/20 PASS。

### v2.2B — Workflow Resume Runtime

- `RESUME_EXACT` 与 `REPLAY_FROM_STAGE` 的确定性恢复。
- 已完成 Stage/Task 跳过，幂等 Stage 可重放。
- UNKNOWN、STARTED、FAILED_AFTER_COMMIT 和冲突副作用 fail-closed。
- Workflow Resume Runtime 离线集成测试：7/7 PASS。

### v2.2C — Run-Level Multi-Workflow Resume

- `RunResumeIndex`、`RunResumeResolver`、`RunResumeCoordinator`。
- pending → active 原子激活与 revision/attempt 幂等控制。
- 同一 Run 内 Workflow 独立 Checkpoint lineage。
- Artifact hydration、terminal output completion gate。
- 已提交文件副作用的 digest reconcile，避免重复 Provider 调用和重复写盘。
- C01–C08 真实 DeepSeek P0：8/8 PASS。

## Verification

### P0 Real Provider Gate

| 指标 | 结果 |
| --- | --- |
| Raw E2E Rate | 100% |
| Runtime Capability Rate | 100% |
| Provider Error Rate | 0% |
| Correct Workflow Resume | 100% |
| Completed Workflow Skip | 100% |
| Duplicate Side Effect Rate | 0% |
| Unsafe Resume Acceptance Rate | 0% |
| Artifact Integrity Rate | 100% |
| Process-Restart Recovery | PASS |

### Contract and offline gates

- Run Resume Dataset：16/16 PASS。
- Checkpoint Dataset：16/16 PASS。
- Contract Verification：PASS。
- Architecture Verification：PASS。
- mypy：38 个相关源文件无问题。
- 全量离线测试：265 passed；17 个 Provider 不可达测试按规则 skip。

详细证据固定在 [realtest_reports/v2.2/](realtest_reports/v2.2/)，P0 原始报告也保留在
[benchmarks/v22c/results/p0_round1.json](benchmarks/v22c/results/p0_round1.json)。

## Important findings recorded

1. C03：Artifact 未水合会导致恢复路径零执行却假成功。
2. C07：Checkpoint lineage 缺失会导致重复 Provider 调用与重复副作用。
3. P0 统一运行中 C03 的首次失败是 Harness stale field 误报，不是 Runtime 回归。

## Deferred validation

- v2.2A 的多 Provider、不同 SDK/响应形态验证：当前真实边界证据来自 DeepSeek，
  结构化 Codec 已隔离供应商差异，但尚未形成多供应商矩阵。
- 真实 API 测试在 Provider 不可达环境中的强制执行：离线 CI 允许 skip，专用真实 API
  job 仍应要求 `executed == expected`。
- C09 版本不兼容、C10 Provider timeout、C11 上游失败隔离：已有 Contract/Dataset
  或离线验证，不作为 v2.2.0 P0 阻塞项。

## Post-close backlog

- C09–C11 增强证据；C12 二次进程启动已由 C02/C03/C05/C07 覆盖。
- v2.3 Planner Capability。
- v2.3 Tool Selection Capability。
- Replay 比较、公开 SDK/API、Workspace AST/LSP 能力。

v2.2.0 不引入 Planner 重规划、并发/分布式 Workflow、Provider Failover 或新的
Orchestrator。
