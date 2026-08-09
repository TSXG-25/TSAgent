# P1 Broad Capability Acceptance — Freeze Record

- Frozen commit: `06cf0345`
- H1 baseline: `ef861196`
- H2 scope: unsupported effect truthfulness
- Freeze date: 2026-08-09

## Final evidence

| Metric | Result |
| --- | ---: |
| Full real-provider task success | 31/32 |
| Runtime contract failure | 1/32 (`MEM03`) |
| Security violation | 0 |
| Cross-context leakage | 0 |
| Duplicate side effect | 0 |
| False `COMPLETED` | 0 |
| Unsupported effect hallucination | 0 |
| H2 deterministic dataset | 9/9 PASS |
| Offline regression | 427 passed, 17 skipped, 2 deselected |
| mypy | PASS |

`MEM03` 的 Memory fact 写入、覆盖和检索证据通过方差检查；最终自然语言答案存在
模型质量波动，因此归类为 `MODEL_QUALITY_VARIANCE`，不再修改 Memory Runtime。

## Freeze invariant

```text
No verified effect evidence
→ no success claim
→ no COMPLETED
```

P1 的完整原始真实 API 结果由本次验收运行生成；本摘要是仓库内的永久口径，不包含
Provider secret、完整响应或工作区绝对路径。旧的 `acceptance_p1_round1.json` 保持
不变，继续表示早期 24/32 轮次，不能与本冻结结果混用。

## Deferred to P2

- Long-horizon progress preservation and bounded replan
- Real subprocess crash/recovery and event replay
- Soak/concurrency resource trends
- Provider portability
- Cancellation/Timeout、Provider Failover 和分布式执行
