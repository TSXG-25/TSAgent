# Testing Strategy

## 测试边界

本仓库负责代码、缺陷、架构和离线契约验证，不负责真实生产流程、真实用户操作、现场设备、线上部署或未经配置的外部 Provider 验证。真实场景结果应继续归档到 realtest_reports，并和 Runtime Correctness、Capability Outcome、Provider Error 分开。

## 分层

1. Unit/contract：验证 schema、policy、compiler、verifier、checkpoint validator、memory policy 等纯规则。
2. Architecture gates：验证 single Runtime spine、禁止隐式 replan、context ownership 和模块边界。
3. Runtime/service integration：验证 AgentService、SQLite revision/fence、事件、artifact、output、cancel/timeout 和 launcher。
4. Deterministic benchmark：benchmarks/ 验证 Runtime 正确性和不变量，不代表模型能力。
5. Capability eval：evals/ 使用 Dataset/Oracle 评估 planner/selection/workflow 等能力；Planner self-check 不等于真实 Provider 通过。
6. Real-provider harness：realtest_reports/ 记录脱敏的真实模型/Provider evidence，应明确 Raw E2E、Capability、Runtime Correctness 和 Provider Error 口径。
7. Desktop：apps/desktop 的 build/client harness 验证协议和 mock/local client；真实 Tauri host 需要外部环境。

## 本次离线验证命令

文档完成后应至少运行：

    rg -n "RunResumeCoordinator|checkpoint_request|replan" agent tests
    python3 -m pytest -q tests/test_h8_single_runtime_spine.py tests/test_run_resume_runtime.py
    python3 -m pytest -q tests/test_architecture_verification.py tests/test_v23b_architecture_gate.py

若工作区存在用户已有 dirty changes，应记录完整 git status，并把与本次文档无关的失败归因保持为未隔离，而不是修改生产代码以“修绿”。

## 解释测试结果

- 通过 checkpoint 单测：只证明 checkpoint contract 的组件行为。
- 通过 Service 测试：证明测试使用的 launcher/service 路径，不自动证明真实 Desktop host。
- 通过 realtest：证明报告中声明的 provider、模型和 fixture 条件，不自动证明所有 Runtime contract。
- 静态调用搜索无结果：可以支持“未找到当前调用”，但不能证明未来动态导入绝对不存在；需要结合明确 import/call sites 和 targeted tests。

## 回归要求

修改 Runtime、Service、Workflow 或 Memory 时，应同时更新对应 contract、lifecycle 和 quality evidence。若改变 resume 语义，必须添加主路径验收，更新 ADR 和 README，并保留精确 checkpoint 与 re-launch 的区分。
