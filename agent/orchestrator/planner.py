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
from agent.task import Task, Verb
from agent.compiler.context import CompilerContext
from agent.registry.tool_registry import registry as _tool_registry
from agent.cognition.intent_schema import DOMAIN_MEMORY


def _extract_workflow_output_path(user_input: str) -> str:
    """Extract an explicit output file for the question-code workflow."""
    candidates = re.findall(
        r"(?<![\w/])(?:[\w.-]+/)*[\w.-]+\.(?:py|txt|csv|json|xlsx|xls|docx|pptx)",
        user_input or "",
        flags=re.IGNORECASE,
    )
    for candidate in reversed(candidates):
        normalized = candidate.replace("\\", "/")
        if normalized.lower().startswith("input/") or normalized.lower().endswith("question.docx"):
            continue
        return normalized
    return "output/solution.py"


class PlannerStage:
    """PLAN 阶段规划器。

    持有对 ExecutionOrchestrator 容器的反向引用
    （访问 _timings / _selector / _reference_resolver / _conversation_state / replan_count）。
    """

    def __init__(self, orchestrator):
        self._orch = orchestrator

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
        if resolved.resolution_trace:
            print(f"  🔍 引用消歧: {resolved.resolution_trace}")

        # ── Stage 1: IntentEngine 意图理解（消费 CognitiveContext）──
        intent = intent_engine.analyze(planner_context)
        print(f"  🧠 意图: {intent}")

        # 更新跨轮对话状态（State = Cache：timeline 写入；last_* 为 Deprecated 双写）
        self._orch._context_builder.update_conversation_state(intent, resolution=resolved)

        # 记录跨会话解析事实（Memory Facts，v1.2C；不依赖 ResolutionResult 内部）
        try:
            if resolved and resolved.target:
                MemoryService.record_resolution(
                    user_id, user_input, resolved.target, resolved.kind,
                )
        except Exception:
            pass

        # 不要求执行（chat / translation / math / creation / identity 等）→ 直接 LLM 回答
        if not intent.requires_execution:
            if intent.domain == DOMAIN_MEMORY:
                _facts = ""
                try:
                    _facts = MemoryService.get_user_facts(user_id)
                except Exception:
                    pass
                if _facts:
                    system_content += (
                        "\n\n## 用户事实（回答个人/偏好问题时必须优先使用）\n"
                        f"{_facts}\n"
                        "规则：只能基于上述事实回答；事实中不存在则如实说明未记录，禁止编造。"
                    )
            try:
                response = await llm.ainvoke([
                    SystemMessage(content=system_content),
                    HumanMessage(content=user_input),
                ])
                answer = response.content if hasattr(response, 'content') else str(response)
            except Exception:
                answer = "抱歉，我暂时无法回答。"
            MemoryService.record_full_exchange(user_id, user_input, answer)
            return state, "FINISH", answer

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
                    MemoryService.record_full_exchange(user_id, user_input, best_answer)
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
                )).context
            except Exception as e:
                print(f"  ⚠️ Grounding 失败（忽略）: {e}")

            plan = None
            execution_plans = []
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
            )).context
        except Exception as exc:
            print(f"  ⚠️ 通用 Planner Grounding 失败（忽略）: {str(exc)[:120]}")

        fallback_input = (
            "专用工作流未完成。请改用通用任务计划，直接围绕用户原始需求规划，"
            "不要使用 code_generation 工作流，也不要声称未验证的文件已写入。\n"
            f"原始需求：{user_input}"
        )
        plan = None
        execution_plans = []
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
        execution_plans = []
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
