"""ExecutionNeedAnalysis — 决定请求是否要求改变 World State（v2.1A Execution Runtime）。

ADR-0009（确定性验证）在意图层的投影：
    LLM 只决定 domain/action；"是否执行"由本分析器确定性推导，不信任 LLM。

原则：
    - 任何"改变世界状态"的请求（保存/写入/创建/删除/复制/移动/修改/覆盖/追加…）
      必须 requires_execution=True；
    - 明确的信息类请求（解释/翻译/计算/介绍…）→ False；
    - 其余交由 Intent domain 决定（None）。

设计约束：
    - 纯函数，零依赖 Service，可独立单测；
    - 不关心 domain/action/target，只回答一个问题："这句话是不是要求改变世界状态"。
"""
from enum import Enum
import re
import shlex
from typing import Optional


class RequestedOutcome(str, Enum):
    """事实层记录的用户要求，不由 LLM 重新解释。"""

    USER_VISIBLE_OUTPUT = "USER_VISIBLE_OUTPUT"
    FILE_READ = "FILE_READ"
    FILE_MUTATION = "FILE_MUTATION"
    CODE_EXECUTION = "CODE_EXECUTION"
    COMMAND_EXECUTION = "COMMAND_EXECUTION"


class EffectScope(str, Enum):
    """Whether an effect is a user-visible mutation or runtime scratch work."""

    USER_EFFECT = "USER_EFFECT"
    INTERNAL_EXECUTION_EFFECT = "INTERNAL_EXECUTION_EFFECT"

# 世界状态改变动词（World State Change Verbs）。
# 与"信息类请求"的边界：保存/写入/创建/删除/复制/移动/修改/覆盖/追加等必然改变外部状态。
_WSC = re.compile(
    r"保存(?:到|为|成)?|写入|写到|写成|创建|新建|新增|添加|增加|删除|移除|复制|拷贝|移动|修改|改成|改为|替换|覆盖|追加|附加|"
    r"输出到|存到|另存为|"
    r"(?:生成|创建|新建)\s+(?:[\w.-]+/)*[\w.-]+\.\w+|"
    r"写(?:一个|一份)?(?:脚本|程序|文件|总结|报告)?\s*到\s*(?:[\w./\\-]+\.\w+)|"
    r"继续(?:执行|任务|做|处理|完成)|恢复任务|接着(?:做|执行)|"
    r"(?:生成|写).{0,24}到\s*(?:[\w./\\-]+\.\w+)"
)

# 明确的信息类请求（前缀匹配）：不改变世界状态，直接回答即可。
_PURE_INFO = re.compile(
    r"^(?:解释|讲解|介绍一下|介绍|翻译|计算|算一下|什么是|说说|聊|告诉我|回答)"
)

# 这些词必须表示用户要求真实执行，而不是让模型描述如何执行。
_COMMAND_EXECUTION = re.compile(
    r"(?:实际\s*)?(?:执行|运行|跑一下|跑一遍|调用)\s*"
    r"(?:命令|脚本|程序|shell|bash|sh|date\b|python\b|pytest\b|npm\b|git\b|curl\b)|"
    r"(?:执行|运行|跑一下).{0,32}(?:命令|脚本|程序)|"
    r"(?:写|生成|创建).{0,32}(?:脚本|程序).{0,24}(?:执行|运行|跑一下)|"
    r"(?:执行|运行|跑一下).{0,40}(?:原样|完整).{0,12}(?:输出|结果)"
)
_CODE_EXECUTION = re.compile(
    r"(?:用|使用)\s*(?:python|Python|代码|脚本|程序).{0,24}"
    r"(?:算|计算|执行|运行|跑)|"
    r"(?:实际\s*)?(?:执行|运行|跑一下).{0,24}(?:代码|脚本|程序)|"
    r"(?:代码|脚本|程序).{0,80}(?:运行|执行|跑一下)|"
    r"(?:[\w./\\-]+\.py|它|该文件|这个脚本|此脚本).{0,24}"
    r"(?:运行|执行|跑一下)|"
    r"(?:并\s*)?(?:执行|运行|跑一下|跑一遍)\s*"
    r"(?:它|该文件|这个脚本|此脚本)?\s*(?=$|[，。；,.!?！？])"
)
_FILE_READ = re.compile(
    r"(?:读取|读一下|查看|打开|分析).{0,40}(?:文件|目录|源码|代码|\.py\b)|"
    r"(?:列出|查看)\s*(?:目录|文件列表)"
)


def analyze_requested_outcomes(text: str) -> tuple[RequestedOutcome, ...]:
    """Extract explicit user outcomes without consulting an LLM.

    H4a only preserves the requested fact.  Routing and completion enforcement
    consume this contract in the following H4 slices.
    """

    value = (text or "").strip()
    if not value:
        return ()

    outcomes = [RequestedOutcome.USER_VISIBLE_OUTPUT]
    if _FILE_READ.search(value):
        outcomes.append(RequestedOutcome.FILE_READ)
    if _WSC.search(value):
        outcomes.append(RequestedOutcome.FILE_MUTATION)
    if _CODE_EXECUTION.search(value):
        outcomes.append(RequestedOutcome.CODE_EXECUTION)
    if _COMMAND_EXECUTION.search(value):
        outcomes.append(RequestedOutcome.COMMAND_EXECUTION)
    return tuple(outcomes)


def extract_explicit_command(text: str) -> Optional[str]:
    """Extract a command explicitly supplied by the user.

    This is a routing fact, not a command generator.  If the request does not
    contain an explicit command after an execution verb, return ``None`` so
    the normal cognition path can decide whether clarification is needed.
    """

    value = (text or "").strip()
    if RequestedOutcome.COMMAND_EXECUTION not in analyze_requested_outcomes(value):
        return None
    match = re.search(r"(?:执行|运行|调用)\s+(.+)$", value)
    if match is None:
        return None
    command = re.split(
        r"[，。；,;]|\s+(?:并|然后|之后)\s+",
        match.group(1),
        maxsplit=1,
    )[0].strip()
    command = re.sub(r"\s+(?:命令|脚本|程序)\s*$", "", command).strip()
    if not command:
        return None
    try:
        tokens = shlex.split(command)
    except ValueError:
        return None
    if not tokens or not re.fullmatch(r"[A-Za-z0-9_./:-]+", tokens[0]):
        return None
    return command


def analyze_execution_need(text: str) -> Optional[bool]:
    """判定输入是否要求改变世界状态。

    Args:
        text: 用户原始输入。

    Returns:
        True  → 必须执行（World State Change）。
        False → 明确的信息类请求（不执行）。
        None  → 交由 Intent domain 决定。
    """
    text = (text or "").strip()
    if not text:
        return None
    requested = analyze_requested_outcomes(text)
    if any(
        outcome in requested
        for outcome in (
            RequestedOutcome.CODE_EXECUTION,
            RequestedOutcome.COMMAND_EXECUTION,
        )
    ):
        return True
    if _WSC.search(text):
        return True
    if _PURE_INFO.match(text):
        return False
    return None


__all__ = [
    "EffectScope",
    "RequestedOutcome",
    "analyze_execution_need",
    "analyze_requested_outcomes",
    "extract_explicit_command",
]
