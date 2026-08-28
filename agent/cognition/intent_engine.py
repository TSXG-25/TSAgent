"""IntentEngine — Agent 认知层意图理解引擎。

职责：
1. 接收 CognitiveContext（已包含 ReferenceResolver 消歧结果）
2. 结构化意图理解 → 输出 IntentResult
3. 判断是否需要 Agent 执行（还是直接 LLM 回答）

上下文增强：
- CognitiveContext 包含 conversation、workspace、resolved_query 等信息
- LLM Prompt 会注入当前文件、上一轮目标、消歧结果等

与 Cognitive Layer 其他模块的关系：
- ReferenceResolver 先消歧（代词/省略/跨轮引用）
- IntentEngine 再理解意图（domain/action/target/entities）
- WorkflowRouter 接收完整 IntentResult 做路由

设计原则：
- 不 import WorkspaceService / MemoryService / 任何 Service
- 只消费 CognitiveContext（纯数据）
- analyze() 是纯函数（给定相同 context 输出相同 intent）
"""
import json
import re
from typing import Optional

from agent.llm import llm as default_llm
from agent.execution_errors import classify_execution_error
from langchain_core.messages import SystemMessage, HumanMessage

from .cognitive_context import CognitiveContext, ResolvedQuery
from .execution_need import (
    RequestedOutcome,
    analyze_execution_need,
    analyze_requested_outcomes,
    extract_explicit_command,
)
from .research_policy import (
    is_fresh_research_request,
    is_source_grounded_request,
)
from .intent_schema import (
    IntentResult,
    DOMAIN_CHAT, DOMAIN_KNOWLEDGE, DOMAIN_CREATION,
    DOMAIN_DEVELOPMENT, DOMAIN_OPERATION, DOMAIN_MEMORY,
    DOMAIN_FILE, DOMAIN_OFFICE, DOMAIN_MATH, DOMAIN_TRANSLATION,
    DOMAIN_SCHEDULING, DOMAIN_WEB, DOMAIN_UNKNOWN,
    ALL_DOMAINS, DOMAIN_DESCRIPTIONS,
)

# ── 已知项目目录前缀（帮助 target 提取） ──
_KNOWN_PREFIXES = [
    "agent/", "workflows/", "skills/", "tools/", "tests/",
    "input/", "output/", "src/", "docs/", "config/",
]

# ── 关键词 → domain 快速映射 ──
_KEYWORD_MAP: list[tuple[re.Pattern, str, str, bool]] = [
    # (regex, domain, action, requires_execution)

    # Chat / 无意义
    (re.compile(r'^(你好|hi|hello|嗨|嘿|在吗|干啥|无聊|哈哈|呵呵|摸摸)'), DOMAIN_CHAT, "greeting", False),
    (re.compile(r'^(再见|拜拜|晚安|bye)'), DOMAIN_CHAT, "farewell", False),
    (re.compile(r'^(谢谢|感谢|谢啦|多谢|感激|thx|thanks)'), DOMAIN_CHAT, "thanks", False),
    (re.compile(r'^(你是谁|你是干嘛的|你叫什么|介绍一下你|你是谁呀)'), DOMAIN_CHAT, "identity", False),
    (re.compile(r'^(介绍一下自己|自我介绍|介绍下你自己)'), DOMAIN_CHAT, "identity", False),
    (re.compile(r'^(我喜欢你|我爱你|喜欢你|我好喜欢你|好喜欢你)'), DOMAIN_CHAT, "affection", False),
    (re.compile(r'^(你认识|认识我|你觉得我|你喜欢|你觉得)'), DOMAIN_CHAT, "social", False),

    # 天气
    (re.compile(r'天气|气温|温度|下雨|下雪|台风|雾霾|空气|湿度'), DOMAIN_KNOWLEDGE, "weather", True),

    # 新闻
    (re.compile(r'新闻|最新|头条|发生了什么|大事'), DOMAIN_KNOWLEDGE, "news", True),

    # 时间日期
    (re.compile(r'现在.*时间|几点了|今天.*几号|星期几|今天是'), DOMAIN_KNOWLEDGE, "time", False),

    # 翻译
    (re.compile(r'翻译|译成|用.*怎么说|英文.*意思|中文.*意思'), DOMAIN_TRANSLATION, "translate", False),

    # Conversation Runtime continuation contract（ADR-0013）。
    # 裸“继续”留给 Planner 根据 runtime_pending 分流；这些显式短语可在认知层
    # 确定地表达行为，不依赖一次 LLM 分类。
    (re.compile(r'^(继续执行(?:任务|未完成的任务|未完成任务)?|继续任务|继续做|继续处理|继续完成|完成剩余任务|恢复任务|接着做|接着执行)$'),
     DOMAIN_DEVELOPMENT, "continue_plan", True),
    (re.compile(r'^(继续讲|继续解释|继续回答|接着说|展开说|再详细一点|详细一点)$'),
     DOMAIN_CHAT, "continue_chat", False),
    (re.compile(r'^(那个呢|这个呢|上一个呢|前面那个|继续那个|继续这个)$'),
     DOMAIN_MEMORY, "reference", False),

    # 代码生成（先于数学"函数"，避免"写一个判断素数的函数"被判成 math）
    (re.compile(r'写.{0,14}[代码程序函数脚本类]|写.{0,12}(排序|快排|查找|遍历|递归|接口|模块)'), DOMAIN_DEVELOPMENT, "code", True),

    # 数学（简单计算走 LLM 直答；复杂计算/脚本才用工具）
    (re.compile(r'计算|算一下|算算|等于|方程|求导|积分|函数|公式|解.*方程'), DOMAIN_MATH, "calculate", False),
    (re.compile(r'^\d+[+\-*/]\d+|^\d+\.\d+'), DOMAIN_MATH, "calculate", False),

    # 创作（精确短语，避免"大小写/拼写"的"写"作为语素时误判为创作）
    (re.compile(r'写[一首篇个段]{0,2}(?:诗|故事|小说|剧本|文案)|作诗|赋诗|创作|生成.*(?:诗|故事|文案)'), DOMAIN_CREATION, "generate", False),

    # 记忆查询必须早于“编程/开发”关键词，否则“我最喜欢什么编程语言”
    # 会被误判为代码开发并进入执行链。
    (re.compile(r'我.*(?:住|居住|来自|所在).*(?:城市|哪里|哪儿|地点)?|'
                r'我的.*(?:编程语言|语言|城市|所在地|职业|编辑器|框架|项目)|'
                r'我.*(?:喜欢|最喜欢).*(?:编程语言|语言|框架|编辑器|IDE)'),
     DOMAIN_MEMORY, "query", False),

    # 代码开发
    (re.compile(r'写.*[代码程序函数类]|编程|开发|实现.*功能|写.*[接口模块]|修复.*bug|debug|重构'), DOMAIN_DEVELOPMENT, "code", True),
    (re.compile(r'代码.*[审查审阅查看]|review|代码审查'), DOMAIN_DEVELOPMENT, "review", True),
    # v2.0-B：明确代码修改请求（给 X 增加功能 / 配置解析 / 大小写不敏感）
    # action=modify → 无 workflow 路由（走 Planner，多步修改：读→改→测试→文档），
    # 避免误入 code_generation（该 workflow 只适合"生成新代码文件"）
    (re.compile(r'给.*增加|增加.*[功能解析支持]|大小写|不区分大小写|配置.*解析'), DOMAIN_DEVELOPMENT, "modify", True),

    # 记忆查询
    (re.compile(r'我.*[叫姓名是]|我的.*[兴趣喜好名字]|之前.*[说提问]|记不记得|还记得'), DOMAIN_MEMORY, "query", False),
    (re.compile(r'关于我|我的.*[事实信息]'), DOMAIN_MEMORY, "query", False),

    # 文件操作
    # 仅匹配带明确文件扩展名的写入表达，避免为普通“生成代码/内容”
    # 抢占开发或创作意图。命中后由 Planner 的 literal-write fast path
    # 继续确认目标和内容是否完整。
    (re.compile(r'(?:复制|拷贝|copy).*(?:到|为|成)\s+\S+\.[A-Za-z0-9]+'), DOMAIN_FILE, "copy", True),
    (re.compile(r'(?:移动|move).*(?:到|为|成)\s+\S+\.[A-Za-z0-9]+'), DOMAIN_FILE, "move", True),
    (re.compile(r'(?:删除|移除|delete)\s+\S+\.[A-Za-z0-9]+'), DOMAIN_FILE, "delete", True),
    (re.compile(r'(?:创建|新建|生成|写入|写到|保存到|输出到)\s+\S+\.[A-Za-z0-9]+'), DOMAIN_FILE, "write", True),
    (re.compile(r'读取.*文件|打开.*文件|写入.*文件|创建.*文件|删除.*文件|列出.*目录|浏览.*目录'), DOMAIN_FILE, "operate", True),
    (re.compile(r'保存到|保存为|写入到|写到|追加到|另存为'), DOMAIN_FILE, "write", True),
    (re.compile(r'^读取\s+\S+|^读\s+\S+|^打开\s+\S+|^查看\s+\S+|^阅读\s+\S+'), DOMAIN_FILE, "operate", True),
    (re.compile(r'read.*file|write.*file|list.*dir'), DOMAIN_FILE, "operate", True),

    # Office
    (re.compile(r'word|excel|ppt|文档|表格|幻灯片|办公'), DOMAIN_OFFICE, "document", True),

    # 信息搜索
    (re.compile(r'搜索|查找|查询.*[信息资料]|百度|谷歌|搜一下'), DOMAIN_KNOWLEDGE, "search", True),

    # 日程
    (re.compile(r'提醒|闹钟|待办|日程|计划|安排'), DOMAIN_SCHEDULING, "remind", True),
]

# ── 引用问题字段判定（Intent Engine 职责；Conversation 层只做纯映射）──
_REFERENCE_KIND_PATTERNS = [
    (re.compile(r"答案|回答|结果|多少|等于|算出|得数"), "answer"),
    (re.compile(r"(?:继续|接着).*(?:刚才|上一个|前面|那个|这个|第[一二三四五六七八九十\d]+个)"), "instruction"),
    (re.compile(r"(?:刚才|上一个|前面|那个|这个).*(?:函数|方法|类|文件|任务|指令)?"), "instruction"),
    (re.compile(r"继续|接着|恢复|接着做|继续做"), "runtime"),
    (re.compile(r"做什么|干什么|任务|目标|要求|指令|让我|建议|方案|上一条|之前.*什么"), "instruction"),
]


def _detect_reference_kind(text: str) -> str:
    """判定引用类问题指向的字段。仅用于 reference 语义；非引用输入返回空。"""
    for pattern, kind in _REFERENCE_KIND_PATTERNS:
        if pattern.search(text):
            return kind
    if re.fullmatch(r"(?:那个呢|这个呢|上一个呢|前面那个|继续那个|继续这个)", text.strip()):
        return "instruction"
    return ""


# ── Domain Upgrade（v2.1B-3，ADR-0013 关联）──
# ExecutionNeedAnalysis 判定 World State Change（need=True）时，非执行域
# （chat/math/translation/creation）不得保持原域——否则"写一个判断素数的函数"
# 会被判成 math（函数关键词）而永远进不了执行链/目标判定。
_NON_EXEC_DOMAINS = frozenset({
    DOMAIN_CHAT, DOMAIN_MATH, DOMAIN_TRANSLATION, DOMAIN_CREATION,
})


def _upgrade_domain(domain: str, need: Optional[bool]) -> str:
    """need=True（World State Change）时，非执行域 → development。

    这是 Runtime Compiler 的确定性规则（ADR-0009），不是 regex。
    """
    if need is True and domain in _NON_EXEC_DOMAINS:
        return DOMAIN_DEVELOPMENT
    return domain


# ── LLM 分析 Prompt ──
LLM_INTENT_PROMPT = """你是一个 Agent 意图理解引擎。分析用户输入，结合上下文信息，输出结构化意图。

当前上下文信息：
{context_summary}

用户输入: {input}

输出 JSON:
{{
    "domain": "开发|知识|闲聊|创作|运维|记忆|文件|办公|数学|翻译|日程|网络|未知",
    "action": "简短动作描述（英文，如 modify、read、explain、search、code）",
    "target": "用户指定的目标对象（如果有），如文件路径、类名、函数名",
    "entities": ["目标中提到的其他关键实体"],
    "confidence": 0.95,
    "requires_execution": true/false,
    "summary": "一句话说明用户想做什么"
}}

Domain 说明：
{domain_descriptions}

规则：
1. 闲聊/打招呼/无意义输入 → domain="闲聊", requires_execution=false
2. 天气/新闻/百科问答 → domain="知识"
3. 代码/Bug修复/技术问题 → domain="开发"
4. 写诗/写故事/创作 → domain="创作", requires_execution=false
5. 如果无法确定 → domain="未知", confidence<0.5
6. 如果用户输入省略了目标，但上下文中有当前文件或上一轮目标，使用上下文信息补全
7. 如果用户使用"这里"、"它"、"这个"等代词，根据上下文推断所指对象

JSON:"""


def _build_context_summary(context: CognitiveContext) -> str:
    """构建上下文摘要字符串（用于 LLM Prompt）。"""
    parts = []

    # Workspace 上下文
    if context.workspace:
        ws = context.workspace
        if ws.current_file:
            parts.append(f"当前文件: {ws.current_file}")
        if ws.opened_files:
            parts.append(f"打开的文件: {', '.join(ws.opened_files[-3:])}")
        if ws.current_symbol:
            parts.append(f"当前符号: {ws.current_symbol}")

    # 消歧结果
    resolved = context.resolved_query
    if resolved and (resolved.target or resolved.symbol):
        parts.append(f"消歧结果: target={resolved.target!r}, symbol={resolved.symbol!r}")

    # 跨轮上下文（v1.2B：timeline 派生）
    conv_state = context.conversation_state
    if conv_state and conv_state.timeline:
        latest = conv_state.timeline.latest()
        if latest and latest.target:
            parts.append(f"上一轮目标: {latest.target}")
        if latest and latest.symbol:
            parts.append(f"上一轮符号: {latest.symbol}")

    # 最近对话摘要
    if context.conversation:
        recent = context.conversation[-2:]
        conv_lines = []
        for msg in recent:
            role = msg.get("role", "?")
            content = msg.get("content", "")[:80]
            conv_lines.append(f"  {role}: {content}")
        if conv_lines:
            parts.append("最近对话:\n" + "\n".join(conv_lines))

    return "\n".join(parts) if parts else "（无上下文信息）"


def _extract_target(text: str) -> str:
    """从用户输入中提取目标对象（文件路径、目录名、符号名）。"""
    text = text.strip()

    # 策略 1: 已知前缀路径匹配
    for prefix in _KNOWN_PREFIXES:
        pattern = re.compile(r'(' + re.escape(prefix) + r'[\w./\\-]+\.\w+)', re.ASCII)
        match = pattern.search(text)
        if match:
            return match.group(1)

    # 策略 2: 通用路径匹配
    path_pattern = re.compile(r'([\w./\\-]+\.\w+)', re.ASCII)
    matches = path_pattern.findall(text)
    for m in matches:
        m = m.strip().lstrip('./\\')
        if '/' in m or '\\' in m:
            return m

    # 策略 3: 关键词后提取名称
    keyword_patterns = [
        r'(?:修改|优化|重构|读取|打开|查看|分析|检查|审查|review|read|write|edit|fix|update|improve|optimize)\s+([\w./\\-]+(?:\.\w+)?)',
        r'(?:文件|代码|脚本|模块|类|函数|方法)\s+[`"\'“”]?([\w./\\-]+(?:\.\w+)?)[`"\'“”]?',
    ]
    for pattern_text in keyword_patterns:
        match = re.search(pattern_text, text, re.IGNORECASE | re.ASCII)
        if match:
            return match.group(1).strip()

    # 策略 4: 孤立的文件/目录名
    file_pattern = re.compile(r'[\w-]+\.\w+', re.ASCII)
    match = file_pattern.search(text)
    if match:
        return match.group(0)

    # 策略 5: 驼峰/帕斯卡命名（类名、符号名）
    symbol_pattern = re.compile(r'[A-Z][a-zA-Z0-9]+')
    match = symbol_pattern.search(text)
    if match:
        return match.group(0)

    # 策略 5b: snake_case / 下划线命名（max_active → max_active，v1.2B B5）
    snake_pattern = re.compile(r'\b[a-z][a-z0-9]*(?:_[a-z0-9]+)+\b')
    match = snake_pattern.search(text)
    if match:
        return match.group(0)

    # 策略 6: 中文天气城市（"杭州天气怎么样" → 杭州，v1.2A Intent Extraction）
    if re.search(r'天气|气温|温度|下雨|下雪|台风|雾霾', text):
        m = re.search(r'([\u4e00-\u9fff]{2,3}?)\s*(?:的)?\s*(?:天气|气温|温度|如何|怎么样)', text)
        if m:
            city = m.group(1)
            if city and city not in ("天气", "气温", "温度", "如何"):
                return city

    return ""


def _merge_target(intent_target: str, context: CognitiveContext) -> str:
    """合并 IntentEngine 提取的 target 和上下文中的 target。

    优先级: intent_target > resolved_query.target > workspace.current_file
    """
    if intent_target:
        return intent_target

    # 使用 ReferenceResolver 消歧结果
    resolved = context.resolved_query
    if resolved and resolved.target:
        return resolved.target

    # 使用 current_file
    if context.current_file:
        return context.current_file

    return ""


def _merge_entities(intent_entities: list[str], context: CognitiveContext) -> list[str]:
    """合并实体列表（去重）。"""
    seen = set()
    result = []

    # 先加 IntentEngine 提取的
    for e in intent_entities:
        if e and e not in seen:
            seen.add(e)
            result.append(e)

    # 再加消歧结果的 symbol
    resolved = context.resolved_query
    if resolved and resolved.symbol and resolved.symbol not in seen:
        seen.add(resolved.symbol)
        result.append(resolved.symbol)

    # 再加消歧结果的 entities
    if resolved:
        for e in resolved.entities:
            if e and e not in seen:
                seen.add(e)
                result.append(e)

    return result


class IntentEngine:
    """意图理解引擎。

    消费 CognitiveContext（已包含 ReferenceResolver 消歧结果）。
    两级策略：
    1. 关键词快速匹配（低开销，覆盖常见模式）
    2. LLM 1-shot 分析（仅关键词未匹配或低置信度时）

    不 import 任何 Service。
    """

    def __init__(self):
        self._llm_fallback_count = 0
        self._llm = default_llm

    def _deterministic_analysis(
        self,
        context: CognitiveContext,
    ) -> tuple[IntentResult | None, str, Optional[bool], tuple[RequestedOutcome, ...]]:
        """Resolve intent facts that do not require a Provider call."""
        user_input = context.query
        text = user_input.strip().lower()

        # 先提取 target（从原始输入）
        raw_target = _extract_target(user_input)

        # 执行需求分析（World State Change → 确定性 requires_execution；v2.1A）。
        # LLM 不参与"是否执行"决策（ADR-0009），regex 规则也不堆在 Intent 里。
        need = analyze_execution_need(user_input)
        requested_outcomes = analyze_requested_outcomes(user_input)

        # An explicit user command is already a complete execution intent.
        # Route it without an LLM classification round; the Planner will use
        # the same parsed command to build the canonical shell task.
        explicit_command = extract_explicit_command(user_input)
        if explicit_command is not None:
            return IntentResult(
                domain=DOMAIN_OPERATION,
                action="execute",
                target=explicit_command,
                entities=[],
                current_file=context.current_file or "",
                confidence=0.99,
                requires_execution=True,
                summary="explicit user command execution",
                raw_input=user_input,
                reference_kind=_detect_reference_kind(user_input),
                requested_outcomes=requested_outcomes,
            ), raw_target, need, requested_outcomes

        # Code execution is also an explicit operational outcome.  Preserve
        # that deterministic fact before the general LLM domain classifier;
        # otherwise requests such as "write this Python file and run it" can
        # spend the only routing round on cognition and never reach the
        # canonical write -> execute plan.
        if RequestedOutcome.CODE_EXECUTION in requested_outcomes:
            return IntentResult(
                domain=DOMAIN_OPERATION,
                action="execute",
                target=raw_target or "python-source",
                entities=[],
                current_file=context.current_file or "",
                confidence=0.99,
                requires_execution=True,
                summary="explicit code execution outcome",
                raw_input=user_input,
                reference_kind=_detect_reference_kind(user_input),
                requested_outcomes=requested_outcomes,
            ), raw_target, need, requested_outcomes

        # External research is never delegated to an LLM-only task. The
        # Planner lowers this intent to a source-backed web tool, including
        # non-financial dynamic topics and explicit tutorial/method searches.
        if is_source_grounded_request(user_input):
            return IntentResult(
                domain=DOMAIN_WEB,
                action="fresh_research",
                target=_merge_target(raw_target, context),
                entities=_merge_entities([], context),
                current_file=context.current_file or "",
                confidence=0.98,
                requires_execution=True,
                summary="external source grounding required",
                raw_input=user_input,
                reference_kind=_detect_reference_kind(user_input),
                freshness_required=is_fresh_research_request(user_input),
                source_grounding_required=True,
                requested_outcomes=requested_outcomes,
            ), raw_target, need, requested_outcomes

        # Stage 1: 关键词快速匹配
        for pattern, domain, action, requires_exec in _KEYWORD_MAP:
            if pattern.search(text):
                # 合并 target
                final_target = _merge_target(raw_target, context)
                return IntentResult(
                    domain=_upgrade_domain(domain, need),
                    action=action,
                    target=final_target,
                    entities=_merge_entities([], context),
                    current_file=context.current_file or "",
                    confidence=0.85,
                    requires_execution=need if need is not None else requires_exec,
                    summary=f"{domain}: {action}",
                    raw_input=user_input,
                    reference_kind=_detect_reference_kind(user_input),
                    requested_outcomes=requested_outcomes,
                ), raw_target, need, requested_outcomes

        return None, raw_target, need, requested_outcomes

    @staticmethod
    def _merge_llm_analysis(
        result: IntentResult,
        context: CognitiveContext,
        raw_target: str,
        need: Optional[bool],
        requested_outcomes: tuple[RequestedOutcome, ...],
    ) -> IntentResult:
        """Apply deterministic facts to a Provider-classified intent."""
        final_target = raw_target or _merge_target("", context)

        result.target = final_target
        result.entities = _merge_entities(result.entities, context)
        result.current_file = context.current_file or ""
        result.reference_kind = _detect_reference_kind(context.query)
        result.domain = _upgrade_domain(result.domain, need)
        result.requested_outcomes = requested_outcomes

        if need is not None:
            result.requires_execution = need
        if any(
            outcome in requested_outcomes
            for outcome in (
                RequestedOutcome.CODE_EXECUTION,
                RequestedOutcome.COMMAND_EXECUTION,
            )
        ):
            result.requires_execution = True

        return result

    def analyze(self, context: CognitiveContext) -> IntentResult:
        """Synchronous compatibility API for non-Runtime callers."""
        deterministic, raw_target, need, requested_outcomes = (
            self._deterministic_analysis(context)
        )
        if deterministic is not None:
            return deterministic

        # Stage 2: LLM 1-shot 分析（domain/action 可 LLM 判定）
        result = self._llm_analyze(context.query, context)
        return self._merge_llm_analysis(
            result,
            context,
            raw_target,
            need,
            requested_outcomes,
        )

    async def analyze_async(self, context: CognitiveContext) -> IntentResult:
        """Runtime API; Provider fallback remains cancellable and observable."""
        deterministic, raw_target, need, requested_outcomes = (
            self._deterministic_analysis(context)
        )
        if deterministic is not None:
            return deterministic

        result = await self._llm_analyze_async(context.query, context)
        return self._merge_llm_analysis(
            result,
            context,
            raw_target,
            need,
            requested_outcomes,
        )

    @staticmethod
    def _llm_prompt(user_input: str, context: CognitiveContext) -> str:
        domain_descriptions = "\n".join(
            f"  {d}: {desc}" for d, desc in DOMAIN_DESCRIPTIONS.items()
        )
        context_summary = _build_context_summary(context)
        return LLM_INTENT_PROMPT.replace(
            "{context_summary}", context_summary
        ).replace(
            "{domain_descriptions}", domain_descriptions
        ).replace(
            "{input}", user_input
        )

    def _parse_llm_analysis(
        self,
        response: object,
        user_input: str,
        context: CognitiveContext,
    ) -> IntentResult:
        content = (
            response.content.strip()
            if hasattr(response, "content")
            else str(response).strip()
        )
        if content.startswith("```"):
            content = content.split("```")[1]
            if content.startswith("json"):
                content = content[4:]
        obj = json.loads(content.strip())

        domain_map = {
            "开发": DOMAIN_DEVELOPMENT,
            "知识": DOMAIN_KNOWLEDGE,
            "闲聊": DOMAIN_CHAT,
            "创作": DOMAIN_CREATION,
            "运维": DOMAIN_OPERATION,
            "记忆": DOMAIN_MEMORY,
            "文件": DOMAIN_FILE,
            "办公": DOMAIN_OFFICE,
            "数学": DOMAIN_MATH,
            "翻译": DOMAIN_TRANSLATION,
            "日程": DOMAIN_SCHEDULING,
            "网络": DOMAIN_WEB,
            "未知": DOMAIN_UNKNOWN,
        }
        domain = domain_map.get(obj.get("domain", ""), DOMAIN_UNKNOWN)
        requires_exec = domain in {
            DOMAIN_DEVELOPMENT,
            DOMAIN_FILE,
            DOMAIN_OPERATION,
            DOMAIN_OFFICE,
            DOMAIN_WEB,
            DOMAIN_UNKNOWN,
        }
        self._llm_fallback_count += 1
        return IntentResult(
            domain=domain,
            action=obj.get("action", ""),
            target=obj.get("target", ""),
            entities=obj.get("entities", []),
            current_file=context.current_file or "",
            confidence=obj.get("confidence", 0.5),
            requires_execution=requires_exec,
            summary=obj.get("summary", ""),
            raw_input=user_input,
        )

    @staticmethod
    def _llm_analysis_failure(user_input: str, error: Exception) -> IntentResult:
        failure_code = classify_execution_error(error)
        if failure_code.startswith("PROVIDER_"):
            return IntentResult(
                domain=DOMAIN_UNKNOWN,
                confidence=0.0,
                requires_execution=True,
                summary="意图理解所需的 LLM 服务不可用",
                raw_input=user_input,
                failure_code=failure_code,
                failure_message="当前 LLM 服务暂时不可用，本次未生成或执行任务。",
            )
        need = analyze_execution_need(user_input)
        return IntentResult(
            domain=DOMAIN_UNKNOWN,
            confidence=0.3,
            requires_execution=need if need is not None else True,
            summary=f"意图理解失败 ({error})，默认走执行路径",
            raw_input=user_input,
        )

    def _llm_analyze(self, user_input: str, context: CognitiveContext) -> IntentResult:
        """Synchronous LLM analysis retained for non-Runtime callers."""
        try:
            response = self._llm.invoke([
                SystemMessage(content=self._llm_prompt(user_input, context))
            ])
            return self._parse_llm_analysis(
                response,
                user_input,
                context,
            )
        except Exception as error:
            return self._llm_analysis_failure(user_input, error)

    async def _llm_analyze_async(
        self,
        user_input: str,
        context: CognitiveContext,
    ) -> IntentResult:
        """Asynchronous LLM analysis used by the production Runtime."""
        try:
            response = await self._llm.ainvoke([
                SystemMessage(content=self._llm_prompt(user_input, context))
            ])
            return self._parse_llm_analysis(
                response,
                user_input,
                context,
            )
        except Exception as error:
            return self._llm_analysis_failure(user_input, error)


# 全局单例
engine = IntentEngine()
