# P2-S1 Deterministic Soak & Resource Harness

运行范围：fake provider；真实 `AgentService`、`RunContext`、SQLite Runtime Store、scoped Workspace 和 durable EventRepository。没有调用外部 Provider。

证据文件：[p2_s1_deterministic.json](results/p2_s1_deterministic.json)

## 结果

| Case | 场景 | 结果 | Runtime gates |
| --- | --- | --- | --- |
| S01 | 50 sequential short runs | PASS | 9/9 |
| S02 | 10 sessions × 5 runs | PASS | 9/9 |
| S03 | 10 concurrent runs，强制 barrier 交错、同名相对路径 | PASS | 9/9 |
| S04 | 500 event replay/read cycles | PASS | 11/11 |

## Hard evidence

- cross-context、workspace、memory leakage：0
- duplicate side effect：0
- false `COMPLETED`：0
- orphan active Run：0
- subscriber leak：0
- SQLite busy/deadlock：0
- post-close active `RunContext` / workspace handle：0
- S04 event gap、cursor drift、replay append：0
- S04 Runtime execution count：1；replay 未触发第二次执行

资源采样包含 `baseline`、每 10 个 Run、`pre-close`、`post-close-gc`；RSS 和文件描述符作为趋势证据记录，不设置未经基线校准的硬阈值。

## 边界

本报告证明确定性生命周期、作用域隔离和 durable event replay 在 soak 条件下成立；不证明真实 Provider 的模型质量、长链能力或 crash/restart 能力。P2-R 与 Provider-backed L01–L05 仍按计划延期。
