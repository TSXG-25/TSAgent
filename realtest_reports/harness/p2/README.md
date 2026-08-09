# P2 Harness

当前提交实现 P2-L 的证据管线和离线 fixture：

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
重跑失败 case。真实 Provider adapter、进程 kill、Soak 和第二 Provider 分别属于
后续 P2-R、P2-S、P2-P 提交。

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
