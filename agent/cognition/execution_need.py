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
import re
from typing import Optional

# 世界状态改变动词（World State Change Verbs）。
# 与"信息类请求"的边界：保存/写入/创建/删除/复制/移动/修改/覆盖/追加等必然改变外部状态。
_WSC = re.compile(
    r"保存(?:到|为|成)?|写入|写到|写成|创建|新建|删除|移除|复制|拷贝|移动|修改|覆盖|追加|附加|"
    r"输出到|存到|另存为|"
    r"继续(?:执行|任务|做|处理|完成)|恢复任务|接着(?:做|执行)|"
    r"(?:生成|写).{0,24}到\s*(?:[\w./\\-]+\.\w+)"
)

# 明确的信息类请求（前缀匹配）：不改变世界状态，直接回答即可。
_PURE_INFO = re.compile(
    r"^(?:解释|讲解|介绍一下|介绍|翻译|计算|算一下|什么是|说说|聊|告诉我|回答)"
)


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
    if _WSC.search(text):
        return True
    if _PURE_INFO.match(text):
        return False
    return None
