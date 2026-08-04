"""Compiler — maps Planner Tasks to deterministic ExecutionPlans.

ADR-0002: four stages (Normalize → Semantic Check → Lower → Static Check).
Stateless rule engine. No LLM involvement. Pure function, no side effects.
Each verb has a corresponding Rule (lowering rule) that knows which tools to call.
"""
from agent.compiler.tool_selector import Compiler, Rule, CompileError
from agent.compiler.context import CompilerContext

__all__ = ["Compiler", "Rule", "CompileError", "CompilerContext"]
