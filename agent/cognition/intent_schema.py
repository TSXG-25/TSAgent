"""Intent Schema — 意图数据模型。

User Input 经过 ReferenceResolver + IntentEngine 处理后，得到结构化的意图表示。
"""
from dataclasses import dataclass, field
from typing import Optional

from .execution_need import RequestedOutcome


# ── 稳定的 Domain 层（不会频繁变化） ──
DOMAIN_CHAT = "chat"             # 闲聊、打招呼、无意义
DOMAIN_KNOWLEDGE = "knowledge"   # 知识查询、信息搜索
DOMAIN_CREATION = "creation"     # 内容创作、生成
DOMAIN_DEVELOPMENT = "development"  # 代码开发、Bug 修复
DOMAIN_OPERATION = "operation"   # 运维操作、命令执行
DOMAIN_MEMORY = "memory"         # 记忆查询、事实检索
DOMAIN_FILE = "file"             # 文件操作
DOMAIN_OFFICE = "office"         # 办公文档
DOMAIN_MATH = "math"             # 数学计算
DOMAIN_TRANSLATION = "translation"  # 翻译
DOMAIN_SCHEDULING = "scheduling" # 日程管理
DOMAIN_WEB = "web"               # 网络操作
DOMAIN_UNKNOWN = "unknown"       # 无法识别

ALL_DOMAINS = [
    DOMAIN_CHAT, DOMAIN_KNOWLEDGE, DOMAIN_CREATION,
    DOMAIN_DEVELOPMENT, DOMAIN_OPERATION, DOMAIN_MEMORY,
    DOMAIN_FILE, DOMAIN_OFFICE, DOMAIN_MATH, DOMAIN_TRANSLATION,
    DOMAIN_SCHEDULING, DOMAIN_WEB, DOMAIN_UNKNOWN,
]

# ── Domain 描述（给 LLM 理解用） ──
DOMAIN_DESCRIPTIONS = {
    DOMAIN_CHAT: "闲聊对话、打招呼、无意义输入、感情表达",
    DOMAIN_KNOWLEDGE: "查询事实、搜索信息、天气、新闻、百科问答",
    DOMAIN_CREATION: "创作内容：写诗、写故事、生成文案、绘画描述",
    DOMAIN_DEVELOPMENT: "代码开发：编程、Bug修复、代码审查、功能开发、技术问题",
    DOMAIN_OPERATION: "运维操作：系统命令、部署、配置、安装",
    DOMAIN_MEMORY: "查询关于用户自身的事实、历史对话、偏好",
    DOMAIN_FILE: "文件操作：读写文件、目录浏览",
    DOMAIN_OFFICE: "办公文档处理：Word、Excel、PPT",
    DOMAIN_MATH: "数学计算、公式推导、数值运算",
    DOMAIN_TRANSLATION: "翻译任务：中英互译等",
    DOMAIN_SCHEDULING: "日程管理：提醒、待办事项",
    DOMAIN_WEB: "网络操作：网页抓取、API调用",
    DOMAIN_UNKNOWN: "无法确定用户意图",
}


@dataclass
class IntentResult:
    """意图分析结果。

    Attributes:
        domain: 一级分类（稳定，不会频繁新增）
        action: 二级动作（动态，随工具扩展）
        target: 用户指定的目标对象（文件路径/目录/符号名）
        entities: 提取的实体列表（符号名、函数名、类名等）
        current_file: 当前 Workspace 打开的文件（注入用）
        confidence: 置信度 (0-1)
        requires_execution: 是否需要 Agent 执行（False = 直接 LLM 回答）
        requested_outcomes: 用户明确要求的结果集合（不信任 LLM 覆盖）
        summary: 简短的意图说明
        raw_input: 原始用户输入
    """
    domain: str = DOMAIN_UNKNOWN
    action: str = ""
    target: str = ""
    entities: list[str] = field(default_factory=list)
    current_file: str = ""
    confidence: float = 0.0
    requires_execution: bool = True
    summary: str = ""
    raw_input: str = ""
    reference_kind: str = ""   # 引用类意图的目标字段提示（answer/instruction/runtime/goal）；Intent Engine 判定
    freshness_required: bool = False
    source_grounding_required: bool = False
    requested_outcomes: tuple[RequestedOutcome, ...] = ()
    # Stable cognition-boundary failure.  A provider outage must not be
    # downgraded to DOMAIN_UNKNOWN and sent through a second Planner call.
    failure_code: str = ""
    failure_message: str = ""

    @property
    def is_chat(self) -> bool:
        """闲聊/无意义输入→不需要执行"""
        return self.domain == DOMAIN_CHAT

    @property
    def is_unknown(self) -> bool:
        return self.domain == DOMAIN_UNKNOWN

    @property
    def has_target(self) -> bool:
        """是否有明确的文件/代码目标。"""
        return bool(self.target)

    def __repr__(self) -> str:
        return (
            f"Intent(domain={self.domain}, action={self.action}, "
            f"target={self.target!r}, "
            f"entities={self.entities}, "
            f"current_file={self.current_file!r}, "
            f"conf={self.confidence:.2f}, exec={self.requires_execution})"
        )
