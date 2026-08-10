# v2.3D Cancellation / Timeout Contract Dataset

本目录是 ADR-0023 的固定评测合同。Dataset/Oracle 本身不调用 Provider、Tool、
SQLite 或生产 Runtime；D2/D3 的生产实现证据由对应 integration tests 提供，不能用
Dataset PASS 代替 Runtime capability 结论。

## 固定范围

Dataset 共 16 例：

```text
C01–C08   request lifecycle / safe boundaries
C09–C10   durable intent across process restart
C11–C12   Run timeout vs Tool timeout policy
C13–C16   multi-workflow, client, fencing, committed effects
```

它冻结三层语义：

```text
durable InterruptionIntent
        ↓
Reason-specific policy
        ↓
safe boundary / terminal outcome / resume rule
```

所有 hard gate 都是零容忍：

```text
post_cancel_new_side_effect
duplicate_cancel_transition
false_cancelled_before_durable_flush
completed_effect_silently_lost
cancelled_run_auto_resumed
atomic_transaction_torn_by_cancellation
terminal_snapshot_event_mismatch
stale_writer_after_cancel_accepted
cancel_intent_lost_after_restart
timeout_misclassified_as_completed
```

运行：

```bash
python -B -m benchmarks.v23d.validate
pytest -q tests/test_v23d_interruption_contract.py
mypy agent/interruption benchmarks/v23d
```

D2 已实现 durable cancellation intent、AgentService `cancel_run()`、事件和状态投影；
D3 已接入 Planner/Provider/Tool/Workflow 安全边界、Provider 可中止等待和 Run timeout
watchdog。真实 Provider/CLI E2E 继续由 D4 验证。
