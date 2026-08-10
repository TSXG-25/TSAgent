# P2 Harness

当前 Harness 已实现 P2-L、P2-S1 和 P2-R1；P2-P 仍需第二 Provider：

```text
benchmarks/p2 Dataset
        ↓
fixture / future Runtime adapter
        ↓
RunTraceEvidence
        ↓
RuntimeInvariantResult
        ↓
Capability Outcome + Runtime Correctness + Performance Report
```

运行离线 fixture：

```bash
python -B -m realtest_reports.harness.p2.runner --mode fixture
```

输出明确标记 `source=fixture`，不计入真实 Provider 或 Runtime 能力结果，也不自动
重跑失败 case。

P2-L 真实适配器只接受固定 case，不自动重试：

```bash
python -B -m realtest_reports.harness.p2.runner \
  --mode real --ids L01,L02,L03,L04,L05 \
  --results /private/tmp/p2_l_real.json
```

每个 case 使用独立 SQLite、Workspace 和 Run；报告会同时保存 Service Snapshot/Event、
Runtime evidence、文件地面真相、工具/LLM/Planner 计数和确定性不变量结果。真实
运行需要 Provider 配置，Provider 错误必须原样作为一次 attempt 记录，不能被自动
重跑覆盖。

## P2-S1 deterministic soak

```bash
python -B realtest_reports/harness/p2/groups/soak.py \
  --case all \
  --results realtest_reports/results/p2_s1_deterministic.json
```

S01–S04 使用真实 AgentService、SQLite、scoped Context/Workspace 和 durable Event
Repository，但用确定性 launcher 隔离 Provider 方差。

## P2-R1 true process crash/restart

```bash
python -B realtest_reports/harness/p2/groups/restart.py \
  --case all \
  --work-root /private/tmp/tsagent-p2-r1 \
  --results realtest_reports/results/p2_r1_round1.json
```

每个 R case 都在独立目录中执行以下流程：

```text
spawn worker A
→ 等待 fsync durable milestone marker
→ parent 发送 SIGKILL
→ 确认 child return code = -9
→ spawn worker B（新 SQLite connection / Context / EventBus）
→ AgentService rehydrate / resume
→ 对账 Snapshot、Events、Checkpoint、Ledger、Fence、Workspace 和 effect audit
```

Crash hook 只能观察已发生的生产里程碑并写 marker，不能创建被验收的状态。R01–R04
使用 deterministic executor，但 AgentService、SQLite Store、RunContext、Workspace、
RunResumeCoordinator、WorkflowExecutor、Artifact、Checkpoint、Fence 和 durable Event
均为生产路径。

永久证据：

- `realtest_reports/results/p2_r1_discovery_round1.json`：修复前 0/4 原始 FAIL；
- `realtest_reports/results/p2_r1_round1.json`：修复后统一 4/4 PASS；
- `realtest_reports/p2_r1_discovery.md`：discovery 根因及证据纪律。
