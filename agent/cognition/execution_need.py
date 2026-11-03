"""Execution-need enums shared by Intent, authorization and verification."""
from enum import Enum


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


__all__ = ["EffectScope", "RequestedOutcome"]
