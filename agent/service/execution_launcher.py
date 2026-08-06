"""Launcher protocol used by AgentService Core.

The concrete adapter owns Planner/WorkflowExecutor/ResumeCoordinator wiring.
AgentService only schedules it and never calls those internals directly.
"""

from __future__ import annotations

from typing import Protocol

from agent.runtime_context import RunContext, SessionContext

from .contracts import ResumeRunRequest, StartRunRequest


class ExecutionLauncher(Protocol):
    async def start(
        self,
        *,
        session_context: SessionContext,
        run_context: RunContext,
        request: StartRunRequest,
    ) -> None:
        ...

    async def resume(
        self,
        *,
        run_context: RunContext,
        request: ResumeRunRequest,
    ) -> None:
        ...


__all__ = ["ExecutionLauncher"]
