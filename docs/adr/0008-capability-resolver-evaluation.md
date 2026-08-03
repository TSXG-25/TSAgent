# ADR-0008: Capability = Resolver + Evaluation

- 状态: Accepted
- 日期: 2026-08
- 关联: ADR-0001..0007

---

## 背景

v1.0 建立架构，v1.1 建立工程体系。v1.2 进入**能力层**。
本 ADR 冻结能力开发的统一模式，防止能力层重蹈"if/elif 堆积"与"God Object"覆辙。

## 决策

### 原则：Capability = Resolver + Evaluation

每个新能力遵循固定四件套：

```
Capability
  ├── Resolver    （实现）
  ├── Dataset     （定义）
  ├── Metric      （衡量）
  └── Regression  （防回退）
```

实例：
- Grounding → Grounding Recall → Benchmark
- Presentation → Conversation E2E
- Reference Resolution → Context Resolution Accuracy

### Resolution Pipeline Contract

所有 Resolver（Reference / Repository / Memory）统一模型：

```python
@dataclass
class ResolutionCandidate:
    kind: Literal["topic", "symbol", "file", "ordinal", "reference", "unknown"]
    target: Optional[str]
    confidence: float
    reason: str
    source: str
```

Pipeline（纯函数，无副作用）：

```
Input → [子 Resolver] → Candidates → merge() → ResolutionResult
```

- `resolve()` 与各子 Resolver 不修改任何状态（ConversationState 由 Runtime 更新）。
- Repository / Memory 未来复用同一接口——Resolver 不知道"当前在聊天还是改代码"。

### Unknown 优于误判

上下文不足时返回 `unknown`，由 Runtime 引导用户补充信息。
禁止为了追求 Accuracy 而硬猜目标。

## Evidence

### Trigger
Context Resolution 首测：42% 基线暴露"那 X 呢"与代词规则冲突、第一轮 target 未提取。
### Observation
Resolver 规则快速堆积（if/elif），有 God Object 风险。
### Decision
内部 Pipeline 抽象 + 统一 ResolutionCandidate + 纯函数 merge + Unknown 语义。
### Validation
Context Resolution Accuracy 42% → 83%；规则重组为子 Resolver 后行为不变。
