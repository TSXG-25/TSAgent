"""Agent loop — native tool-calling execution core.

The model calls tools directly; every call is authorized against the
intent-derived EffectAuthorization and executed through the single
ExecutionStage action path so evidence recording has one implementation.
"""
from .runner import AgentLoop
from .toolset import LoopTool, build_toolset

__all__ = ["AgentLoop", "LoopTool", "build_toolset"]
