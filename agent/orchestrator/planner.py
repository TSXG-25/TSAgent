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
import re
import time
from datetime import datetime
from typing import Dict, Optional, Tuple
from langchain_core.messages import SystemMessage, HumanMessage

from agent.state import AgentState
from agent.executor.executors.workflow import WorkflowExecutor
from agent.planner.planner import plan_with_metadata
from agent.services import MemoryService
from agent.registry.skill_registry import skill_registry
from agent.router.workflow_router import router as workflow_router
from agent.cognition.intent_engine import engine as intent_engine
from agent.workflow import ExecutionContext, Artifact, Workflow
from agent.task import ExecutionPlan, Task, Verb
from agent.compiler.context import CompilerContext
from agent.registry.tool_registry import registry as _tool_registry
from agent.cognition.intent_schema import DOMAIN_CHAT, DOMAIN_DEVELOPMENT, DOMAIN_MEMORY
from agent.execution_errors import classify_execution_error, is_non_retriable
from agent.cognition.research_policy import research_query, research_timeliness


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
    from agent.compat.conversation import get_legacy_conversation_retriever

    retriever = conversation_retriever or get_legacy_conversation_retriever()

    kind = classify_conversation_intent(
        intent,
        user_input,
        runtime_pending=retriever.runtime_pending(user_id),
    )
    if kind is ConversationIntent.CONTINUE_PLAN:
        intent.domain = DOMAIN_DEVELOPMENT
        intent.action = "continue_plan"
        intent.requires_execution = True
        intent.reference_kind = "runtime"
    elif kind is ConversationIntent.CONTINUE_CHAT:
        intent.domain = DOMAIN_CHAT
        intent.action = "continue_chat"
        intent.requires_execution = False
        intent.reference_kind = "answer"
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
            intent.reference_kind = "instruction"
            intent.summary = "引用目标与未完成执行目标冲突，需要澄清"
            return kind
        intent.domain = DOMAIN_MEMORY
        intent.action = "reference"
        intent.requires_execution = False
        intent.reference_kind = "instruction"
    return kind


def _extract_workflow_output_path(user_input: str) -> str:
    """Extract an explicit output file for the question-code workflow."""
    return _extract_explicit_output_path(user_input) or "output/solution.py"


def _extract_explicit_output_path(user_input: str) -> Optional[str]:
    """Extract the user-requested output path, without inventing a default."""
    paths = _extract_explicit_output_paths(user_input)
    return paths[-1] if paths else None


def _extract_explicit_output_paths(user_input: str) -> list[str]:
    """Extract all explicit output paths in their input order."""
    candidates = re.findall(
        r"(?<![\w/])(?:[\w.-]+/)*[\w.-]+\.(?:py|txt|md|csv|json|yaml|yml|xlsx|xls|docx|pptx)",
        user_input or "",
        flags=re.IGNORECASE,
    )
    paths: list[str] = []
    for candidate in reversed(candidates):
        normalized = candidate.replace("\\", "/")
        if normalized.lower().startswith("input/") or normalized.lower().endswith("question.docx"):
            continue
        if normalized.casefold() not in {item.casefold() for item in paths}:
            paths.insert(0, normalized)
    return paths


_OUTPUT_MATERIALIZATION_RE = re.compile(
    r"保存到|保存为|写入到|写到|输出到|落盘|另存为|写成|生成|创建|新建"
)
_TEXT_OUTPUT_SUFFIXES = (
    ".py", ".txt", ".md", ".json", ".csv", ".yaml", ".yml",
    ".js", ".ts", ".rs", ".go", ".java", ".sh", ".html", ".css",
)


def _ensure_explicit_output_write_task(
    plan: list[dict],
    user_input: str,
    intent=None,
) -> list[dict]:
    """Guarantee an explicit text-file output request reaches the write chain.

    The LLM Planner may correctly create search/explain tasks while omitting the
    world-state mutation implied by ``保存到 output/example.py``.  That omission
    is a contract violation, not a reason to trust the final prose.  Add one
    canonical ``write`` Task after the preceding tasks so the existing WriteRule
    and ExecutionVerifier own materialization and truthfulness.
    """
    if intent is not None and not getattr(intent, "requires_execution", False):
        return plan
    if not _OUTPUT_MATERIALIZATION_RE.search(user_input or ""):
        return plan

    targets = [
        target for target in _extract_explicit_output_paths(user_input)
        if target.lower().endswith(_TEXT_OUTPUT_SUFFIXES)
    ]
    if not targets:
        # Office/binary targets retain their dedicated degradation path.
        return plan

    write_tasks = [
        task for task in plan
        if str(task.get("verb", "")).lower() == Verb.WRITE.value
    ]
    normalized_targets = {
        target.casefold().rstrip("/") for target in targets
    }
    assigned_targets = {
        str(task.get("target", "")).replace("\\", "/").casefold().rstrip("/")
        for task in write_tasks
        if task.get("target")
    }
    for index, target in enumerate(targets):
        normalized_target = target.casefold().rstrip("/")
        if normalized_target in assigned_targets:
            continue
        # Reuse an unbound Planner write task before appending a new one.
        reusable = next(
            (
                task for task in write_tasks
                if not task.get("target")
                or str(task.get("target", "")).replace("\\", "/").casefold().rstrip("/")
                not in normalized_targets
            ),
            None,
        )
        if reusable is not None:
            reusable["target"] = target
            reusable["target_type"] = "file"
            assigned_targets.add(normalized_target)

    missing_targets = [
        target for target in targets
        if target.casefold().rstrip("/") not in assigned_targets
    ]
    if not missing_targets:
        return plan

    existing_ids = [str(task.get("id", "")) for task in plan if task.get("id")]
    base_dependencies = list(existing_ids)
    next_number = len(plan) + 1
    for target in missing_targets:
        next_id = f"task-{next_number}"
        while next_id in existing_ids:
            next_number += 1
            next_id = f"task-{next_number}"
        plan.append({
            "id": next_id,
            "verb": Verb.WRITE.value,
            "target": target,
            "target_type": "file",
            "goal": f"根据用户需求生成内容并保存到 {target}",
            "description": (
                "这是用户明确要求的文件落盘步骤。保留前置检索/分析结果，"
                "只输出目标文件的完整内容并实际写入。\n"
                f"原始需求：{user_input}"
            ),
            "success_condition": f"文件 {target} 存在且非空",
            "dependencies": base_dependencies,
            "children": [],
            "inputs": {"use_prior_facts": True},
            "status": "pending",
            "observations": [],
            "error": "",
        })
        existing_ids.append(next_id)
        next_number += 1
    return plan


def _extract_literal_file_write(user_input: str) -> Optional[tuple[str, str]]:
    """Extract a complete one-file write request without invoking an LLM.

    This fast path is deliberately narrow: it only accepts one explicit text
    path and a literal ``内容为/内容是`` clause, with no research, read, or
    execution verb.  The resulting Task still goes through Compiler,
    PlanExecutor, and ExecutionVerifier.
    """
    value = str(user_input or "").strip()
    if not value or len(_extract_explicit_output_paths(value)) != 1:
        return None
    if re.search(r"搜索|检索|查找|读取|分析|合并|运行|执行|根据|然后", value):
        return None
    if not re.search(r"创建|新建|写入|写到|保存到|输出到|生成", value):
        return None
    match = re.search(r"内容(?:为|是|：|:)?\s*(.+?)\s*$", value, re.DOTALL)
    if match is None:
        return None
    target = _extract_explicit_output_paths(value)[0]
    if not target.lower().endswith(_TEXT_OUTPUT_SUFFIXES):
        return None
    content = match.group(1).strip().strip("`\"'")
    if not content:
        return None
    return target, content


class PlannerStage:
    """PLAN 阶段规划器。

    持有对 ExecutionOrchestrator 容器的反向引用
    （访问 _timings / _selector / _reference_resolver / _conversation_state / replan_count）。
    """

    def __init__(self, orchestrator):
        self._orch = orchestrator

    def _memory_view(self):
        session_context = getattr(self._orch, "session_context", None)
        return getattr(session_context, "memory_view", None)

    def _record_exchange(self, user_id: str, user_input: str, answer: str) -> None:
        memory_view = self._memory_view()
        if memory_view is not None:
            memory_view.record_full_exchange(user_input, answer)
        else:
            MemoryService.record_full_exchange(user_id, user_input, answer)

    def _record_resolution(
        self,
        user_id: str,
        utterance: str,
        resolved_target: str,
        kind: str,
    ) -> None:
        memory_view = self._memory_view()
        if memory_view is not None:
            memory_view.record_resolution(utterance, resolved_target, kind)
        else:
            MemoryService.record_resolution(user_id, utterance, resolved_target, kind)

    def _get_user_facts(self, user_id: str) -> str:
        memory_view = self._memory_view()
        if memory_view is not None:
            return memory_view.get_user_facts()
        return MemoryService.get_user_facts(user_id)

    async def run(
        self,
        user_input: str,
        user_id: str,
        context: Dict,
        repo_context: str,
        skill_hint: str,
    ) -> Tuple[AgentState, str, Optional[str]]:
        """PLAN 阶段：Build CognitiveContext → ReferenceResolver → IntentEngine → WorkflowRouter → Planner。

        Returns:
            (AgentState, runtime_state_next, best_answer_or_None)
        """
        t0 = time.perf_counter()

        # 构建 AgentState
        from agent.llm import llm
        now = datetime.now()
        system_content = self._orch._context_builder.render_context(context, now)
        if repo_context:
            system_content += f"\n\n相关代码片段:\n{repo_context}"
        skill = skill_registry.select(user_input)
        if skill:
            skill_prompt = skill.get_system_prompt()
            if skill_prompt:
                system_content += f"\n\n{skill_prompt}"
            skill_hint = skill.planner_hint

        planning_memory = "\n\n".join(
            part for part in (context.get("session", ""), context.get("short_term", ""))
            if part
        )
        state: AgentState = {
            "messages": [
                SystemMessage(content=system_content),
                HumanMessage(content=user_input),
            ],
            "plan": [],
            "current_task_index": 0,
            "artifacts": {},
            "memory_context": planning_memory,
            "repo_context": repo_context,
            "skill_hint": skill_hint,
            "retries": 0,
            "workflow": None,
            "execution_plans": [],
        }

        # ── Stage 0: 构建 CognitiveContext ──
        planner_context = self._orch._context_builder.build(
            user_input=user_input,
            user_id=user_id,
            context=context,
            repo_context=repo_context,
            state=state,
        )
        print(f"  🧠 规划上下文: {planner_context.short_summary()}")

        # ── Stage 0.5: ReferenceResolver 消歧（v1.2B：产出 ResolutionResult）──
        resolved = self._orch._reference_resolver.resolve(user_input, planner_context)
        planner_context.resolved_query = resolved.to_resolved_query()
        state["resolved_target"] = resolved.target or ""
        if resolved.resolution_trace:
            print(f"  🔍 引用消歧: {resolved.resolution_trace}")

        # ── Stage 1: IntentEngine 意图理解（消费 CognitiveContext）──
        intent = intent_engine.analyze(planner_context)
        print(f"  🧠 意图: {intent}")

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
            conversation_kind = _apply_conversation_contract(
                intent,
                user_id,
                user_input,
                pending_target=getattr(planner_context, "runtime_pending_target", ""),
                reference_target=resolved.target,
                conversation_retriever=session_retriever,
            )
            state["conversation_intent"] = conversation_kind.value
            retriever = session_retriever
            if retriever is None:
                from agent.compat.conversation import get_legacy_conversation_retriever
                retriever = get_legacy_conversation_retriever()
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

        # 更新跨轮对话状态（State = Cache：timeline 写入；last_* 为 Deprecated 双写）
        self._orch._context_builder.update_conversation_state(intent, resolution=resolved)

        # 记录跨会话解析事实（Memory Facts，v1.2C；不依赖 ResolutionResult 内部）
        try:
            if resolved and resolved.target:
                self._record_resolution(
                    user_id, user_input, resolved.target, resolved.kind,
                )
        except Exception:
            pass

        # 不要求执行（chat / translation / math / creation / identity 等）→ 直接 LLM 回答
        if not intent.requires_execution:
            if intent.action == "clarify":
                answer = (
                    "你提到的引用目标与当前未完成任务可能不是同一个对象。"
                    "请明确要继续当前未完成任务，还是继续你刚才提到的引用目标。"
                )
                self._record_exchange(user_id, user_input, answer)
                return state, "FINISH", answer
            if intent.domain == DOMAIN_MEMORY:
                _facts = ""
                try:
                    _facts = self._get_user_facts(user_id)
                except Exception:
                    pass
                if _facts:
                    system_content += (
                        "\n\n## 用户事实（回答个人/偏好问题时必须优先使用）\n"
                        f"{_facts}\n"
                        "规则：只能基于上述事实回答；事实中不存在则如实说明未记录，禁止编造。"
                    )
            # v2.1B-2（ADR-0013）：Conversation Reference Resolver —— 只注入对应字段
            try:
                from agent.conversation import (
                    resolve_reference_type,
                    ReferenceType, render_reference,
                )
                from agent.compat.conversation import get_legacy_conversation_retriever
                session_context = getattr(self._orch, "session_context", None)
                conversation_retriever = (
                    session_context.conversation_retriever
                    if session_context is not None
                    else get_legacy_conversation_retriever()
                )
                _ref_type = resolve_reference_type(intent)
                if _ref_type is ReferenceType.LAST_RUNTIME:
                    _cont = state.get("conversation_runtime_continuation", "") or ""
                    if _cont:
                        system_content += f"\n\n{_cont}"
                elif _ref_type is not ReferenceType.UNKNOWN:
                    _inj = render_reference(
                        conversation_retriever.snapshot(user_id), _ref_type)
                    if _inj:
                        system_content += f"\n\n{_inj}"
            except (AttributeError, ImportError, KeyError, TypeError) as exc:
                _record_contract_diagnostic(
                    state,
                    "conversation_reference_injection",
                    exc,
                    run_context=getattr(self._orch, "run_context", None),
                )
            try:
                response = await llm.ainvoke([
                    SystemMessage(content=system_content),
                    HumanMessage(content=user_input),
                ])
                answer = response.content if hasattr(response, 'content') else str(response)
            except Exception:
                answer = "抱歉，我暂时无法回答。"
            self._record_exchange(user_id, user_input, answer)
            return state, "FINISH", answer

        # Fresh research is a deterministic capability boundary. Build one
        # source-backed task directly instead of asking the general Planner to
        # invent an ``llm_executor`` search substitute.
        if getattr(intent, "freshness_required", False) or intent.action == "fresh_research":
            task_data = {
                "id": "task-1",
                "verb": "search",
                "target": user_input,
                "target_type": "text",
                "goal": "检索外部来源并返回带来源的研究结果",
                "description": "必须使用 web_search；不得依赖模型记忆替代外部检索。",
                "success_condition": "至少返回一个可核验来源，或明确报告检索不可用",
                "dependencies": [],
                "children": [],
                "inputs": {
                    "query": research_query(user_input),
                    "timeliness": research_timeliness(user_input),
                },
                "policy": {
                    "executor": "tool",
                    "tool_policy": {"allow": ["web_search"]},
                },
            }
            fresh_plan = _ensure_explicit_output_write_task(
                [task_data], user_input, intent,
            )
            canonical_plan = []
            fresh_execution_plans = []
            for task in fresh_plan:
                task_obj = Task.from_dict(task)
                canonical_plan.append(task_obj.to_dict())
                fresh_execution_plans.append(
                    self._orch._selector.compile(
                        task_obj,
                        context=CompilerContext(registry=_tool_registry),
                    )
                )
            state["plan"] = canonical_plan
            state["execution_plans"] = fresh_execution_plans
            state["current_task_index"] = 0
            self._print_plan(list(state.get("plan") or []))
            return state, "EXECUTE", None

        # A complete literal write is a deterministic world-state change.  Do
        # not spend Planner/LLM calls rediscovering the content already given
        # by the user; it still enters the canonical Compiler → Executor →
        # Verifier chain below.
        literal_write = _extract_literal_file_write(user_input)
        if literal_write and getattr(intent, "requires_execution", False):
            target, content = literal_write
            task_obj = Task.from_dict({
                "id": "task-1",
                "verb": Verb.WRITE.value,
                "target": target,
                "target_type": "file",
                "goal": f"将用户提供的内容写入 {target}",
                "description": "内容已由用户明确提供，无需再次生成。",
                "success_condition": f"文件 {target} 存在且非空",
                "dependencies": [],
                "children": [],
                "inputs": {"content": content},
            })
            compiled = self._orch._selector.compile(
                task_obj,
                context=CompilerContext(registry=_tool_registry),
            )
            state["plan"] = [task_obj.to_dict()]
            state["execution_plans"] = [compiled]
            state["current_task_index"] = 0
            self._print_plan(list(state.get("plan") or []))
            return state, "EXECUTE", None

        # ── Stage 2: WorkflowRouter 执行路由（接收完整 IntentResult）──
        wf_obj, wf_reason = workflow_router.route(intent)

        if wf_obj:
            wf_name = wf_obj.id if hasattr(wf_obj, 'id') else str(wf_obj)
            print(f"\n{'='*50}\n🚀 路由到 Workflow: {wf_name}\n{'='*50}")

            # 更新 ConversationState
            self._orch._conversation_state.last_workflow = wf_name

            if isinstance(wf_obj, Workflow):
                # Canonical Workflow → WorkflowExecutor
                state["workflow"] = wf_name
                ctx = ExecutionContext(
                    workflow_id=wf_obj.id,
                    user_input=user_input,
                    task=Task(
                        id="root",
                        verb=Verb.READ,
                        target="",
                        goal=user_input,
                    ),
                )
                path_match = re.search(r'input/([\w.]+)', user_input)
                qpath = f"input/{path_match.group(1)}" if path_match else "input/question.docx"
                ctx.set_artifact(Artifact(id="p", type="question_path", content=qpath, summary=qpath))
                output_path = _extract_workflow_output_path(user_input)
                ctx.set_artifact(Artifact(
                    id="output-path",
                    type="output_path",
                    content=output_path,
                    summary=output_path,
                ))

                try:
                    result = await WorkflowExecutor().execute(wf_obj, ctx)
                except Exception as exc:
                    print(f"  ⚠️ 专用 Workflow 异常，降级到通用 Planner: {str(exc)[:160]}")
                    result = None
                self._orch._timings["plan_llm"] = round(time.perf_counter() - t0, 3)

                if result and result.success and result.outputs.get("_summary"):
                    best_answer = result.outputs["_summary"]
                    self._record_exchange(user_id, user_input, best_answer)
                    return state, "FINISH", best_answer
                else:
                    reason = result.error[:160] if result else "Workflow 异常"
                    print(f"  ⚠️ 专用 Workflow 未完成（{reason}），降级到通用 Planner")
                    return await self._fallback_to_generic_planner(
                        user_input=user_input,
                        context=context,
                        repo_context=repo_context,
                        skill_hint=skill_hint,
                        planner_context=planner_context,
                        intent=intent,
                        state=state,
                        planning_memory=planning_memory,
                        started_at=t0,
                    )
            else:
                raise TypeError(
                    f"WorkflowRegistry 只能返回 canonical Workflow，收到 {type(wf_obj).__name__}"
                )
        else:
            # 未命中 Workflow → Planner 兜底（带契约校验 + Retry + Grounding，PR-7）
            print(f"\n📋 无匹配 Workflow（{wf_reason}），使用 Planner 生成计划。")
            # ── Grounding：缩小 Planner 搜索空间（基于 intent 检索键）──
            grounding_ctx = None
            try:
                from agent.grounding import Grounder, GroundingInput
                ws_ctx = planner_context.workspace
                grounding_ctx = Grounder().ground(GroundingInput(
                    query=user_input,
                    intent=intent,
                    current_file=getattr(ws_ctx, "current_file", "") or "",
                    opened_files=list(getattr(ws_ctx, "opened_files", []) or []),
                    workspace=(
                        getattr(getattr(self._orch, "run_context", None), "workspace", None)
                    ),
                )).context
            except Exception as e:
                print(f"  ⚠️ Grounding 失败（忽略）: {e}")

            plan = None
            execution_plans: list[ExecutionPlan] = []
            last_error = ""
            planner_input = user_input
            for attempt in range(3):
                try:
                    plan_output = await plan_with_metadata(
                        planner_input, planning_memory,
                        repo_context, skill_hint, None,
                        grounding=grounding_ctx,
                    )
                    plan = plan_output.tasks
                    plan = _ensure_explicit_output_write_task(
                        plan, user_input, intent,
                    )
                    for i, t in enumerate(plan):
                        t.setdefault("id", f"task-{i+1}")
                        t.setdefault("status", "pending")
                        t.setdefault("observations", [])
                        t.setdefault("error", "")
                        t.setdefault("children", [])
                        t.setdefault("description", "")
                        t.setdefault("dependencies", [])
                    # 追加写入：显式"追加"请求 → 写任务 inputs.mode=append（确定性）
                    if any(tok in user_input.lower() for tok in ("追加", "附加", "append")):
                        for _t in plan:
                            if _t.get("verb") == "write":
                                _t.setdefault("inputs", {})["mode"] = "append"
                    self._print_plan(plan)

                    # 契约校验 + 编译（编译期错误 → retry）
                    execution_plans = []
                    for t in plan:
                        task_obj = Task.from_dict(t)
                        ep = self._orch._selector.compile(
                            task_obj,
                            context=CompilerContext(
                                registry=_tool_registry,
                            ),
                        )
                        execution_plans.append(ep)
                    break
                except Exception as e:
                    last_error = str(e)
                    print(f"  ⚠️ Planner 输出不合法（{attempt+1}/3）: {last_error}")
                    planner_input = (
                        f"你上一次输出的计划不合法，需要修正后重新输出。\n"
                        f"错误：{last_error}\n"
                        f"规则：verb 必须是 read/write/modify/execute/search/list/explain/delete/move/resolve；\n"
                        f"target_type=file 时 target 必须是具体文件路径（禁止中文描述）；\n"
                        f"示例：{{\"verb\": \"read\", \"target\": \"output/solution.py\", \"target_type\": \"file\"}}\n\n"
                        f"原始需求：{user_input}"
                    )

            if plan is None:
                print("❌ Planner 连续 3 次输出不合法，任务失败（PlanningFailure，不进入执行链）")
                return state, "FAIL", None

            # v2.0-A Abstention：Planner 信息不足 → 不猜测，向用户澄清（Uncertainty 横切）
            if not plan:
                print("⚠️ Planner 信息不足（Abstain，不猜测）—— 需要向用户澄清。")
                return state, "FINISH", (
                    "信息不足，需要澄清：请提供具体的目标（如文件路径或模块名），"
                    "以便我制定执行计划。"
                )

            state["execution_plans"] = execution_plans
            state["plan"] = plan
            state["current_task_index"] = 0
            self._orch._timings["plan_llm"] = round(time.perf_counter() - t0, 3)
            return state, "EXECUTE", None

    async def _fallback_to_generic_planner(
        self,
        user_input: str,
        context: Dict,
        repo_context: str,
        skill_hint: str,
        planner_context,
        intent,
        state: AgentState,
        planning_memory: str,
        started_at: float,
    ) -> Tuple[AgentState, str, Optional[str]]:
        """Recover from a specialized workflow failure without false success."""
        grounding_ctx = None
        try:
            from agent.grounding import Grounder, GroundingInput

            ws_ctx = planner_context.workspace
            grounding_ctx = Grounder().ground(GroundingInput(
                query=user_input,
                intent=intent,
                current_file=getattr(ws_ctx, "current_file", "") or "",
                opened_files=list(getattr(ws_ctx, "opened_files", []) or []),
                workspace=(
                    getattr(getattr(self._orch, "run_context", None), "workspace", None)
                ),
            )).context
        except Exception as exc:
            print(f"  ⚠️ 通用 Planner Grounding 失败（忽略）: {str(exc)[:120]}")

        fallback_input = (
            "专用工作流未完成。请改用通用任务计划，直接围绕用户原始需求规划，"
            "不要使用 code_generation 工作流，也不要声称未验证的文件已写入。\n"
            f"原始需求：{user_input}"
        )
        plan = None
        execution_plans: list[ExecutionPlan] = []
        for attempt in range(2):
            try:
                plan_output = await plan_with_metadata(
                    fallback_input,
                    planning_memory,
                    repo_context,
                    skill_hint,
                    None,
                    grounding=grounding_ctx,
                )
                plan = plan_output.tasks
                plan = _ensure_explicit_output_write_task(
                    plan, user_input, intent,
                )
                for i, task_data in enumerate(plan):
                    task_data.setdefault("id", f"task-{i + 1}")
                    task_data.setdefault("status", "pending")
                    task_data.setdefault("observations", [])
                    task_data.setdefault("error", "")
                    task_data.setdefault("children", [])
                    task_data.setdefault("description", "")
                    task_data.setdefault("dependencies", [])
                execution_plans = [
                    self._orch._selector.compile(
                        Task.from_dict(task_data),
                        context=CompilerContext(registry=_tool_registry),
                    )
                    for task_data in plan
                ]
                break
            except Exception as exc:
                print(f"  ⚠️ 通用 Planner 输出不合法（{attempt + 1}/2）: {str(exc)[:160]}")
                plan = None

        if not plan:
            return state, "FINISH", "任务未完成：专用工作流失败，通用 Planner 也未能生成可执行计划。"

        self._print_plan(plan)
        state["execution_plans"] = execution_plans
        state["plan"] = plan
        state["current_task_index"] = 0
        self._orch._timings["plan_llm"] = round(time.perf_counter() - started_at, 3)
        return state, "EXECUTE", None

    # ── REPLAN ──

    async def replan(
        self,
        state: AgentState,
        user_input: str,
        user_id: str,
    ) -> Tuple[AgentState, str]:
        """REPLAN 阶段：重规划失败任务。"""
        current_plan = state.get("plan") or []
        deterministic_failure = next(
            (
                str(task.get("error_code", ""))
                or classify_execution_error(task.get("error", ""))
                for task in current_plan
                if task.get("status") == "failed"
                and (
                    str(task.get("error_code", ""))
                    or classify_execution_error(task.get("error", ""))
                )
            ),
            "",
        )
        if deterministic_failure and is_non_retriable(deterministic_failure):
            state["runtime_failure_code"] = deterministic_failure
            state["runtime_terminal_status"] = (
                "BLOCKED"
                if deterministic_failure == "RESEARCH_TOOL_UNAVAILABLE"
                else "FAILED_TERMINAL"
            )
            print(
                f"❌ 确定性错误 {deterministic_failure}，停止重规划"
            )
            return state, "FAIL"

        if self._orch.replan_count >= 2:
            print("❌ 达到最大重试次数")
            return state, "FAIL"

        self._orch.replan_count += 1
        state["retries"] = self._orch.replan_count
        print(f"\n⚠️ 任务失败，重规划 ({self._orch.replan_count}/2)...")

        t_replan = time.perf_counter()
        failed_info = [
            f"- {t['id']}: {t.get('goal', '?')} (错误: {t.get('error', '?')})"
            for t in state.get("plan", [])
            if t.get("status") == "failed"
        ]
        replan_input = (
            f"原始需求: {user_input}\n\n以下任务执行失败，需要重新规划：\n"
            + "\n".join(failed_info)
        )
        new_plan = (await plan_with_metadata(replan_input, "", "", "", None)).tasks
        new_plan = _ensure_explicit_output_write_task(new_plan, user_input)
        for t in new_plan:
            t.setdefault("status", "pending")
            t.setdefault("observations", [])
            t.setdefault("error", "")
        if any(tok in user_input.lower() for tok in ("追加", "附加", "append")):
            for _t in new_plan:
                if _t.get("verb") == "write":
                    _t.setdefault("inputs", {})["mode"] = "append"


        preserved_facts = {}
        for t in state.get("plan", []):
            if t.get("status") == "succeeded":
                preserved_facts.update(t.get("facts", {}))

        old_unfinished = [
            t for t in state.get("plan", [])
            if t.get("status") in ("pending", "running")
        ]
        for t in new_plan:
            t.setdefault("facts", {})
            t["facts"].update(preserved_facts)

        # 重新生成 ExecutionPlan
        execution_plans: list[ExecutionPlan] = []
        for t in new_plan:
            task_obj = Task.from_dict(t)
            ep = self._orch._selector.compile(
                task_obj,
                context=CompilerContext(
                    registry=_tool_registry,
                ),
            )
            execution_plans.append(ep)

        state["execution_plans"] = execution_plans + state.get("execution_plans", [])[len(old_unfinished):]
        state["plan"] = old_unfinished + new_plan
        state["current_task_index"] = 0
        print(f"  🔄 重新规划，共 {len(state['plan'])} 个任务（保留 {len(preserved_facts)} 个 Facts）")
        self._orch._timings["replan_llm"] = round(time.perf_counter() - t_replan, 3)
        return state, "EXECUTE"

    # ── 辅助 ──

    def _print_plan(self, tasks: list) -> None:
        print("\n" + "=" * 60)
        print("📋 执行计划（Task 分解）")
        print("=" * 60)
        for task in tasks:
            tid = task.get("id", "?")
            verb = task.get("verb", "")
            target = task.get("target", "")
            goal = task.get("goal", "?")
            if verb and target:
                label = f"{verb} {target}"
            else:
                label = goal
            dep = (
                f" (依赖: {task.get('dependencies', [])})"
                if task.get("dependencies")
                else ""
            )
            children = task.get("children", [])
            child_cnt = f" [{len(children)} 子任务]" if children else ""
            print(f"  {tid}: {label}{dep}{child_cnt}")
        print("=" * 60 + "\n")
