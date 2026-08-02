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
from langchain_core.messages import SystemMessage, HumanMessage

from .cognitive_context import CognitiveContext, ResolvedQuery
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

    # 数学（简单计算走 LLM 直答；复杂计算/脚本才用工具）
    (re.compile(r'计算|算一下|算算|等于|方程|求导|积分|函数|公式|解.*方程'), DOMAIN_MATH, "calculate", False),
    (re.compile(r'^\d+[+\-*/]\d+|^\d+\.\d+'), DOMAIN_MATH, "calculate", False),

    # 创作
    (re.compile(r'写.*[诗故事文小说剧本]|创作|生成.*[诗故事文案]'), DOMAIN_CREATION, "generate", False),

    # 代码开发
    (re.compile(r'写.*[代码程序函数类]|编程|开发|实现.*功能|写.*[接口模块]|修复.*bug|debug|重构'), DOMAIN_DEVELOPMENT, "code", True),
    (re.compile(r'代码.*[审查审阅查看]|review|代码审查'), DOMAIN_DEVELOPMENT, "review", True),

    # 记忆查询
    (re.compile(r'我.*[叫姓名是]|我的.*[兴趣喜好名字]|之前.*[说提问]|记不记得|还记得'), DOMAIN_MEMORY, "query", False),
    (re.compile(r'关于我|我的.*[事实信息]'), DOMAIN_MEMORY, "query", False),

    # 文件操作
    (re.compile(r'读取.*文件|打开.*文件|写入.*文件|创建.*文件|删除.*文件|列出.*目录|浏览.*目录'), DOMAIN_FILE, "operate", True),
    (re.compile(r'^读取\s+\S+|^读\s+\S+|^打开\s+\S+|^查看\s+\S+|^阅读\s+\S+'), DOMAIN_FILE, "operate", True),
    (re.compile(r'read.*file|write.*file|list.*dir'), DOMAIN_FILE, "operate", True),

    # Office
    (re.compile(r'word|excel|ppt|文档|表格|幻灯片|办公'), DOMAIN_OFFICE, "document", True),

    # 信息搜索
    (re.compile(r'搜索|查找|查询.*[信息资料]|百度|谷歌|搜一下'), DOMAIN_KNOWLEDGE, "search", True),

    # 日程
    (re.compile(r'提醒|闹钟|待办|日程|计划|安排'), DOMAIN_SCHEDULING, "remind", True),
]

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

    # 跨轮上下文
    conv_state = context.conversation_state
    if conv_state:
        if conv_state.last_file:
            parts.append(f"上一轮文件: {conv_state.last_file}")
        if conv_state.last_symbol:
            parts.append(f"上一轮符号: {conv_state.last_symbol}")
        if conv_state.last_target:
            parts.append(f"上一轮目标: {conv_state.last_target}")

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
    for pattern in keyword_patterns:
        match = re.search(pattern, text, re.IGNORECASE | re.ASCII)
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

    def analyze(self, context: CognitiveContext) -> IntentResult:
        """分析用户输入，返回结构化意图。

        Args:
            context: 当前认知上下文（已包含 ReferenceResolver 消歧结果）

        Returns:
            结构化的 IntentResult
        """
        user_input = context.query
        text = user_input.strip().lower()

        # 先提取 target（从原始输入）
        raw_target = _extract_target(user_input)

        # Stage 1: 关键词快速匹配
        for pattern, domain, action, requires_exec in _KEYWORD_MAP:
            if pattern.search(text):
                # 合并 target
                final_target = _merge_target(raw_target, context)
                return IntentResult(
                    domain=domain,
                    action=action,
                    target=final_target,
                    entities=_merge_entities([], context),
                    current_file=context.current_file or "",
                    confidence=0.85,
                    requires_execution=requires_exec,
                    summary=f"{domain}: {action}",
                    raw_input=user_input,
                )

        # Stage 2: LLM 1-shot 分析
        result = self._llm_analyze(user_input, context)

        # 合并 target（LLM 提取的 + 上下文的）
        final_target = _merge_target(result.target, context)
        if not final_target:
            final_target = raw_target

        result.target = final_target
        result.entities = _merge_entities(result.entities, context)
        result.current_file = context.current_file or ""

        return result

    def _llm_analyze(self, user_input: str, context: CognitiveContext) -> IntentResult:
        """LLM 1-shot 意图分析（带上下文）。"""
        domain_descriptions = "\n".join(
            f"  {d}: {desc}" for d, desc in DOMAIN_DESCRIPTIONS.items()
        )
        context_summary = _build_context_summary(context)

        prompt = LLM_INTENT_PROMPT.replace(
            "{context_summary}", context_summary
        ).replace(
            "{domain_descriptions}", domain_descriptions
        ).replace(
            "{input}", user_input
        )

        try:
            response = self._llm.invoke([SystemMessage(content=prompt)])
            content = response.content.strip() if hasattr(response, 'content') else str(response).strip()
            # Extract JSON
            if content.startswith("```"):
                content = content.split("```")[1]
                if content.startswith("json"):
                    content = content[4:]
            content = content.strip()
            obj = json.loads(content)

            domain_raw = obj.get("domain", "")
            # Map Chinese domain names to constants
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
            domain = domain_map.get(domain_raw, DOMAIN_UNKNOWN)
            confidence = obj.get("confidence", 0.5)
            requires_exec = obj.get("requires_execution", True)
            # Chat domain always skips execution
            if domain == DOMAIN_CHAT:
                requires_exec = False

            self._llm_fallback_count += 1
            return IntentResult(
                domain=domain,
                action=obj.get("action", ""),
                target=obj.get("target", ""),
                entities=obj.get("entities", []),
                current_file=context.current_file or "",
                confidence=confidence,
                requires_execution=requires_exec,
                summary=obj.get("summary", ""),
                raw_input=user_input,
            )

        except Exception as e:
            # LLM 失败时保守处理：走 Planner
            return IntentResult(
                domain=DOMAIN_UNKNOWN,
                confidence=0.3,
                requires_execution=True,
                summary=f"意图理解失败 ({e})，默认走执行路径",
                raw_input=user_input,
            )


# 全局单例
engine = IntentEngine()