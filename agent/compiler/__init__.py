"""ToolSelector — maps Planner Tasks to deterministic ExecutionPlans.

Stateless rule engine. No LLM involvement.
Each verb has a corresponding Rule that knows which tools to call.
"""
from agent.compiler.tool_selector import ToolSelector, Rule

__all__ = ["ToolSelector", "Rule"]