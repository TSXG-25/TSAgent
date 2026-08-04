# v2.0 RC Demo 验收

## Offline Runtime Smoke Demo

该 Demo 不调用外部 LLM，也不修改仓库文件，验证真实 Runtime 链：

```text
Workspace Boot
    ↓
Task
    ↓
Compiler
    ↓
ExecutionPlan
    ↓
ExecutorFactory
    ↓
ExecutionResult
    ↓
ReflectionContext → Reflection → Decision
```

运行：

```bash
python scripts/demo_rc_smoke.py
```

验收标准：

- `demo-read` 成功读取 `agent/runtime.py`；
- `demo-list` 成功列出 `tests/`；
- 人为失败被诊断为 `grounding`；
- Correction Proposal 输出 `re_ground`；
- Decision 输出 `switch`；
- 进程退出码为 `0`。

## Provider-backed Demos

以下 Demo 需要配置 `.env` 中的 LLM Provider，因此不纳入离线 CI：

1. 工程修复：定位 Bug → 修改文件 → 运行测试 → 生成变更摘要。
2. 项目阅读：检索仓库 → 总结结构 → 回答问题 → 生成 patch。
3. Research：搜索 → 阅读 → 摘要。

每个 Provider-backed Demo 在进入 v2.0.0 发布前，应保留输入、执行日志、最终产物、
测试命令和失败恢复记录，作为 RC 验收附件，而不是只记录最终回答。
