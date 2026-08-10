# P2 Runtime Endurance & Portability Acceptance

这是 P1 之后冻结的 Contract / Dataset / Oracle。它定义评测证据；各 Runtime
实现切片的完成状态与真实 Provider 证据必须在 Harness/ADR 中单独记录，不能由
Dataset 存在本身推导。

## Dataset

共 16 例：

| 组 | 数量 | 关注点 |
| --- | ---: | --- |
| `L01–L05` | 5 | 10–20 步长链、进度保留、有限重规划、产物完整性 |
| `R01–R04` | 4 | 真实子进程 kill、恢复、副作用对账、事件 replay |
| `S01–S04` | 4 | 50 次顺序、10×5 Session、10 并发 Run、500 replay/read |
| `P01–P03` | 3 | 同一场景双 Provider，不重新提示 |

## 双层评分

每个已执行 case 必须同时记录：

```text
Capability Outcome: PASS / FAIL / PARTIAL
Runtime Correctness: PASS / FAIL
```

模型没有完成任务但 Runtime 正确进入 `FAILED`/`BLOCKED`，属于 Capability 失败、
Runtime 正确，不能被归类为假成功。

## P2 硬门禁

下列指标任何非零都阻塞 P2 收口：

```text
False COMPLETED
Duplicate Side Effect
Cross-context Leakage
Security Violation
Stale Writer Acceptance
Terminal Snapshot/Event Mismatch
Durable State Loss
Completed Workflow Re-execution
Unsupported Effect Hallucination
```

事件丢失、孤儿 active Run、subscriber 泄漏和 SQLite 死锁/未处理 busy 也作为
Soak/Restart 的安全门禁记录。

## 性能基线

按 case 的 `performance_profile` 统计，而不是使用一个全局 timeout：

```text
wall_ms
provider_ms
llm_calls
replans
tool_calls
time_to_first_event_ms
time_to_first_artifact_ms
```

运行合同校验：

```bash
python -B -m benchmarks.p2.validate
mypy benchmarks/p2 tests/test_p2_acceptance_contract.py
```

P2-L/R/S 已有独立执行 Harness；P2-P 已有固定 P01–P03、统一 Provider adapter、
子进程隔离、证据采集和双层评分 Harness。真实双 Provider 已按相同
dataset/scenario hash 各执行一次：primary Runtime `3/3 PASS`，secondary Runtime
`3/3 PASS`；最终证据见 `realtest_reports/v2.3/p2_freeze.*`。fixture 结果仍不得计为
真实能力。
