"""Deterministic policy for selecting pre-answer context work.

The policy runs before the cognitive pipeline and must not call an LLM or an
embedding model.  It selects only the context sources justified by the
request.  Ambiguous requests start without optional context and may be
classified by the normal intent boundary without loading every index first.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

from agent.cognition.execution_need import RequestedOutcome, analyze_requested_outcomes
from agent.cognition.research_policy import (
    is_fresh_research_request,
    is_source_grounded_request,
)


class ContextMode(str, Enum):
    SIMPLE_CHAT = "simple_chat"
    MINIMAL = "minimal"
    MEMORY = "memory"
    RESEARCH = "research"
    REPOSITORY = "repository"
    EXECUTION = "execution"


_SIMPLE_CHAT_PATTERN = re.compile(
    r"(?:"
    r"你好|hi|hello|嗨|嘿|在吗|"
    r"你是谁(?:呀)?|你叫什么|"
    r"介绍(?:一下|下)?你自己|自我介绍|你能做什么|"
    r"谢谢|感谢|谢啦|多谢|thx|thanks|"
    r"再见|拜拜|晚安|bye|"
    r"我爱你|喜欢你"
    r")",
    re.IGNORECASE,
)
_NON_CHAT_PATTERN = re.compile(
    r"文件|目录|源码|代码|脚本|程序|搜索|查询|研究|调研|"
    r"股票|股市|新闻|天气|最新|今天|今日|最近|当前|实时|"
    r"执行|运行|保存|写入|输出到|修改|删除|复制|移动|"
    r"记得|记住|我的|上次|之前",
    re.IGNORECASE,
)
_MEMORY_QUERY_PATTERN = re.compile(
    r"我(?:叫什么|的名字|住在哪里|住哪儿|来自哪里)|"
    r"我(?:最)?喜欢什么|我(?:的)?(?:编程语言|语言|兴趣|偏好)|"
    r"我的(?:偏好|兴趣|事实|信息)|关于我|记不记得|还记得|"
    r"上一轮|上次|之前的对话",
    re.IGNORECASE,
)
_PERSONALIZED_PATTERN = re.compile(
    r"结合我的|根据我的|按照我的|我的偏好|用户偏好|个性化|适合我",
    re.IGNORECASE,
)
_FACT_CAPTURE_PATTERN = re.compile(
    r"我(?:叫|是|住在|居住于|居住在|来自|喜欢|最喜欢)|"
    r"记住(?:我|这个)|我使用的(?:编程语言|语言|编辑器|框架)|"
    r"我的(?:\s*API[ _-]?key|密钥|手机号|邮箱)\s*(?:是|为|[:：])",
    re.IGNORECASE,
)
_REPOSITORY_PATTERN = re.compile(
    r"仓库|代码库|项目中|源码|源代码|代码|函数|类|模块|目录|"
    r"(?:agent|tools|tests|src|workflows|skills)/|"
    r"\.(?:py|ts|tsx|js|jsx|go|rs|java|cpp|h)\b",
    re.IGNORECASE,
)


def _is_simple_chat_request(text: str) -> bool:
    value = re.sub(r"\s+", "", str(text or "").strip())
    if not value:
        return False
    if len(analyze_requested_outcomes(value)) != 1:
        return False
    if is_fresh_research_request(value) or is_source_grounded_request(value):
        return False
    if _NON_CHAT_PATTERN.search(value):
        return False
    if any(
        pattern.search(value)
        for pattern in (
            _MEMORY_QUERY_PATTERN,
            _PERSONALIZED_PATTERN,
            _FACT_CAPTURE_PATTERN,
            _REPOSITORY_PATTERN,
        )
    ):
        return False
    # Short requests without an explicit context/effect signal are safe to
    # answer from the base model. This covers terse conversational probes such
    # as "测试" without making a keyword list for every possible utterance.
    return bool(_SIMPLE_CHAT_PATTERN.search(value)) or len(value) <= 12


@dataclass(frozen=True)
class ContextPolicy:
    """Pre-answer context work selected from the original user request."""

    mode: ContextMode
    memory_retrieval: bool
    repository_retrieval: bool
    pre_answer_fact_extraction: bool
    post_answer_fact_extraction: bool
    semantic_skill_selection: bool

    @classmethod
    def for_request(cls, text: str) -> "ContextPolicy":
        if _is_simple_chat_request(text):
            return cls(
                mode=ContextMode.SIMPLE_CHAT,
                memory_retrieval=False,
                repository_retrieval=False,
                pre_answer_fact_extraction=False,
                post_answer_fact_extraction=False,
                semantic_skill_selection=False,
            )

        value = str(text or "").strip()
        personalized = bool(_PERSONALIZED_PATTERN.search(value))
        fact_capture = bool(
            _FACT_CAPTURE_PATTERN.search(value)
            and not _MEMORY_QUERY_PATTERN.search(value)
            and not re.search(r"什么|哪个|哪一|哪儿|哪里|谁|吗|呢|？|\?", value)
        )
        if _MEMORY_QUERY_PATTERN.search(value):
            return cls(
                mode=ContextMode.MEMORY,
                memory_retrieval=True,
                repository_retrieval=False,
                pre_answer_fact_extraction=False,
                post_answer_fact_extraction=False,
                semantic_skill_selection=False,
            )
        if is_fresh_research_request(value) or is_source_grounded_request(value):
            return cls(
                mode=ContextMode.RESEARCH,
                memory_retrieval=personalized,
                repository_retrieval=False,
                pre_answer_fact_extraction=False,
                post_answer_fact_extraction=False,
                semantic_skill_selection=False,
            )
        requested = analyze_requested_outcomes(value)
        execution_requested = any(
            outcome in requested
            for outcome in (
                RequestedOutcome.FILE_MUTATION,
                RequestedOutcome.CODE_EXECUTION,
                RequestedOutcome.COMMAND_EXECUTION,
            )
        )
        if execution_requested:
            return cls(
                mode=ContextMode.EXECUTION,
                memory_retrieval=personalized,
                repository_retrieval=False,
                pre_answer_fact_extraction=False,
                post_answer_fact_extraction=fact_capture,
                # Explicit execution requests already carry their effect
                # contract.  Semantic skill selection would load the
                # embedding model before a deterministic tool path and is
                # not required to authorize or verify the effect.
                semantic_skill_selection=False,
            )
        if _REPOSITORY_PATTERN.search(value):
            return cls(
                mode=ContextMode.REPOSITORY,
                memory_retrieval=personalized,
                repository_retrieval=True,
                pre_answer_fact_extraction=False,
                post_answer_fact_extraction=fact_capture,
                semantic_skill_selection=True,
            )
        if fact_capture:
            return cls(
                mode=ContextMode.MEMORY,
                memory_retrieval=False,
                repository_retrieval=False,
                pre_answer_fact_extraction=False,
                post_answer_fact_extraction=True,
                semantic_skill_selection=False,
            )
        return cls(
            mode=ContextMode.MINIMAL,
            memory_retrieval=False,
            repository_retrieval=False,
            pre_answer_fact_extraction=False,
            post_answer_fact_extraction=False,
            semantic_skill_selection=False,
        )


__all__ = ["ContextMode", "ContextPolicy"]
