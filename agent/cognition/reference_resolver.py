"""ReferenceResolver — 上下文引用消歧引擎。

职责：
1. 在 IntentEngine 之前运行，专门处理代词、省略句、跨轮引用
2. 两级策略：确定性规则（无 LLM）→ LLM 1-shot（复杂场景）
3. 输出 ResolvedQuery，供 IntentEngine 消费

设计原则：
- 不调用任何 Service（WorkspaceService、MemoryService 等）
- 只消费 CognitiveContext（纯数据）
- 不修改任何状态，只做查询和输出
"""
import re
from typing import Optional

from .cognitive_context import CognitiveContext, ResolvedQuery, ConversationState, ResolutionCandidate

# ── 确定性消歧规则 ──

# 纯代词模式：输入只包含代词+操作词
_PRONOUN_PATTERNS = [
    re.compile(r'^(这里|那里|它|这个|那个|这些|那些|这|那)\s*(.*)$'),
    re.compile(r'^(解释|说明|分析|看看|查看|打开|修改|改|优化|重构|删除|移动|复制|运行|执行)\s*(这里|那里|它|这个|那个|这|那)$'),
]

# 省略目标操作模式：动词 + 程度词，无目标
_OMITTED_TARGET_PATTERNS = [
    re.compile(r'^(修改|改|优化|重构|调整|更新|编辑|完善|改进)(一下|一哈|一点)?$'),
    re.compile(r'^(看看|查看|打开|读|读取|显示)(一下|一哈)?$'),
    re.compile(r'^(解释|说明|分析|总结|审查|review)(一下|一哈)?$'),
    re.compile(r'^(删除|移除|清理|去掉)(一下|一哈)?$'),
    re.compile(r'^(运行|执行|测试|跑)(一下|一哈)?$'),
    re.compile(r'^(继续|下一步|接着|往下)$'),
    re.compile(r'^(重新|再试|再来|再来一次)$'),
]

# 符号引用模式：指向代码中的符号
_SYMBOL_REFERENCE_PATTERNS = [
    re.compile(r'^(这个|那个|上面的|下面的|这个)\s*(函数|方法|类|变量|常量|接口|模块|文件)?$'),
    re.compile(r'^(解释|说明|分析|看看|查看)\s*(这个|那个|上面的|下面的)\s*(函数|方法|类|变量|常量|接口|模块|文件)?$'),
    re.compile(r'^(函数|方法|类|变量|常量|接口|模块|文件)\s*(的|中|里)\s*(这个|那个|这个)'),
]

# 省略目标操作中的动词（用于区分主题延续，如"上海呢" vs "改呢"）
_OMITTED_TARGET_VERBS = {
    "修改", "改", "优化", "重构", "调整", "更新", "编辑", "完善", "改进",
    "看看", "查看", "打开", "读", "读取", "显示",
    "解释", "说明", "分析", "总结", "审查", "review",
    "删除", "移除", "清理", "去掉", "运行", "执行", "测试", "跑",
    "继续", "下一步", "接着", "往下", "重新", "再试", "再来", "再来一次",
}

# 跨轮续操作模式：基于上一轮的目标继续操作
_CONTINUATION_PATTERNS = [
    re.compile(r'^那\s*(修改|改|优化|重构|调整|更新|编辑|完善|改进)(一下|一哈)?$'),
    re.compile(r'^那就\s*(修改|改|优化|重构|调整|更新|编辑|完善|改进)(一下|一哈)?$'),
    re.compile(r'^(也|同样)\s*(修改|改|优化|重构|调整|更新|编辑|完善|改进)(一下|一哈)?$'),
]

# 中文数字序数模式
_ORDINAL_PATTERNS = [
    re.compile(r'^(第[一二三四五六七八九十\d])个'),
    re.compile(r'^(第[一二三四五六七八九十\d])步'),
    re.compile(r'^(第[一二三四五六七八九十\d])条'),
    re.compile(r'^上面(的|那)?'),
    re.compile(r'^下面(的|那)?'),
]


def _is_pronoun_only(text: str) -> bool:
    """检查是否纯代词引用（"这里"、"它"、"这个"等）。"""
    for pattern in _PRONOUN_PATTERNS:
        if pattern.match(text):
            return True
    return False


def _is_omitted_target(text: str) -> bool:
    """检查是否省略了目标对象的操作句（"修改一下"、"优化"等）。"""
    for pattern in _OMITTED_TARGET_PATTERNS:
        if pattern.match(text):
            return True
    return False


def _is_symbol_reference(text: str) -> bool:
    """检查是否指向符号的引用（"这个函数"、"上面的方法"等）。"""
    for pattern in _SYMBOL_REFERENCE_PATTERNS:
        if pattern.match(text):
            return True
    return False


def _is_continuation(text: str) -> bool:
    """检查是否跨轮续操作（"那改一下"、"也优化一下"等）。"""
    for pattern in _CONTINUATION_PATTERNS:
        if pattern.match(text):
            return True
    return False


def _is_ordinal_reference(text: str) -> bool:
    """检查是否序数引用（"第一个"、"第二个"、"上面的"等）。"""
    for pattern in _ORDINAL_PATTERNS:
        if pattern.match(text):
            return True
    return False


def _extract_action_from_omitted(text: str) -> str:
    """从省略句中提取动作词。

    例如:
    "修改一下" → "modify"
    "看看" → "read"
    "解释一下" → "explain"
    """
    action_map = {
        "修改": "modify", "改": "modify", "优化": "optimize",
        "重构": "refactor", "调整": "modify", "更新": "modify",
        "编辑": "modify", "完善": "modify", "改进": "modify",
        "看看": "read", "查看": "read", "打开": "read",
        "读": "read", "读取": "read", "显示": "read",
        "解释": "explain", "说明": "explain", "分析": "analyze",
        "总结": "summarize", "审查": "review", "review": "review",
        "删除": "delete", "移除": "delete", "清理": "clean",
        "去掉": "delete",
        "运行": "execute", "执行": "execute", "测试": "test",
        "跑": "execute",
        "继续": "continue", "下一步": "continue", "接着": "continue",
        "往下": "continue",
        "重新": "retry", "再试": "retry", "再来": "retry",
        "再来一次": "retry",
    }

    for keyword, action in action_map.items():
        if keyword in text:
            return action
    return ""


def _needs_complex_llm_resolve(text: str, context: CognitiveContext) -> bool:
    """检查是否需要 LLM 级复杂消歧。

    条件：
    1. 有上下文信息可参考
    2. 不是简单的代词/省略句（那些已被确定性规则覆盖）
    3. 包含模糊引用，需要理解对话语义才能消歧
    """
    # 如果没有任何上下文信息，不需要 LLM
    if not context.conversation and not context.workspace:
        return False

    # 检查是否包含跨句引用指示词
    complex_indicators = [
        "为什么", "怎么", "如何", "能否", "可以",
        "接着", "然后", "之后",
        "像", "类似", "同样",
        "不对", "错了", "不对吧",
    ]
    for indicator in complex_indicators:
        if indicator in text:
            return True

    return False


class ReferenceResolver:
    """上下文引用消歧引擎。

    两级策略：
    Stage 1: 确定性规则匹配（代词、省略句、续操作等）
    Stage 2: LLM 1-shot 消歧（仅复杂引用场景）

    所有规则都不修改任何外部状态。
    """

    def __init__(self):
        self._llm_calls = 0

    @property
    def llm_call_count(self) -> int:
        return self._llm_calls

    def resolve(self, user_input: str, context: CognitiveContext) -> ResolvedQuery:
        """消歧主入口。

        Args:
            user_input: 用户原始输入
            context: 当前认知上下文

        Returns:
            消歧后的 ResolvedQuery
        """
        text = user_input.strip()

        # ── Stage 1: 确定性规则消歧 ──
        # 注意：规则按优先级从高到低排列
        # 更具体的模式（如续操作）先于更宽泛的模式（如代词）

        # 规则 1: 跨轮续操作 → 使用 last_target（优先级最高，避免"那改一下"被代词规则吞掉）
        if _is_continuation(text):
            return self._resolve_continuation(text, context)

        # 规则 2: 主题延续（"上海呢" / "那广州呢" / "北京怎么样"）
        # → 新 target + 继承上一轮 domain/action（v1.2A）
        # 注意：必须早于代词规则（"那广州呢" 否则会被"那"代词模式吞掉）
        if self._is_topic_continuation(text, context):
            return self._resolve_topic_continuation(text, context)

        # 规则 2.5: 符号引用 + 语气词（"那个函数呢" → last_symbol，v1.2A）
        if re.match(r'^(那个|这个|上面|下面)\s*(函数|方法|类|变量|接口|模块|文件)?\s*(呢|呢？|呢吧)?$', text):
            return self.resolve_symbol(text, context).to_resolved_query(text)

        # 规则 3: 符号引用 → 使用 last_symbol
        if _is_symbol_reference(text):
            return self._resolve_symbol_ref(text, context)

        # 规则 4: 省略目标操作 → 使用 current_file
        if _is_omitted_target(text):
            return self._resolve_omitted_target(text, context)

        # 规则 5: 纯代词引用 → 使用 last_file / last_symbol（最后检查，避免误吞续操作/省略句）
        if _is_pronoun_only(text):
            return self._resolve_pronoun(text, context)

        # 规则 6: 序数引用 → TODO: 解析上一轮列表
        if _is_ordinal_reference(text):
            return self._resolve_ordinal(text, context)

        # ── Stage 2: LLM 复杂消歧 ──
        if _needs_complex_llm_resolve(text, context):
            return self._llm_resolve(text, context)

        # 默认：无消歧
        return ResolvedQuery(
            target="",
            symbol="",
            raw=user_input,
            confidence=1.0,
            resolution_trace="无需消歧",
        )

    def _is_topic_continuation(self, text: str, context: CognitiveContext) -> bool:
        """检测主题延续（"上海呢"/"那广州呢"/"北京怎么样"）。

        X 不能是纯操作动词（避免吞掉省略句），且需要上一轮 domain 才能延续。
        """
        if not (context.conversation_state and context.conversation_state.last_domain):
            return False
        m = re.match(r'^(那|那么)?\s*([\w\u4e00-\u9fff]+?)\s*(呢|怎么样|如何|现在怎么样)\s*$', text)
        if not m:
            return False
        x = m.group(2)
        # "那个函数呢" 这类是指代+名词 → 符号引用（非主题延续）
        if re.match(r'^(个|这个|那个|上面|下面|这里的|上面的|下面的)', x):
            return False
        return x not in _OMITTED_TARGET_VERBS

    def _resolve_topic_continuation(self, text: str, context: CognitiveContext) -> ResolvedQuery:
        """解析主题延续：新 target + 继承上一轮 domain/action。"""
        conv = context.conversation_state
        m = re.match(r'^(那|那么)?\s*([\w\u4e00-\u9fff]+?)\s*(呢|怎么样|如何|现在怎么样)\s*$', text)
        target = m.group(2).strip() if m else text.strip()
        return ResolvedQuery(
            target=target,
            raw=text,
            entities=[conv.last_action] if conv and conv.last_action else [],
            confidence=0.8,
            resolution_trace=(
                f"主题延续: target={target}（继承 {conv.last_domain}/{conv.last_action}）"
            ),
        )

    def _resolve_pronoun(self, text: str, context: CognitiveContext) -> ResolvedQuery:
        """消歧纯代词引用。

        优先级: last_symbol > last_file > current_symbol > current_file
        """
        target = ""
        symbol = ""
        trace_parts = []

        # 优先使用 last_symbol（最精确）
        if context.last_symbol:
            symbol = context.last_symbol
            target = context.last_file or context.current_file or ""
            trace_parts.append(f"last_symbol={symbol}")
        elif context.last_file:
            target = context.last_file
            trace_parts.append(f"last_file={target}")
        elif context.current_symbol:
            symbol = context.current_symbol
            target = context.current_file or ""
            trace_parts.append(f"current_symbol={symbol}")
        elif context.current_file:
            target = context.current_file
            trace_parts.append(f"current_file={target}")
        else:
            trace_parts.append("无可用上下文")

        return ResolvedQuery(
            target=target,
            symbol=symbol,
            raw=text,
            confidence=0.9 if target or symbol else 0.0,
            resolution_trace=f"代词消歧: {' | '.join(trace_parts)}",
        )

    def _resolve_symbol_ref(self, text: str, context: CognitiveContext) -> ResolvedQuery:
        """消歧符号引用（"这个函数"、"上面的方法"等）。"""
        target = context.last_file or context.current_file or ""
        symbol = context.last_symbol or context.current_symbol or ""

        trace_parts = []
        if symbol:
            trace_parts.append(f"last_symbol={symbol}")
        if target:
            trace_parts.append(f"target={target}")

        return ResolvedQuery(
            target=target,
            symbol=symbol,
            raw=text,
            confidence=0.85 if symbol else 0.5,
            resolution_trace=f"符号引用消歧: {' | '.join(trace_parts) if trace_parts else '未命中'}",
        )

    def _resolve_omitted_target(self, text: str, context: CognitiveContext) -> ResolvedQuery:
        """消歧省略目标的操作句（"修改一下" → target=current_file）。"""
        action = _extract_action_from_omitted(text)
        target = ""

        # 优先使用 current_file
        if context.current_file:
            target = context.current_file
        elif context.last_file:
            target = context.last_file
        elif context.last_target:
            target = context.last_target
        elif context.current_symbol:
            target = context.current_symbol

        trace = f"省略目标消歧: action={action}"
        if target:
            trace += f", target={target}"

        return ResolvedQuery(
            target=target,
            symbol=context.current_symbol or context.last_symbol or "",
            raw=text,
            entities=[action] if action else [],
            confidence=0.9 if target else 0.3,
            resolution_trace=trace,
        )

    def _resolve_continuation(self, text: str, context: CognitiveContext) -> ResolvedQuery:
        """消歧跨轮续操作（"那改一下" → target=last_target）。"""
        action = _extract_action_from_omitted(text)
        target = context.last_target or context.last_file or context.current_file or ""

        trace = f"续操作消歧: action={action}"
        if target:
            trace += f", target={target}"

        return ResolvedQuery(
            target=target,
            symbol=context.last_symbol or "",
            raw=text,
            entities=[action] if action else [],
            confidence=0.85 if target else 0.3,
            resolution_trace=trace,
        )

    def _resolve_ordinal(self, text: str, context: CognitiveContext) -> ResolvedQuery:
        """消歧序数引用（"第一个"、"上面的"等）。"""
        # TODO: 实现序数消歧（需要跟踪上一轮输出的列表）
        return ResolvedQuery(
            target="",
            raw=text,
            confidence=0.3,
            resolution_trace="序数消歧: 暂未实现（TODO）",
        )

    # ── Resolution Pipeline（ADR-0008）：统一 Candidate + merge（纯函数）──

    def resolve_symbol(self, text: str, context: CognitiveContext) -> ResolutionCandidate:
        """符号引用消歧（"那个函数呢" → last_symbol）。"""
        conv = context.conversation_state
        symbol = conv.last_symbol if conv else ""
        return ResolutionCandidate(
            kind="symbol",
            target=symbol or None,
            confidence=0.85 if symbol else 0.0,
            reason=f"符号引用: last_symbol={symbol}" if symbol else "符号引用: 无可用符号",
            source="symbol",
        )

    def resolve_unknown(self, text: str, context: CognitiveContext) -> ResolutionCandidate:
        """上下文不足 → unknown（Unknown 优于误判，ADR-0008）。"""
        return ResolutionCandidate(
            kind="unknown",
            confidence=0.1,
            reason="无可用上下文，返回 unknown（由 Runtime 引导用户补充）",
            source="unknown",
        )

    def resolve_candidates(self, text: str, context: CognitiveContext) -> list:
        """收集所有子 Resolver 的候选（Pipeline 输入）。"""
        conv = context.conversation_state
        candidates = []
        # topic / symbol / reference / unknown 各产生候选
        if conv and conv.last_domain:
            m = re.match(r'^(那|那么)?\s*([\w\u4e00-\u9fff]+?)\s*(呢|怎么样|如何|现在怎么样)\s*$', text)
            if m and not re.match(r'^(个|这个|那个|上面|下面)', m.group(2)):
                candidates.append(ResolutionCandidate(
                    kind="topic", target=m.group(2), confidence=0.8,
                    reason=f"主题延续: {m.group(2)}", source="topic",
                ))
        if re.match(r'^(那个|这个|上面|下面)\s*(函数|方法|类|变量|接口|模块|文件)?\s*(呢|呢？|呢吧)?$', text):
            candidates.append(self.resolve_symbol(text, context))
        if not candidates:
            candidates.append(self.resolve_unknown(text, context))
        return candidates

    @staticmethod
    def merge_candidates(candidates: list) -> ResolutionCandidate:
        """merge（纯函数，无副作用）：按 confidence 择优。"""
        if not candidates:
            return ResolutionCandidate(kind="unknown", confidence=0.1, reason="无候选")
        return max(candidates, key=lambda c: c.confidence)

    def _llm_resolve(self, text: str, context: CognitiveContext) -> ResolvedQuery:
        """LLM 1-shot 复杂引用消歧。"""
        try:
            from agent.llm import llm
            from langchain_core.messages import SystemMessage, HumanMessage

            prompt = self._build_llm_prompt(text, context)
            response = llm.invoke([SystemMessage(content=prompt)])
            content = response.content.strip() if hasattr(response, 'content') else str(response).strip()

            # 解析结果
            target = ""
            symbol = ""
            entities = []

            for line in content.split("\n"):
                line = line.strip()
                if line.startswith("target:"):
                    target = line[len("target:"):].strip()
                elif line.startswith("symbol:"):
                    symbol = line[len("symbol:"):].strip()
                elif line.startswith("entity:"):
                    entity = line[len("entity:"):].strip()
                    if entity:
                        entities.append(entity)

            self._llm_calls += 1

            return ResolvedQuery(
                target=target,
                symbol=symbol,
                entities=entities,
                raw=text,
                confidence=0.7 if target or symbol else 0.0,
                resolution_trace=f"LLM 消歧: target={target}, symbol={symbol}",
            )

        except Exception as e:
            return ResolvedQuery(
                target="",
                raw=text,
                confidence=0.0,
                resolution_trace=f"LLM 消歧失败: {e}",
            )

    def _build_llm_prompt(self, text: str, context: CognitiveContext) -> str:
        """构建 LLM 消歧 Prompt。"""
        # 构建上下文摘要
        ctx_lines = []
        if context.current_file:
            ctx_lines.append(f"当前文件: {context.current_file}")
        if context.current_symbol:
            ctx_lines.append(f"当前符号: {context.current_symbol}")
        if context.last_file:
            ctx_lines.append(f"上一轮文件: {context.last_file}")
        if context.last_symbol:
            ctx_lines.append(f"上一轮符号: {context.last_symbol}")
        if context.last_target:
            ctx_lines.append(f"上一轮目标: {context.last_target}")

        # 最近对话
        conv_lines = []
        for msg in context.conversation[-4:]:
            role = msg.get("role", "?")
            content = msg.get("content", "")[:100]
            conv_lines.append(f"{role}: {content}")

        return f"""你是一个引用消歧引擎。分析用户输入，结合上下文信息，输出消歧结果。

用户输入: {text}

## 上下文信息
{chr(10).join(ctx_lines) if ctx_lines else "（无）"}

## 最近对话
{chr(10).join(conv_lines) if conv_lines else "（无）"}

请输出以下信息（每行一个）：
- target: 用户所指的目标文件/对象（如果有）
- symbol: 用户所指的代码符号名（如果有）
- entity: 其他实体（每行一个，可多行）

如果用户输入没有引用任何上下文，所有字段留空。"""