"""Stable public API for TSAgent.

Integrations should depend on ``TSAgent`` rather than the internal runtime
orchestrator classes.  The runtime remains replaceable behind this facade.
"""
from agent.runtime import UniversalAgent


class TSAgent:
    """Public application facade for one user/session."""

    def __init__(self, user_id: str = "default") -> None:
        self._runtime = UniversalAgent(user_id)

    @property
    def user_id(self) -> str:
        return self._runtime.user_id

    async def run(self, user_input: str) -> str:
        """Run one user request through the unified runtime."""
        if not isinstance(user_input, str) or not user_input.strip():
            raise ValueError("user_input must be a non-empty string")
        return await self._runtime.run(user_input)
