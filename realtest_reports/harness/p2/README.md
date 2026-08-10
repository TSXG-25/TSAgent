# P2 Harness

当前 Harness 已实现 P2-L、P2-S1、P2-R1 和 P2-P 的 Provider-neutral
执行/证据管线；P2-P 的真实双 Provider 结果仍 deferred：

```text
benchmarks/p2 Dataset
        ↓
fixture / future Runtime adapter
        ↓
RunTraceEvidence
        ↓
RuntimeInvariantResult
        ↓
case.hard_gates → RuntimeCaseScore
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

Runtime 判定只使用 Dataset 为该 case 冻结的 `hard_gates`。例如缺少 required artifact
会让 Capability FAIL；若 Run 已如实进入 `FAILED_TERMINAL/BLOCKED`，它只作为诊断，
不会被误报成 Runtime FAIL。若 Run 声称 `COMPLETED`，相同缺失仍会触发
`false_completed`。真实适配器还会保存脱敏后的 tool name、target、success，不保存
工具内容或 Provider prompt，并为每个 case 使用独立 tenant/user/session scope。

首次真实 discovery（commit `697ce06d`）永久保留为：

- `realtest_reports/results/p2_l_real_discovery_697ce06d.json`：原始 5-case 报告；
- `realtest_reports/results/p2_l_real_rescored_697ce06d.json`：不重跑 Provider，按冻结的
  case hard gates 重算。结果为 Capability `2/5`、Runtime `4/5`；L05 因重规划重复
  已完成 Task 保持 Runtime FAIL，并由独立生产 hotfix 处理。

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

## P2-P Provider portability

先运行离线合同与报告验证（不会调用 Provider）：

```bash
python -B -m realtest_reports.harness.p2.groups.portability \
  --mode fixture \
  --results /private/tmp/p2_p_fixture.json
```

fixture 会构造 primary/secondary 两个变体下的 P01–P03 六个尝试，用于验证：

```text
固定 prompt / fixture hash
同 case 双 Provider parity
Capability Outcome 与 Runtime Correctness 分离
P03 unsupported-effect + malformed structured-response 两个固定 probe
secret-free Provider evidence
DEFERRED / INVALID / EXECUTED 状态区分
```

它明确标记 `source=fixture`、`real_executed=0`，不能作为真实 Provider 能力证据。

真实双 Provider 模式：

```bash
P2_SECONDARY_PROVIDER=<provider-name> \
P2_SECONDARY_API_KEY=<secret> \
P2_SECONDARY_MODEL=<model> \
P2_SECONDARY_BASE_URL=<openai-compatible-url> \
python -B -m realtest_reports.harness.p2.groups.portability \
  --mode real \
  --ids P01,P02,P03 \
  --work-root /private/tmp/tsagent-p2-p \
  --results /private/tmp/p2_p_real.json
```

每个 case/provider 在独立子进程中运行，且在 Runtime consumer import 前只安装一个
Provider adapter，不允许生产 Router 自动切到另一个 Provider。相同 case 使用完全
相同的 prompt/fixture hash；任何漂移直接判 `INVALID`。缺少 Provider 配置时判
`DEFERRED` 且不启动子进程，不会伪造 Capability FAIL。

首次真实 primary discovery（commit `697ce06d`）中，primary P01–P03 的 Capability /
Runtime 均为 `3/3 PASS`，secondary 因缺少配置为 `3 DEFERRED`。原始报告和不重跑
Provider 的聚合修正版分别保存在：

- `realtest_reports/results/p2_p_primary_discovery_697ce06d.json`；
- `realtest_reports/results/p2_p_primary_corrected_697ce06d.json`。

修正版只修复 `python -m` 导致的跨模块 Enum identity 聚合错误，不改变任何 attempt
证据；双 Provider parity 仍明确保持 `DEFERRED`。

P03 的 malformed probe 是固定的边界故障注入：第一次 structured-response 调用产生
稳定 `MALFORMED_RESPONSE`，随后只允许现有 raw JSON fallback 继续；它不是自动重跑，
也不修改 Provider-specific prompt。

当前 scenario manifest hash：

```text
f08d66b97527557e9dc455aa2d35d1bbd56ef0d74f22759557b6b6a7fb2ca3f7
```
