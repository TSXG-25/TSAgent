#!/usr/bin/env python3
"""v2.0 RC offline smoke demo.

This is an executable Runtime demo, not a benchmark:
    1. boot the real workspace/tool registries;
    2. compile and execute read/list Tasks through ExecutorFactory;
    3. inject one deterministic failure and pass only its evidence to
       Reflection and Decision.

It does not call an external LLM and does not modify repository files.
"""
import asyncio
import os
import sys
from pathlib import Path

os.environ.setdefault("TSAGENT_ALLOW_LOCAL_EXECUTION", "1")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent.bootstrap import load_all
from agent.compiler.rules import DEFAULT_RULES
from agent.compiler.tool_selector import Compiler
from agent.context import ReflectionContext, RuntimeContext
from agent.decision import DecisionInput, decide
from agent.executor.contract import executor_factory
from agent.reflection.reflector import reflect_context
from agent.registry.tool_registry import registry
from agent.services.workspace_service import get_workspace_service
from agent.task import Task, Verb
from agent.workflow import ExecutionContext
from evaluation.benchmark.failboard_v2 import Evidence


def _compiler() -> Compiler:
    compiler = Compiler()
    for rule in DEFAULT_RULES:
        compiler.add_rule(rule)
    return compiler


async def _execute(compiler: Compiler, task: Task):
    workspace = get_workspace_service()
    from agent.compiler.context import CompilerContext
    plan = compiler.compile(task, context=CompilerContext(workspace=workspace, registry=registry))
    context = ExecutionContext(
        task=task,
        user_input=task.goal,
        variables={"workspace": workspace, "execution_plan": plan},
    )
    executor = executor_factory.get(plan.executor)
    result = await executor.execute(task, context)
    print(
        f"[EXECUTION] {task.id}: executor={plan.executor} "
        f"success={result.success} output={result.text[:100]!r}"
    )
    return result


async def main() -> int:
    print("TSAgent v2.0 RC offline smoke demo")
    load_all()
    compiler = _compiler()

    read_result = await _execute(compiler, Task(
        id="demo-read",
        verb=Verb.READ,
        target="agent/runtime.py",
        target_type="file",
        goal="读取 Runtime 实现",
    ))
    list_result = await _execute(compiler, Task(
        id="demo-list",
        verb=Verb.LIST,
        target="tests/",
        target_type="file",
        goal="列出测试目录",
    ))
    if not (read_result.success and list_result.success):
        return 1

    failure = ReflectionContext(
        runtime=RuntimeContext(query="读取不存在的文件"),
        task_id="demo-failure",
        failure="workspace.resolve: 无匹配文件",
        evidence=(Evidence(
            source="grounder",
            location="workspace.resolve",
            expected="candidate file",
            actual="无匹配",
        ),),
        symptom="hallucination",
        retry_count=1,
        last_action="read_file",
    )
    reflection = reflect_context(failure)
    decision_input = DecisionInput.from_reflection_context(
        failure,
        diagnosis="grounding_miss",
        diagnosis_confidence=reflection.diagnosis.confidence,
    )
    decision, trace = decide(decision_input)
    print(
        f"[RECOVERY] root_cause={reflection.diagnosis.root_cause} "
        f"correction={reflection.correction.action} "
        f"decision={decision.action} rule={trace.policy_rule}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
