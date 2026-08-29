"""ContextBuilder — 认知上下文构建。

职责：
1. build()：聚合 MemoryService / WorkspaceService / State → CognitiveContext
2. render_context()：渲染系统 Prompt 的内存上下文部分
3. update_conversation_state()：意图理解后更新跨轮对话状态

Phase C.1：从 orchestrator.py 的 _build_cognitive_context / _render_context / _update_conversation_state 迁移。
"""
from datetime import datetime
from enum import Enum
from typing import Any, Dict, Mapping, Optional

from agent.state import AgentState
from agent.services import MemoryService
from agent.cognition.cognitive_context import ConversationState
from agent.context.contracts import PlannerContext
from agent.cognition.intent_schema import IntentResult
from agent.context_policy import ContextPolicy


_COMPLETED_TASK_STATUSES = frozenset({"succeeded", "skipped"})


def _task_projection(
    task: Mapping[str, Any],
    *,
    dependencies: Optional[list[str]] = None,
) -> dict[str, Any]:
    """Project one Runtime task into the Planner continuation contract.

    Descriptions, inputs, observations, errors and policy are deliberately
    excluded.  They are Runtime/Executor details, not Planner facts.
    """

    raw_verb = task.get("verb", "")
    verb = raw_verb.value if isinstance(raw_verb, Enum) else str(raw_verb or "")
    task_dependencies = dependencies
    if task_dependencies is None:
        task_dependencies = [
            str(value)
            for value in task.get("dependencies", []) or []
        ]
    return {
        "id": str(task.get("id", "")),
        "verb": verb,
        "target": str(task.get("target", "") or ""),
        "target_type": str(task.get("target_type", "") or ""),
        "status": str(task.get("status", "pending") or "pending"),
        "dependencies": list(task_dependencies),
    }


def _continuation_projection(
    plan: object,
) -> tuple[tuple[dict[str, Any], ...], tuple[dict[str, Any], ...]]:
    """Return completed tasks and the remaining active task scope."""

    if not isinstance(plan, list):
        return (), ()
    tasks = [item for item in plan if isinstance(item, Mapping)]
    active_ids = {
        str(task.get("id", ""))
        for task in tasks
        if str(task.get("status", "pending") or "pending")
        not in _COMPLETED_TASK_STATUSES
    }
    completed: list[dict[str, Any]] = []
    remaining: list[dict[str, Any]] = []
    for task in tasks:
        status = str(task.get("status", "pending") or "pending")
        if status in _COMPLETED_TASK_STATUSES:
            completed.append(_task_projection(task))
        else:
            dependencies = [
                str(value)
                for value in task.get("dependencies", []) or []
                if str(value) in active_ids
            ]
            remaining.append(_task_projection(task, dependencies=dependencies))
    return tuple(completed), tuple(remaining)


def _established_fact_projection(state: Mapping[str, Any]) -> tuple[str, ...]:
    """Collect compact, already-recorded observations for planning only."""

    facts: list[str] = []
    for task in state.get("plan", []) or []:
        if not isinstance(task, Mapping):
            continue
        task_id = str(task.get("id", ""))
        for observation in task.get("observations", []) or []:
            if not isinstance(observation, Mapping):
                continue
            summary = str(observation.get("summary", "") or "").strip()
            if summary:
                facts.append(f"{task_id}: {summary[:300]}")
    for evidence in state.get("goal_evidence", []) or []:
        if not isinstance(evidence, Mapping):
            continue
        detail = str(evidence.get("detail", "") or "").strip()
        if detail:
            facts.append(detail[:300])
    return tuple(dict.fromkeys(facts))


def _artifact_reference_projection(artifacts: object) -> tuple[str, ...]:
    """Expose opaque artifact identities, never filesystem paths."""

    if not isinstance(artifacts, Mapping):
        return ()
    references: list[str] = []
    for key, value in artifacts.items():
        if isinstance(value, Mapping):
            reference = value.get("artifact_id", value.get("id", key))
        else:
            reference = key
        rendered = str(reference or "").strip()
        if rendered:
            references.append(rendered)
    return tuple(dict.fromkeys(references))


class ContextBuilder:
    """认知上下文构建器。

    持有对 ExecutionOrchestrator 容器的反向引用（访问 _conversation_state）。
    """

    def __init__(self, orchestrator):
        self._orch = orchestrator

    def build(
        self,
        user_input: str,
        user_id: str,
        context: dict,
        repo_context: str,
        state: AgentState,
        context_policy: Optional[ContextPolicy] = None,
    ) -> PlannerContext:
        """构建 PlannerContext（认知层统一入口）。

        从 MemoryService、WorkspaceService 等来源聚合数据。
        PlannerContext 是纯数据容器，下游模块不 import 任何 Service。
        """
        session_context = getattr(self._orch, "session_context", None)
        memory_view = getattr(session_context, "memory_view", None)

        # Workspace 上下文
        ws_context = None
        try:
            run_context = self._orch.run_context
            if run_context is not None:
                if run_context.workspace is not None:
                    ws_context = run_context.workspace.current_context()
            else:
                # A planner without a RunContext has no workspace facts.  It
                # must not consult a process-global workspace as an implicit
                # source of truth.
                ws_context = None
        except Exception:
            pass

        use_memory = context_policy is None or context_policy.memory_retrieval

        # 从 MemoryService 获取最近对话
        conversation = []
        if use_memory:
            try:
                session_text = (
                    memory_view.get_session_context(n=8)
                    if memory_view is not None
                    else MemoryService.get_session_context(user_id, n=8)
                )
                if session_text:
                    lines = session_text.strip().split("\n")
                    for line in lines[-10:]:
                        line = line.strip()
                        if line.startswith("用户:"):
                            conversation.append({"role": "user", "content": line[3:].strip()})
                        elif line.startswith("助手:") or line.startswith("AI:"):
                            conversation.append({"role": "assistant", "content": line[3:].strip()})
                        elif ":" in line:
                            parts = line.split(":", 1)
                            conversation.append({"role": parts[0].strip(), "content": parts[1].strip()})
            except Exception:
                pass

        # 当前 plan / task
        plan = state.get("plan", [])
        completed_tasks, continuation_scope = _continuation_projection(plan)
        current_task = None
        task_idx = state.get("current_task_index", 0)
        if plan and task_idx < len(plan):
            current_task = plan[task_idx]

        # Artifacts
        artifacts = state.get("artifacts", {})
        established_facts = _established_fact_projection(state)
        available_artifacts = _artifact_reference_projection(artifacts)

        # Memory facts
        memory = {
            "facts": context.get("facts", ""),
        }

        # 跨会话解析事实（Memory Facts，v1.2C；Resolver 纯函数）
        memory_resolutions = []
        if use_memory:
            try:
                memory_resolutions = (
                    memory_view.get_resolutions(n=20)
                    if memory_view is not None
                    else MemoryService.get_resolutions(user_id, n=20)
                )
            except Exception:
                pass

        # Repository 符号列表（file → [symbols]，Ordinal 解析用；v1.2B B5）
        repository_symbols = {}
        if context_policy is None or context_policy.repository_retrieval:
            try:
                from agent.repository.indexer import get_repository_indexer

                idx = get_repository_indexer()
                if idx is not None and idx.file_symbols:
                    repository_symbols = idx.file_symbols
            except Exception:
                pass

        return PlannerContext(
            query=user_input,
            conversation=conversation,
            conversation_state=self._orch._conversation_state,
            workspace=ws_context,
            plan=plan,
            task=current_task,
            runtime_pending_target=str(context.get("runtime_pending_target", "") or ""),
            repository_context=repo_context,
            repository_symbols=repository_symbols,
            memory=memory,
            memory_resolutions=memory_resolutions,
            artifacts=artifacts,
            completed_tasks=completed_tasks,
            established_facts=established_facts,
            available_artifacts=available_artifacts,
            continuation_scope=continuation_scope,
        )

    def update_conversation_state(
        self,
        intent: IntentResult,
        resolution=None,
    ) -> None:
        """在意图理解后更新跨轮对话状态（v1.2B：State = Cache）。

        timeline 是唯一语义来源（写入 Resolver 产出的 ResolutionResult）。
        """
        state = self._orch._conversation_state
        if resolution is not None:
            state.record(resolution)

    def render_context(self, context: dict, now: datetime) -> str:
        """渲染系统 Prompt 的记忆上下文部分。"""
        date_str = now.strftime("%Y年%m月%d日")
        time_str = now.strftime("%H:%M")
        weekday = now.strftime("%A")
        parts = [f"当前时间: {date_str} {time_str} {weekday}"]
        session = context.get("session", "")
        if session:
            parts.append(f"\n## 当前会话（最新）\n{session}")
        short_term = context.get("short_term", "")
        if short_term:
            parts.append(f"\n## 近期对话\n{short_term}")
        long_term = context.get("long_term", "")
        if long_term:
            parts.append(f"\n## 历史对话摘要\n{long_term}")
        facts = context.get("facts", "")
        if facts:
            parts.append(f"\n## 关于用户的事实\n{facts}")
        parts.append(
            "\n## 回答规则\n- 如果用户询问个人信息或时间，优先使用会话记录回答。\n"
            "- 禁止编造不存在的历史。\n- 不要描述执行过程。\n"
        )
        return "\n\n".join(parts)
