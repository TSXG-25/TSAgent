# TSAgent v2.0 Release Notes

## What TSAgent is

TSAgent is a workspace-oriented agent runtime for engineering tasks. It turns
user intent into canonical Tasks, compiles them into deterministic execution
plans, runs them through one executor pipeline, and records structured failure
evidence for Reflection and Decision.

## Highlights

- Unified Runtime and one execution pipeline: `Task → ExecutionPlan → ExecutorFactory`.
- One canonical Task model from Planner through Executor.
- Context projection: `RuntimeContext`, `PlannerContext`, `ExecutorContext`, and `ReflectionContext`.
- Deterministic Reflection and Decision engines backed by FailBoard evidence.
- Capability evaluation, Contract Verification, Trend Gate, and Architecture Verification.
- Stable public Python API: `from agent import TSAgent`.
- Locked dependency set and CI RC gates.

## Breaking changes

- The legacy ReAct executor, ActionResolver, executor DAG, and NodeGraph APIs were removed.
- Function-based legacy workflows were removed; WorkflowRegistry accepts only canonical `Workflow` objects.
- `Task` and `Observation` are no longer exported from `agent.planner.schemas`; import them from `agent.task`.
- `ToolSelector.select()` and its legacy SPI were removed; use `compile(task, CompilerContext(...))`.
- Planner output is validated through the canonical Task model; goal-text verb guessing was removed.

## Verification baseline

- Offline regression suite: `149 passed`.
- Tool regression suite: `17 passed, 3 deselected` (web-network cases excluded).
- Reflection benchmark: PASS.
- Decision benchmark: PASS.
- Trend Gate: PASS.
- Contract Verification: PASS.
- Architecture Verification: PASS.

## RC path

1. Tag `v2.0.0-rc1` after the local and CI gates pass.
2. Run provider-backed demos with at least two configured providers.
3. Promote to `v2.0.0` when the RC introduces no new FailBoard events.

Provider-backed demos are deliberately not reported as complete until they are
run with real credentials in the target environment.
