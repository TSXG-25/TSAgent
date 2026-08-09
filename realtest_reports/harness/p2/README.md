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
