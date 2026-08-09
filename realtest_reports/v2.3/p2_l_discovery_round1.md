# P2-L Discovery Round 1

- Commit: `5f36b1f7`
- Dataset hash: `9f33d2ee400651a2980cf876d054aef93fc86a50dd4791ffbceff2558df934f6`
- Provider: `primary`
- Cases: L01–L05, one attempt each
- Result: Capability `0/5`; Runtime Correctness `0/5`
- P2-L accepted: **No**
- P2-R started: **No**

## Case summary

| Case | Terminal status | Capability | Runtime | False `COMPLETED` | Missing artifacts | Workspace leakage |
| --- | --- | --- | --- | ---: | ---: | ---: |
| L01 | `FAILED_TERMINAL` | FAIL | FAIL | 0 | 1 | 1 |
| L02 | `COMPLETED` | FAIL | FAIL | 1 | 2 | 1 |
| L03 | `FAILED_TERMINAL` | FAIL | FAIL | 0 | 1 | 1 |
| L04 | `COMPLETED` | FAIL | FAIL | 1 | 3 | 1 |
| L05 | `COMPLETED` | FAIL | FAIL | 1 | 2 | 1 |

## Root cause classification

这不是 Long-horizon 模型质量问题，而是 Runtime Context Boundary failure：

```text
RunContext.workspace = isolated temporary workspace
        ↓
filesystem Tool / verifier 使用 process-global ROOT
        ↓
文件写入项目进程根 output/
        ↓
Run 的隔离 Workspace 没有目标 artifact
        ↓
Service 仍可能发布 COMPLETED
```

L01–L05 均检测到目标文件不在 Run 隔离 Workspace；L02/L04/L05 同时产生了
`COMPLETED` 与缺失 required artifact 的矛盾，构成 false-success。L03 没有假完成，
但仍证明写入边界错误。

本轮还观察到 Provider structured-output `400`、connection/timeout 日志；这些不是
本轮 Runtime 结论，且当时 adapter 尚未按 case 持久化 `provider_errors`。后续 adapter
已加入该字段，但不自动重跑本轮结果。

## 处理决定

在进入 P2-R 之前必须开 Runtime hotfix，至少证明：

1. filesystem tool、Verifier 和 Artifact projection 统一使用 `RunContext.workspace`；
2. 不再从模块级 `tools.filesystem.ROOT` 推导 Run 产物；
3. 目标 artifact 缺失时不能发布 `run_completed`；
4. 两个 Run 在同名相对路径下仍保持隔离；
5. 修复后重新跑 L01–L05，保留本轮 discovery 结果，不覆盖历史 attempt。

因此当前 P2 顺序冻结为：

```text
P2-L discovery  ❌ blocked by Runtime Context Boundary failure
Runtime hotfix  ← next
P2-L clean rerun
P2-R            deferred
```
