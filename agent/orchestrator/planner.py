"""PlannerStage — PLAN 阶段编排。

认知链路：
    User Input
        │
        ▼
    ContextBuilder.build → CognitiveContext
        │
        ▼
    ReferenceResolver.resolve（确定性消歧）
        │
        ▼
    IntentEngine.analyze（意图理解）
        │
        ▼
    WorkflowRouter.route（命中 → WorkflowExecutor / 未命中 → Planner）
        │
        ▼
    Compiler.compile（Task → ExecutionPlan）

REPLAN：保留成功任务的 Facts，重新规划失败任务。

Phase C.1：从 orchestrator.py 的 plan() / replan() / _print_plan() 迁移。
"""
import asyncio
import time
from datetime import datetime
from typing import cast, Any, Dict, Mapping, Optional, Sequence, Tuple
from langchain_core.messages import AIMessage, SystemMessage, HumanMessage

from agent.state import AgentState
from agent.task import Verb
from agent.registry.capability_registry import registry as _capability_registry
from agent.cognition.intent_schema import (
    DOMAIN_CHAT,
    DOMAIN_DEVELOPMENT,
    DOMAIN_MEMORY,
)
from agent.execution_errors import (
    classify_execution_error,
)


def _build_repo_context(user_input: str, repository_indexer) -> str:
    """根据用户输入搜索相关代码片段。"""
    from agent.security import is_internal_storage_path

    if repository_indexer is None:
        raise RuntimeError("REPOSITORY_INDEX_UNAVAILABLE: request requires a workspace index")
    repository_indexer.ensure_built()
    hits = repository_indexer.search_similar(user_input, k=5)
    hits = [
        hit for hit in hits
        if not is_internal_storage_path(hit.get("path", ""))
    ]
    if not hits:
        return ""
    return "\n\n".join(f"[{h['path']}]\n{h['content']}" for h in hits)


def _get_memory_service():
    from agent.services import MemoryService

    return MemoryService


def _get_intent_engine():
    from agent.cognition.intent_engine import engine

    return engine


class _LazyIntentEngine:
    """Patchable compatibility handle without importing the Provider stack."""

    def analyze(self, *args, **kwargs):
        return _get_intent_engine().analyze(*args, **kwargs)

    async def analyze_async(self, *args, **kwargs):
        return await _get_intent_engine().analyze_async(*args, **kwargs)


intent_engine = _LazyIntentEngine()


def _render_runtime_continuation(state: AgentState) -> str:
    """CONTINUE_PLAN（继续）：从 Runtime 状态恢复当前执行，不占 Conversation 边界。

    Conversation 负责 recent_goal/last_answer；当前 plan/task 属于 Runtime。
    """
    plan = state.get("plan", []) or []
    active = [t for t in plan if t.get("status") in ("pending", "running")]
    if not active:
        return ""
    parts = ["## 当前执行（继续此前的任务，必须基于此回答）"]
    for t in active[:3]:
        goal = str(t.get("goal", "")).strip()
        if goal:
            parts.append(f"- 进行中任务: {goal}")
    return "\n".join(parts) if len(parts) > 1 else ""


def _normalize_continuation_target(target: str) -> str:
    return str(target or "").strip().replace("\\", "/").casefold().rstrip("/")


def _record_contract_diagnostic(
    state: AgentState,
    operation: str,
    error: Exception,
    *,
    run_context=None,
) -> None:
    from agent.diagnostics import handle_contract_violation

    event = handle_contract_violation(
        boundary="planner",
        operation=operation,
        expected="ConversationRetrieverProtocol.get/snapshot/runtime_pending/events",
        error=error,
        event_bus_instance=(run_context.event_bus if run_context is not None else None),
        diagnostics=(run_context.diagnostics if run_context is not None else None),
    )
    state.setdefault("diagnostics", []).append({
        "type": "contract_violation",
        "event_id": event.id,
        "failure": event.failure,
    })


def _apply_conversation_contract(
    intent,
    user_id: str,
    user_input: str,
    *,
    pending_target: str = "",
    reference_target: str = "",
    conversation_retriever=None,
):
    """Apply ADR-0013 continuation semantics before routing.

    The Conversation Runtime supplies only the previous ``runtime_pending`` bit;
    it does not own or reconstruct a plan.  Explicit continuation phrases are
    classified by IntentEngine, while a bare "继续" is resolved against that bit.
    """
    from agent.conversation import (
        ConversationIntent,
        classify_conversation_intent,
    )
    retriever = conversation_retriever
    if retriever is None:
        from agent.conversation import ConversationRetriever, ConversationTracker
        retriever = ConversationRetriever(ConversationTracker())

    kind = classify_conversation_intent(
        intent,
        user_input,
        runtime_pending=retriever.runtime_pending(user_id),
    )
    if kind is ConversationIntent.CONTINUE_PLAN:
        intent.domain = DOMAIN_DEVELOPMENT
        intent.action = "continue_plan"
        intent.requires_execution = True
    elif kind is ConversationIntent.CONTINUE_CHAT:
        intent.domain = DOMAIN_CHAT
        intent.action = "continue_chat"
        intent.requires_execution = False
    elif kind is ConversationIntent.CONTINUE_REFERENCE:
        if (
            pending_target
            and reference_target
            and _normalize_continuation_target(pending_target)
            != _normalize_continuation_target(reference_target)
        ):
            intent.domain = DOMAIN_CHAT
            intent.action = "clarify"
            intent.requires_execution = False
            intent.summary = "引用目标与未完成执行目标冲突，需要澄清"
            return kind
        intent.domain = DOMAIN_MEMORY
        intent.action = "reference"
        intent.requires_execution = False
    return kind


class PlannerStage:
    """PLAN 阶段规划器。

    持有对 ExecutionOrchestrator 容器的反向引用（访问 _timings / run_context）。
    """

    def __init__(self, orchestrator):
        self._orch = orchestrator

    async def _resolve_conversation(self, run_context):
        """Durable history: summary + verbatim tail inside the char budget.

        When history exceeds the budget one LLM call merges the durable
        summary with the newly compacted pairs and persists it, so later
        turns (and later processes) still see the whole conversation.
        """
        view = (
            getattr(run_context, "durable_store_view", None)
            if run_context is not None
            else None
        )
        if view is None:
            return [], []
        from agent.context.conversation import (
            derive_pairs,
            flatten_pairs,
            render_conversation_text,
            split_pairs_for_budget,
            SUMMARY_CHAR_CAP,
        )

        record = view.session_summary() or {}
        summary = str(record.get("summary", "") or "")
        run_results = view.conversation_results()
        pairs = derive_pairs(run_results)
        compact, kept = split_pairs_for_budget(pairs, summary)
        if compact:
            from agent.llm import llm

            prior = f"已有摘要：{summary}\n\n" if summary else ""
            render = prior + render_conversation_text(flatten_pairs(compact))
            response = await llm.ainvoke([
                SystemMessage(content=(
                    "把下面的对话历史压缩成一段不超过 "
                    f"{SUMMARY_CHAR_CAP} 字的摘要。必须保留：事实与数值、"
                    "文件路径与产物名、做过的决定、未完成的任务。"
                    "只输出摘要文本。"
                )),
                HumanMessage(content=render),
            ])
            summary = str(getattr(response, "content", "") or "")[:SUMMARY_CHAR_CAP]
            last = compact[-1]
            if last["run_id"]:
                view.save_session_summary(
                    summary,
                    through_run_id=last["run_id"],
                    through_updated_at=last["updated_at"],
                )
        return run_results, flatten_pairs(kept, summary)

    def _memory_view(self):
        session_context = getattr(self._orch, "session_context", None)
        return getattr(session_context, "memory_view", None)

    def _get_user_facts(self, user_id: str) -> str:
        memory_view = self._memory_view()
        if memory_view is not None:
            return memory_view.get_user_facts()
        return _get_memory_service().get_user_facts(user_id)

    async def run(
        self,
        user_input: str,
        user_id: str,
        context: Dict,
        repo_context: str,
        runtime_state: Optional[AgentState] = None,
    ) -> Tuple[AgentState, str, Optional[str]]:
        """PLAN 阶段：Build CognitiveContext → ReferenceResolver → IntentEngine → WorkflowRouter → Planner。

        Returns:
            (AgentState, runtime_state_next, best_answer_or_None)
        """
        t0 = time.perf_counter()

        # 构建 AgentState
        from agent.llm import llm
        now = datetime.now()
        system_content = self._orch._context_builder.render_system_context(now)
        untrusted_context = {
            "long_term_memory": context.get("long_term", ""),
            "user_facts": context.get("facts", ""),
            "repository_excerpt": repo_context,
        }

        def prompt_messages(
            request: str,
            references: Mapping[str, Any],
            conversation: Sequence[Mapping[str, str]] = (),
        ) -> list[Any]:
            messages: list[Any] = [SystemMessage(content=system_content)]
            for message in conversation:
                if message.get("role") == "assistant":
                    messages.append(AIMessage(content=message.get("content", "")))
                else:
                    messages.append(HumanMessage(content=message.get("content", "")))
            reference_text = self._orch._context_builder.render_untrusted_context(
                references
            )
            if reference_text:
                messages.append(HumanMessage(content=reference_text))
            messages.append(HumanMessage(content=request))
            return messages

        planning_memory = "\n\n".join(
            part for part in (context.get("long_term", ""), context.get("facts", ""))
            if part
        )
        state: AgentState = {
            "messages": prompt_messages(user_input, untrusted_context),
            "plan": [],
            "current_task_index": 0,
            "artifacts": {},
            "memory_context": planning_memory,
            "repo_context": repo_context,
            "retries": 0,
            "workflow": None,
            "execution_plans": [],
            "execution_mode": "result_driven",
        }
        if runtime_state is not None:
            # Runtime-owned goal/inbox projections must survive the Planner's
            # context rebuild.  The Planner may replace its task projection,
            # but it is not the owner of goal rounds or action observations.
            source_projection = dict(runtime_state)
            target_projection = cast(dict[str, Any], state)
            for key in (
                "goal_state",
                "goal_evidence",
                "goal_missing",
                "inbox",
                "execution_mode",
                "original_request",
                "requested_outcomes",
                "artifact_required",
            ):
                if key in runtime_state:
                    target_projection[key] = source_projection[key]

        # ── Stage 0: 构建 CognitiveContext ──
        # A resumed PLAN must consume the current Runtime projection.  The
        # Planner receives only ContextBuilder's narrow fields; it never sees
        # the raw checkpoint payload.
        context_state = runtime_state if runtime_state is not None else state
        run_results, conversation = await self._resolve_conversation(
            getattr(self._orch, "run_context", None)
        )
        planner_context = self._orch._context_builder.build(
            user_input=user_input,
            user_id=user_id,
            context=context,
            repo_context=repo_context,
            state=context_state,
            run_results=run_results,
            conversation=conversation,
        )
        print(f"  🧠 规划上下文: {planner_context.short_summary()}")


        # ── Stage 1: IntentEngine 意图理解（消费 CognitiveContext）──
        intent_started = time.perf_counter()
        intent = await intent_engine.analyze_async(planner_context)
        self._orch._timings["intent_route"] = round(
            time.perf_counter() - intent_started,
            3,
        )
        print(f"  🧠 意图: {intent}")
        state.setdefault("original_request", user_input)
        state["run_results"] = planner_context.run_results
        state["conversation"] = planner_context.conversation
        state["resolved_request"] = intent.resolved_request or user_input
        state["intent_target"] = intent.target
        state["artifact_required"] = intent.artifact_required
        state["intent_write_targets"] = list(intent.write_targets)
        state["intent_external_effects"] = [dict(item) for item in intent.external_effects]
        state["intent_memory_action"] = intent.memory_action
        state["intent_memory_facts"] = list(getattr(intent, "memory_facts", None) or [])
        state["requested_outcomes"] = list(dict.fromkeys(
            outcome.value for outcome in intent.requested_outcomes
        ))
        user_input = state["resolved_request"]
        planner_context.query = user_input

        needs = getattr(intent, "context_needs", None) or {}
        if needs.get("memory"):
            memory_view = self._memory_view()
            if memory_view is not None:
                retrieved = await asyncio.to_thread(
                    memory_view.get_retrieved_memory,
                    user_input,
                )
                untrusted_context["long_term_memory"] = retrieved.get("long_term", "")
                untrusted_context["user_facts"] = retrieved.get("facts", "")
                planning_memory = "\n\n".join(
                    part
                    for part in (retrieved.get("long_term", ""), retrieved.get("facts", ""))
                    if part
                )
                state["memory_context"] = planning_memory
        if needs.get("repository"):
            run_context = getattr(self._orch, "run_context", None)
            repository_indexer = (
                getattr(run_context, "repository_indexer", None)
                if run_context is not None
                else None
            )
            if repository_indexer is not None:
                retrieved_repo = await asyncio.to_thread(
                    _build_repo_context,
                    user_input,
                    repository_indexer,
                )
                untrusted_context["repository_excerpt"] = retrieved_repo
                state["repo_context"] = retrieved_repo
                planner_context.repo_context = retrieved_repo

        state["messages"] = prompt_messages(
            user_input,
            untrusted_context,
            planner_context.conversation,
        )

        # H4: preserve explicit requested outcomes and write authorization
        # before any open-ended Planner output can alter the execution scope.
        from agent.cognition.effect_authorization import EffectAuthorization

        authorization = EffectAuthorization.from_state(state, user_input)
        state["requested_outcomes"] = [
            outcome.value for outcome in authorization.requested_outcomes
        ]
        state["authorized_write_scopes"] = list(authorization.write_scopes)
        from agent.goal import GoalState

        goal_state = GoalState.from_dict(state.get("goal_state"))
        run_context = getattr(self._orch, "run_context", None)
        goal_id = str(getattr(run_context, "run_id", "") or goal_state.goal_id)
        goal_version = None
        original_request = str(state.get("original_request") or user_input)
        if intent.conversation_relation == "correction" and planner_context.run_results:
            previous_contract = planner_context.run_results[-1].get("goal_contract")
            if isinstance(previous_contract, Mapping):
                goal_id = str(previous_contract.get("goal_id") or goal_id)
                goal_version = int(previous_contract.get("goal_version", 1)) + 1
                original_request = str(
                    previous_contract.get("original_request")
                    or previous_contract.get("objective")
                    or original_request
                )
        state["goal_state"] = goal_state.with_contract(
            original_request=original_request,
            objective=user_input,
            requested_outcomes=tuple(state["requested_outcomes"]),
            delivery_requirements=tuple(intent.delivery_requirements),
            prohibited_effects=tuple(intent.prohibited_effects),
            acceptance_criteria=tuple(intent.acceptance_criteria),
            goal_id=goal_id,
            goal_version=goal_version,
        ).to_dict()
        intent_failure_code = str(getattr(intent, "failure_code", "") or "")
        if intent_failure_code:
            state["runtime_failure_code"] = intent_failure_code
            state["runtime_failure_class"] = "provider" if intent_failure_code.startswith("PROVIDER_") else "cognition"
            state["runtime_failure_retryable"] = intent_failure_code in {
                "PROVIDER_TIMEOUT",
                "PROVIDER_NETWORK",
                "PROVIDER_UNAVAILABLE",
            }
            state["runtime_terminal_status"] = "FAILED_TERMINAL"
            return (
                state,
                "FAIL",
                str(
                    getattr(intent, "failure_message", "")
                    or "当前 LLM 服务暂时不可用，本次未生成或执行任务。"
                ),
            )

        from agent.effect_truth import initialize_effect_contract

        effect_truth = initialize_effect_contract(
            state,
            user_input,
            external_effects=intent.external_effects,
            capability_resolver=_capability_registry.resolve,
        )
        if effect_truth.unsupported_effects:
            requirement = effect_truth.unsupported_effects[0]
            capability = str(requirement.get("capability", "external_effect"))
            description = str(requirement.get("description", "外部操作"))
            message = (
                f"当前没有可用的 {description} 能力，因此本次未执行该外部操作。"
                "不会伪造已完成的结果。"
            )
            state["plan"] = [{
                "id": "task-1",
                "verb": Verb.EXECUTE.value,
                "target": capability,
                "target_type": "symbol",
                "goal": description,
                "description": user_input,
                "status": "failed",
                "error": f"UNSUPPORTED_CAPABILITY: 未注册能力 {capability}",
                "error_code": "UNSUPPORTED_CAPABILITY",
                "observations": [],
            }]
            return state, "FINISH", message

        research_requested = bool(
            intent.source_grounding_required
            or intent.freshness_required
            or intent.action == "fresh_research"
        )
        if research_requested:
            from agent.bootstrap import load_tool_modules

            load_tool_modules(("web",))

        # v2.3H3: preserve the deterministic research contract in Runtime
        # state. The final answer cannot establish freshness; only successful
        # source-tool observations can satisfy this requirement.
        state["freshness_required"] = bool(
            getattr(intent, "freshness_required", False)
        )
        state["source_grounding_required"] = bool(
            getattr(intent, "source_grounding_required", False)
        )
        state["fresh_evidence"] = False
        state["answer_required"] = True

        # v2.1B-1/2：记录本轮 intent + 会话快照 + 引用类型 + Runtime 续接
        self._orch.last_intent = intent
        try:
            from agent.conversation import resolve_reference_type
            session_context = getattr(self._orch, "session_context", None)
            session_retriever = (
                session_context.conversation_retriever
                if session_context is not None
                else None
            )
            if intent.resolved_request:
                from agent.conversation import ConversationIntent
                conversation_kind = (
                    ConversationIntent.NEW_REQUEST if intent.conversation_relation == "new_request"
                    else ConversationIntent.CONTINUE_PLAN if intent.requires_execution
                    else ConversationIntent.CONTINUE_CHAT
                )
            else:
                conversation_kind = _apply_conversation_contract(
                    intent,
                    user_id,
                    user_input,
                    pending_target=getattr(planner_context, "runtime_pending_target", ""),
                    reference_target=intent.target,
                    conversation_retriever=session_retriever,
                )
            state["conversation_intent"] = conversation_kind.value
            retriever = session_retriever
            if retriever is None:
                from agent.conversation import ConversationRetriever, ConversationTracker
                retriever = ConversationRetriever(ConversationTracker())
            state["conversation_snapshot"] = retriever.snapshot(user_id)
            state["conversation_reference_type"] = resolve_reference_type(intent).value
            state["conversation_runtime_continuation"] = _render_runtime_continuation(state)
            state["conversation_clarification_required"] = intent.action == "clarify"
        except (AttributeError, ImportError, KeyError, TypeError) as exc:
            _record_contract_diagnostic(
                state,
                "conversation_contract",
                exc,
                run_context=getattr(self._orch, "run_context", None),
            )

        # 不要求执行（chat / translation / math / creation / identity 等）→ 直接 LLM 回答
        if not intent.requires_execution:
            if intent.action == "clarify":
                answer = (
                    "你提到的引用目标与当前未完成任务可能不是同一个对象。"
                    "请明确要继续当前未完成任务，还是继续你刚才提到的引用目标。"
                )
                return state, "FINISH", answer
            # v2.1B-2（ADR-0013）：Conversation Reference Resolver —— 只注入对应字段
            try:
                from agent.conversation import (
                    resolve_reference_type,
                    ReferenceType, render_reference,
                )
                session_context = getattr(self._orch, "session_context", None)
                conversation_retriever = (
                    session_context.conversation_retriever
                    if session_context is not None
                    else None
                )
                if conversation_retriever is None:
                    from agent.conversation import ConversationRetriever, ConversationTracker
                    conversation_retriever = ConversationRetriever(ConversationTracker())
                _ref_type = resolve_reference_type(intent)
                if _ref_type is ReferenceType.LAST_RUNTIME:
                    _cont = state.get("conversation_runtime_continuation", "") or ""
                    if _cont:
                        untrusted_context["conversation_continuation"] = _cont
                elif _ref_type is not ReferenceType.UNKNOWN:
                    _inj = render_reference(
                        conversation_retriever.snapshot(user_id), _ref_type)
                    if _inj:
                        untrusted_context["conversation_reference"] = _inj
            except (AttributeError, ImportError, KeyError, TypeError) as exc:
                _record_contract_diagnostic(
                    state,
                    "conversation_reference_injection",
                    exc,
                    run_context=getattr(self._orch, "run_context", None),
                )
            answer_started = time.perf_counter()
            try:
                response = await llm.ainvoke(
                    prompt_messages(
                        user_input,
                        untrusted_context,
                        planner_context.conversation,
                    )
                )
                answer = response.content if hasattr(response, 'content') else str(response)
            except Exception as exc:
                # A provider failure must remain a runtime fact.  Returning a
                # polite fallback string alone makes the answer gate treat the
                # direct-chat path as successful and can emit run_completed.
                # Keep the user-facing wording safe, while preserving a
                # stable terminal classification for the Runtime/Service.
                answer = "抱歉，我暂时无法回答。"
                state["runtime_terminal_status"] = "FAILED_TERMINAL"
                state["runtime_failure_code"] = (
                    classify_execution_error(exc) or "PROVIDER_UNAVAILABLE"
                )
                state["runtime_failure_class"] = "provider"
                state["runtime_failure_retryable"] = True
            self._orch._timings["answer_llm"] = round(
                time.perf_counter() - answer_started,
                3,
            )
            return state, "FINISH", answer

        # ── Stage 2: LOOP — the model drives tool calls directly ──
        state["loop_context"] = {
            key: value for key, value in untrusted_context.items() if value
        }
        return state, "LOOP", None

