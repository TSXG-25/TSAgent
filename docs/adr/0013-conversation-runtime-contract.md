# ADR-0013: Conversation Runtime Contract（v2.1B-1 设计冻结）

- 状态: Accepted（v2.1B-3 Continuation Contract 冻结）
- 日期: 2026-08
- 关联: ADR-0001（核心模型）、ADR-0009（确定性验证）、ADR-0012（Execution Runtime Contract）

---

## ADR-0013-A：一条原则（先于一切）

> **Conversation Runtime stores conversation state, not knowledge.**

Conversation Runtime 代表的是**当前交互状态**（recent_goal / last_instruction / last_answer / turn_count），
不是长期记忆。它不存知识、不做摘要、不检索语义。
因此它不属于 Memory 层——是 **Runtime 的会话状态机**。

---

## 背景（数据驱动）

Memory Fuzz v0.1（130 用例 / 96 分钟）结论：

| Capability | Recall | 判断 |
|---|---|---|
| Fact Memory | 90-100% | ✅ 已稳定，不再投入 |
| Conversation Memory | **0%** | 🔴 结构性缺失 |
| Temporal Reference | 0-40% | 🔴 与 Conversation 同根 |
| Interference | 87% | ✅ 事实库+确定性注入有效 |

根因（运行日志确认）：
1. "刚才让我做什么" 被路由到 planner → LLM explain 任务，**该路径 prompt 不含会话状态**。
2. Direct-chat 路径有 session 注入，但 LLM 未优先使用最近目标/上条答案。

结论：缺失的不是算法，而是**把 Conversation State 放到正确的位置**。

---

## 分层（明确边界）

```
Runtime
│
├── Fact Memory         用户事实 / Preference     （已有，100%，归 Memory 层）
├── Conversation Runtime  ← 新增（本 ADR，归 Runtime 层）
│      recent_goal / last_instruction / last_answer / turn_count
├── Working Memory      Planner / Executor 当前状态（由 RuntimeContext 等 Context 投影承载）
└── Long-term Memory    语义/短期/会话持久化      （已有，归 Memory 层）
```

---

## 三个组件（接口冻结）

### 1. ConversationState —— 冻结数据模型（frozen，每用户一份）

```python
# agent/conversation/state.py（不放在 agent/memory/）

@dataclass(frozen=True)
class ConversationState:
    """当前交互状态快照。只读；唯一写入入口是 ConversationTracker。

    明确不包含：active_task（与 Runtime 状态重复，从 RuntimeContext 获取）、
    summary、语义记忆。
    """
    user_id: str
    recent_goal: str = ""          # = 最近一次 NEW_REQUEST 的原始 user_input（不做摘要）
    last_instruction: str = ""     # = 上一条 NEW_REQUEST 的原始 user_input
    last_answer: str = ""          # = 上一条 assistant 回答（temporal/answer 引用来源）
    turn_count: int = 0            # 对话轮数
    updated_at: float = 0.0
```

设计约束：
- frozen，永不原地修改（ADR-0001 Immutable Core Model）。
- `recent_goal = 原始 user request`：Benchmark 期望值是其子串，deterministic / zero-cost / 无 semantic drift。
- 字段冻结；后续扩展走新 ADR，不临时加字段。

### 2. ConversationTracker —— 唯一写入入口

```python
class ConversationTracker:
    """每轮 answer 生成后调用 update()。确定性，无 LLM，不解析中文。"""

    def update(
        self,
        *,
        user_id: str,
        user_input: str,
        assistant_answer: str,
        intent: "IntentResult | None" = None,
        runtime_pending: "bool | None" = None,
    ) -> ConversationState:
        """规则（冻结）：
        1. intent 经 classify_conversation_intent() → ConversationIntent。
        2. NEW_REQUEST → last_instruction = user_input；recent_goal = user_input。
           REFERENCE / CONTINUE_* → 不覆盖 recent_goal / last_instruction。
        3. last_answer = assistant_answer（所有轮都更新）。
        4. turn_count += 1。
        """
```

### ConversationIntent —— 会话轮次类型（替代 regex）

```python
class ConversationIntent(Enum):
    NEW_REQUEST         # 新指令 / 新问题 / 新事实
    REFERENCE           # 引用上轮内容（"刚才/上一条/上一题/刚才的答案"）
    CONTINUE_PLAN       # 恢复 Runtime 中未完成的计划
    CONTINUE_CHAT       # 延续上一条回答/解释
    CONTINUE_REFERENCE  # 延续省略目标的引用
```

派生规则（**基于已有 Intent Engine 的结构化输出，不做中文 regex 扩展**）：

```python
def classify_conversation_intent(
    intent: "IntentResult | None", user_input: str,
    *, runtime_pending: bool = False,
) -> ConversationIntent:
    # CONTINUE_*：详见本 ADR 的 Continuation Contract。
    # 裸“继续”必须结合 Runtime pending 信号；benchmark 不固定解释它。
    # 显式计划/聊天/引用短语先分别归类为 CONTINUE_PLAN /
    # CONTINUE_CHAT / CONTINUE_REFERENCE。
    # REFERENCE：Intent 已判定为记忆回问/指代
    if intent is not None and intent.domain == "memory" and \
       intent.action in _REFERENCE_ACTIONS:          # query/recall/query_history/...
        return REFERENCE
    return NEW_REQUEST
```

- 中文语义由 Intent Engine 负责（那是它的职责）；Conversation Runtime 只认
  `NEW_REQUEST / REFERENCE / CONTINUE_PLAN / CONTINUE_CHAT / CONTINUE_REFERENCE`。
- continuation 的短语集合是冻结的 frozenset，不能在 Conversation Runtime 中
  不断增长 regex；裸“继续”无 pending 时是 CONTINUE_CHAT，有 pending 时是
  CONTINUE_PLAN。无歧义的“继续执行未完成的任务”等明确短语直接进入
  CONTINUE_PLAN。

### 3. ConversationRetriever —— 唯一读取入口

```python
class ConversationRetriever:
    """Planner / Decision / Direct Chat 全部通过本接口读会话状态。"""

    def get(self, user_id: str) -> ConversationState:
        """返回冻结快照（无则返回空状态）。"""

    def snapshot(self, user_id: str) -> ConversationSnapshot:
        """返回纯数据快照（recent_goal / last_instruction / last_answer）。

        Retriever 不生成 Prompt；Planner / Decision / Direct Chat 自行调用
        render_snapshot() 拼自己的 prompt（ADR-0013 约束②，避免职责耦合）。
        """

    def current_task(self, user_id: str, runtime_context=None) -> str:
        """（预留）需要当前任务时从 RuntimeContext 获取，不维护第二份状态。"""
```

---

## 注入点（冻结）

| 路径 | 注入方式 |
|---|---|
| **Planner** → LLM explain 任务 | 该任务 prompt 的 system 部分注入 `render_for_prompt()` |
| **Decision** | Decision 输入含 `render_for_prompt()`（"继续" → 恢复最近目标，而非 Ask User） |
| **Direct Chat** 直答 | `system_content` 注入 `render_for_prompt()` |
| ~~Reflection~~ | ❌ 不注入 —— Reflection 永远只消费 FailureEvent（保持独立） |

禁止：planner / decision / executor 直接摸 dict；一律走 Retriever。

---

## 非目标（明确不做，防 Scope Creep）

- ❌ 不做 Summary / Embedding / RAG / LLM 摘要（原始请求即答案来源）
- ❌ 不维护 active_task / current_plan（与 Runtime 状态重复，从 RuntimeContext 拿）
- ❌ 不新增持久化层（进程内 + 现有 short_term 已够；跨进程恢复留 v2.1+）
- ❌ 不改 Fact Memory（已 100%）
- ❌ 不往 AgentState 加字段

---

## Conversation Recall 验收

`memory-fuzz v0.3` 继续保留 130-case 数据集外壳，便于与历史结果对照；其中
`metric_scope=continuation` 的用例不计入 Conversation Recall 趋势。Conversation
核心门槛只看 `recent_goal` 与 `previous_instruction`，不把 Runtime continuation
恢复能力混入记忆召回分数。

| 指标 | v0.1 基线 | v2.1B-1 目标 |
|---|---|---|
| conversation/recent_goal | 0% | **≥ 90%** |
| conversation/previous_instruction | 0% | **≥ 90%** |
| temporal/answer_reference | 0% | **≥ 80%** |
| temporal/action_reference | 30% | **≥ 80%** |
| Overall（仅 `memory_recall` scope） | 53% | **≥ 80%** |
| Fact（回归） | 90% | **不下降** |

任一 conversation 子项 < 90% → 不进入 v2.1C。

Continuation 单独报告以下指标，不参与上述 Recall 门槛：

| 指标 | 验收对象 |
|---|---|
| `Plan Resume Rate` | Runtime plan 恢复、执行推进、真实结果与 Verifier 成功 |
| `Chat Resume Rate` | `last_answer` 被正确延续 |
| `Reference Resolution Rate` | 引用目标正确映射；冲突时进入澄清 |

---

## 三条收敛约束（评审追加，已冻结）

1. **ConversationState 必须 immutable**：迁移 `old_state → update() → new_state`，
   与 FailureEvent 一致，支持 Replay / Checkpoint / Time Travel。
2. **Retriever 不生成 Prompt**：只返回 `ConversationSnapshot`（纯数据），
   Planner / Decision / Chat 自己拼 Prompt；`render_snapshot()` 是消费者主动调用的纯函数。
3. **update() 记录 ConversationEvent**：`NEW_REQUEST / REFERENCE / CONTINUE_PLAN /
   CONTINUE_CHAT / CONTINUE_REFERENCE / ANSWER`
   作为事件入日志（bounded deque），供 Conversation Replay，不解析日志。

---

## v2.1B-2（数据驱动补充，已实现）

Memory Fuzz 显示：把 recent_goal/last_answer 一起塞 Prompt 让 LLM 猜会误选。
两个小能力（不做新 Conversation System）：

1. **Conversation Reference Resolver**
   `REFERENCE → ReferenceType(LAST_GOAL/LAST_INSTRUCTION/LAST_ANSWER/LAST_RUNTIME/UNKNOWN)`
   → Retriever 只取对应字段 → 字段级注入。
   语言理解（reference_kind）由 Intent Engine 判定；Conversation 层纯映射，零 regex。
2. **Runtime Continuation**
   `CONTINUE_PLAN` 从 `RuntimeContext` 的当前 plan/task 恢复执行（`_render_runtime_continuation`），
   不占 Conversation 边界（不维护 active_task）。

   `CONTINUE_CHAT` 只使用 `last_answer` 延续回答；`CONTINUE_REFERENCE` 交给
   Reference Resolver 解析省略目标。Benchmark 只在 ADR-0014 中引用本 ADR，
   不复制运行时语义。

---

## v2.1B-3：Continuation Contract（正式冻结）

Continuation 是 Runtime 的语言行为契约，不是 Benchmark 的局部约定。
Conversation Runtime 只保存对话状态；pending plan/task 仍由 Runtime 持有，
并通过 `runtime_pending` 与当前执行目标投影给解析层。

### 三种延续类型

| Contract | 触发语义 | 行为 | 验收对象 |
|---|---|---|---|
| `CONTINUE_PLAN` | 明确恢复执行，或裸延续词遇到 pending execution | 恢复 `RuntimeContext.current_execution` | 计划恢复、执行推进、真实结果/产物 |
| `CONTINUE_REFERENCE` | 明确引用上一个目标，且 Reference Resolver 能解析目标 | 只注入对应引用字段/目标 | 引用目标映射正确 |
| `CONTINUE_CHAT` | 继续回答、解释或展开上一条内容 | 使用 `last_answer` 延续对话 | 回答内容延续正确 |

`CLARIFY` 不是第四种 ConversationIntent，而是解析冲突时的 Planner/Decision
结果：必须暂停执行并向用户澄清，不能盲目选择计划或引用目标。

### 确定性解析优先级

解析顺序区分“用户明确表达”和“Runtime 状态”：

1. **明确计划恢复表达**（如“继续执行未完成任务”“完成剩余任务”）
   → `CONTINUE_PLAN`。
2. **明确引用表达且目标可解析**（如“继续刚才那个函数”“继续第二个”）
   → `CONTINUE_REFERENCE`。
   若解析出的 reference target 与 pending execution target 冲突
   → `CLARIFY`，不得恢复任一方。
3. **裸延续词 + pending execution**（如“继续”“接着”）
   → `CONTINUE_PLAN`。
4. **无 pending，但 reference 可解析**
   → `CONTINUE_REFERENCE`。
5. 其余延续表达
   → `CONTINUE_CHAT`。

因此：

> **裸延续由 Runtime 状态决定；显式延续由用户表达决定；两者冲突时澄清。**

Conversation Runtime 不负责比较 plan/task 的业务目标。目标比较由
Reference Resolver 与 Runtime Context 的投影完成；ConversationState 不增加
`active_task`、`current_plan` 或第二份目标副本。

### 解析与验收约束

- 延续分类必须在 Planner/LLM 生成前确定，不依赖 LLM 临场猜测。
- `CONTINUE_PLAN` 不能只用回答关键词判定（例如只检查“平方”）；必须验证
  执行是否恢复、是否推进以及产物是否真实存在。
- `CONTINUE_REFERENCE` 必须验证目标映射；冲突或不可解析时必须澄清/失败，
  禁止伪造已完成。
- `CONTINUE_CHAT` 才验证 `last_answer` 的内容延续。
- `ConversationEvent` 仍只记录三种 `CONTINUE_*`；`CLARIFY` 作为解析结果和
  诊断信息记录，不新增第四种 continuation contract。

Continuation Benchmark 不属于 Conversation Recall：
`plan_resume`、`chat_resume`、`reference_resume` 分别验证 Runtime 恢复、
回答延续和引用解析；`recent_goal` / `previous_instruction` 的 Recall 趋势
不得吸收这些 case 的结果。

---

## 实现范围（评审通过后）

- `agent/conversation/__init__.py` + `agent/conversation/state.py`
  （~150 行：ConversationState + ConversationIntent + ConversationTracker + ConversationRetriever）
- `agent/orchestrator/planner.py` 两处注入（LLM explain 任务 + Direct Chat）
- Decision 输入注入 `render_for_prompt()`
- `agent/runtime.py` 每轮调用 `tracker.update(...)`
- 回归测试 `tests/test_conversation_runtime.py`（纯函数，无 LLM）
- 重跑 130-case Memory Fuzz 验证
