"""constraint_extractor — 确定性约束提取与 Abstention 检测（v2.0-A Planning）。

设计纪律（ADR-0009）：约束提取与信息不足检测全部**确定性规则**，禁止 LLM 参与。
推理自由度留给 LLM（理解约束→遵守约束），但"有没有约束 / 信息够不够"由规则判定。

与 v1.2 的 target 哲学一致：target 只信确定性来源（raw_target > Resolver > current_file）；
约束同样只信确定性规则（结构化约束可评估、可回归）。

输出与 evaluation/datasets/planning 的 constraints 同构：
    {"type": "no_web", "detail": ...}
    {"type": "scope_only", "path": ...}
    {"type": "no_delete", "detail": ...}
"""
import re
from typing import List

# ── 约束规则（确定性） ──
_NO_WEB_HINTS = [
    "不要联网", "不用网络", "不能上网", "别联网", "不联网", "不要上网",
    "离线", "本地实现", "本地完成", "不要查资料",
    "no web", "offline", "don't use the internet", "don't use internet",
]
_NO_DELETE_HINTS = [
    "不要删除", "不能删除", "别删除", "不删除", "不要删", "不能删", "别删",
    "don't delete", "no delete", "不要移除",
]

_SCOPE_ONLY_RE = re.compile(
    r"(?:只能|只|仅|只要)(?:修改|改|编辑|动|写|动到|修改到)"
    r"[^，。；;\n]*?([\w./-]+)"
)
# 模糊指示词（无具体目标时的信息不足信号）
_VAGUE_RE = re.compile(r"那个|这个|那(?:个|个文件|个函数|个模块|个东西)|它", re.I)
_FILE_RE = re.compile(r"[\w./-]+\.(?:py|js|ts|md|json|txt|java|go|rs|pyc)")


def extract_constraints(user_input: str) -> List[dict]:
    """确定性提取用户输入中的显式约束。

    Returns:
        List[dict]：与 planning dataset 的 constraints 同构。
    """
    constraints: List[dict] = []
    text = (user_input or "").strip()
    if not text:
        return constraints
    lower = text.lower()

    # no_web
    if any(h in lower for h in _NO_WEB_HINTS):
        constraints.append({"type": "no_web", "detail": "不要联网"})

    # no_delete
    if any(h in lower for h in _NO_DELETE_HINTS):
        constraints.append({"type": "no_delete", "detail": "不要删除任何文件"})

    # scope_only
    m = _SCOPE_ONLY_RE.search(text)
    if m:
        path = m.group(1).rstrip("/")
        constraints.append({"type": "scope_only", "path": path, "detail": f"只能修改 {path}/"})

    return constraints


def detect_abstention(user_input: str, grounding=None, repo_context: str = "") -> bool:
    """信息不足检测（Uncertainty 横切：Unknown > Guess）。

    Abstain 条件（全部满足才 abstain）：
        1. 输入中无具体文件路径
        2. Grounding 无候选（搜索空间为空，无法消歧）
        3. 无 repo_context（当前文件上下文无法补全"那个模块"）
        4. 输入含模糊指示词（那个/这个/它）或"动作 + 模糊对象"

    Returns:
        True = 信息不足，应 Abstain / Ask User（不猜）。
    """
    text = (user_input or "").strip()
    if not text:
        return True  # 空输入本身就是信息不足

    if _FILE_RE.search(text):
        return False

    if grounding is not None:
        cands = getattr(grounding, "candidates", None)
        if cands:
            return False

    if (repo_context or "").strip():
        return False

    return bool(_VAGUE_RE.search(text))
