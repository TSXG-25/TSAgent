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


---

## v1.2B 落实（2026-08）

### State = Cache（B1）

- `ConversationState` 唯一字段 `timeline: ResolutionTimeline`（固定窗口 15 轮）。
- `ResolutionTimeline` 是**能力缓存**：`push / latest / history / iter_reverse`，无 kind 语义（Timeline = Storage）。
- 语义查询（`latest_symbol` / `nth(kind, n)`）由 **Resolver** 实现（Resolver = Semantics）。
- 所有 Resolver（Reference / Repository / Memory）共享同一 Timeline。
- `last_*` 字段已删除（断写 → 断读 → 删字段 三阶段迁移完成）。

### Pipeline 主路径化（B2，清 Implementation Debt）

```
resolve() → resolve_candidates()（子 Resolver 收集）→ merge_candidates()（纯函数择优）→ ResolutionResult
```

### ResolutionResult 极简（B1）

只回答"引用最终解析成了什么"：`kind / target / symbol / confidence / trace / raw`。
`domain / action` 属于 Intent，不进入 Resolution（Intent 决定"要做什么"，Resolver 决定"指的是谁"）。

### Resolver Determinism（B3）

```
ResolutionResult.to_json() → Result Hash（kind/target/symbol/confidence）+ Trace Hash（trace）
同输入 + 同上下文连续 100 次 → 双 Hash 全一致 → PASS
```

Resolver 是确定性的（无 LLM 随机性），不同于 LLM 层。

### Ordinal / Repository Runtime（B5 / B6）

- `RepositoryIndexer.symbols_in_file(path)`：按文件有序符号列表。
- Repository 数据经 `CognitiveContext.repository_symbols` 注入（Resolver 保持纯函数，不调 Service）。
- **Capability Reuse Ratio**：Repository Runtime 新增 Resolver / Candidate / Merge / Result / Timeline = 0。
  同一 Resolver 处理 Repository 场景（10/10 = 100%）。

### Resolver Contract 冻结（v1.2B 完成后）

`ResolutionCandidate / ResolutionResult / ResolutionTimeline` 三者**不再改动接口**。
以后只能：新增 resolver / 新增 dataset / 新增 metric。
这是 v1.1 Runtime Contract 的自然延伸（v1.1 冻结执行层接口，v1.2B 冻结解析层接口）。

---

## v1.2C 落实（2026-08）—— Capability Expansion

### Contract Verification（可执行化冻结）

四部分验证（`evaluation/benchmark/contract_verification.py` + 基线）：

```
✓ public fields          （字段名 + 类型）
✓ public methods         （Timeline / Result 方法签名）
✓ function signature     （merge_candidates / resolve_candidates / resolve）
✓ serialization schema   （ResolutionResult.to_json() 的固定 key 集 + 类型）
```

比字段 hash 更稳（改类型 / 改 schema 全部 FAIL）。eval 入口自动校验。

### Memory Resolution（跨会话，Facts 层）

- Memory 保存**事实**（`ResolutionMemory: timestamp / utterance / resolved_target / kind / metadata`），
  不保存 ResolutionResult（不依赖 Runtime 内部对象）。
- `resolve_memory()` 负责 `ResolutionMemory → ResolutionCandidate`（Converter 在 Resolver 层）。
- "昨天那个文件 / 上次那个函数" → memory facts（confidence 0.6，低于当前会话 → merge 时当前会话优先）。

### Capability Hint（Tool 选择归 Planner）

- `resolve_capability()` 返回 `kind="capability"`、`target="calculation"/"web_search"/...`。
- **不绑定具体工具**（否则工具一换 Dataset 全坏）；Tool 选择是 Planner 的职责。
- 独立于引用解析链（resolve_candidates 不收集），供 Planner/Intent 消费。

### Capability Reuse Score（长期 KPI）

```
Conversation   Reuse 100%  Accuracy 42/42
Repository     Reuse 100%  Accuracy 10/10
Memory         Reuse 100%  Accuracy 3/3
Capability     Reuse 100%  Accuracy 7/7
```

规则：某 Capability 需要新 Candidate / Result / Timeline / Merge → 该 Capability Reuse 降级 → Contract 失效信号。

Extension Cost（演化信号，非质量指标）：新增 Resolver 方法数 / Dataset 数 / LOC。

### 关键设计决定

- **target 只信确定性来源**（`raw_target > Resolver 消歧 > current_file`），LLM 不参与 target 决定
  —— 这是 Determinism 的根因修复（曾暴露 100 次 1 次抖动）。
- Memory 是**数据源注入**（`CognitiveContext.memory_resolutions`），Resolver 仍纯函数。


