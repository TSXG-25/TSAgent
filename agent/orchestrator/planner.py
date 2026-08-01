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
    ToolSelector.compile（Task → ExecutionPlan）

REPLAN：保留成功任务的 Facts，重新规划失败任务。

Phase C.1：从 orchestrator.py 的 plan() / replan() / _dict_to_task() / _print_plan() 迁移。
"""
import re
import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple
from langchain_core.messages import SystemMessage, HumanMessage

from agent.state import AgentState
from agent.executor.executors.workflow import WorkflowExecutor
from agent.planner.planner import generate_plan
from agent.services import (
    MemoryService, RepositoryService, ArtifactService,
)
from agent.services.workspace_service import get_workspace_service
from agent.registry.skill_registry import skill_registry
from agent.router.workflow_router import router as workflow_router
from agent.cognition.intent_engine import engine as intent_engine
from agent.cognition.intent_schema import IntentResult, DOMAIN_CHAT
from agent.workflow import ExecutionContext, Artifact
from agent.task import Task, Verb, ExecutionPlan


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

        state: AgentState = {
            "messages": [
                SystemMessage(content=system_content),
                HumanMessage(content=user_input),
            ],
            "plan": [],
            "current_task_index": 0,
            "artifacts": {},
            "memory_context": context.get("short_term", ""),
            "repo_context": repo_context,
            "skill_hint": skill_hint,
            "retries": 0,
            "workflow": None,
            "execution_plans": [],
        }

        # ── Stage 0: 构建 CognitiveContext ──
        cognitive_context = self._orch._context_builder.build(
            user_input=user_input,
            user_id=user_id,
            context=context,
            repo_context=repo_context,
            state=state,
        )
        print(f"  🧠 认知上下文: {cognitive_context.short_summary()}")

        # ── Stage 0.5: ReferenceResolver 消歧 ──
        resolved = self._orch._reference_resolver.resolve(user_input, cognitive_context)
        cognitive_context.resolved_query = resolved
        if resolved.resolution_trace:
            print(f"  🔍 引用消歧: {resolved.resolution_trace}")

        # ── Stage 1: IntentEngine 意图理解（消费 CognitiveContext）──
        intent = intent_engine.analyze(cognitive_context)
        print(f"  🧠 意图: {intent}")

        # 更新跨轮对话状态
        self._orch._context_builder.update_conversation_state(intent)

        # 闲聊/无意义输入 → 直接 LLM 回答
        if intent.is_chat or not intent.requires_execution:
            if intent.is_chat:
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

        # ── Stage 1.5: Workspace 解析 target（Planner 前介入） ──
        resolved_target = None
        if intent.has_target:
            try:
                ws = get_workspace_service()
                matches = ws.resolve(intent.target)
                if matches:
                    best = matches[0]
                    resolved_target = best.path if hasattr(best, 'path') else str(best)
                    print(f"  🎯 Workspace 解析 target: {intent.target} → {resolved_target}")
                else:
                    resolved_target = intent.target
                    print(f"  🎯 target 未在 Workspace 找到匹配，使用原始值: {resolved_target}")
            except Exception as e:
                print(f"  ⚠️ Workspace 解析失败: {e}，使用原始 target")
                resolved_target = intent.target
        else:
            resolved_target = None

        # ── Stage 2: WorkflowRouter 执行路由（接收完整 IntentResult）──
        wf_obj, wf_reason = workflow_router.route(intent)

        if wf_obj:
            wf_name = wf_obj.id if hasattr(wf_obj, 'id') else str(wf_obj)
            print(f"\n{'='*50}\n🚀 路由到 Workflow: {wf_name}\n{'='*50}")

            # 更新 ConversationState
            self._orch._conversation_state.last_workflow = wf_name

            if hasattr(wf_obj, 'stages'):
                # 新式 Workflow（stages）→ WorkflowExecutor v2
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

                result = await WorkflowExecutor().execute(wf_obj, ctx)
                self._orch._timings["plan_llm"] = round(time.perf_counter() - t0, 3)

                if result.success and result.outputs.get("_summary"):
                    best_answer = result.outputs["_summary"]
                    MemoryService.record_full_exchange(user_id, user_input, best_answer)
                    return state, "FINISH", best_answer
                else:
                    best_answer = f"工作流执行{'成功' if result.success else '失败'}"
                    if result.error:
                        best_answer += f"：{result.error[:100]}"
                    MemoryService.record_full_exchange(user_id, user_input, best_answer)
                    return state, "FINISH", best_answer
            else:
                # 旧式 async 函数 → 获取 plan（传入 resolved_target）
                wf_kwargs = {"memory_context": context.get("short_term", "")}
                if resolved_target:
                    wf_kwargs["resolved_target"] = resolved_target
                plan = await wf_obj(user_input, **wf_kwargs)
                for i, t in enumerate(plan):
                    t.setdefault("id", f"task-{i+1}")
                    t.setdefault("status", "pending")
                    t.setdefault("observations", [])
                    t.setdefault("error", "")
                    t.setdefault("children", [])
                    t.setdefault("description", "")
                    t.setdefault("dependencies", [])
                    t.setdefault("verb", "")
                    t.setdefault("target", "")
                self._print_plan(plan)

                # 同样走 ToolSelector（确保 Workflow 输出的 plan 也受益）
                ws_service = None
                try:
                    ws_service = get_workspace_service()
                except Exception:
                    pass
                execution_plans = []
                for t in plan:
                    task_obj = self._dict_to_task(t)
                    try:
                        ep = self._orch._selector.select(task_obj, workspace=ws_service)
                    except Exception:
                        ep = ExecutionPlan(task=task_obj)
                    execution_plans.append(ep)
                state["execution_plans"] = execution_plans

                state["plan"] = plan
                state["current_task_index"] = 0
                self._orch._timings["plan_llm"] = round(time.perf_counter() - t0, 3)
                return state, "EXECUTE", None
        else:
            # 未命中 Workflow → Planner 兜底
            print(f"\n📋 无匹配 Workflow（{wf_reason}），使用 Planner 生成计划。")
            plan = await generate_plan(
                user_input, context.get("short_term", ""),
                repo_context, skill_hint, None,
            )
            for i, t in enumerate(plan):
                t.setdefault("id", f"task-{i+1}")
                t.setdefault("status", "pending")
                t.setdefault("observations", [])
                t.setdefault("error", "")
                t.setdefault("children", [])
                t.setdefault("description", "")
                t.setdefault("dependencies", [])
            self._print_plan(plan)

            # ── Stage 4: ToolSelector 将 Task dict → ExecutionPlan ──
            ws_service = None
            try:
                ws_service = get_workspace_service()
            except Exception:
                pass

            execution_plans = []
            for t in plan:
                task_obj = self._dict_to_task(t)
                try:
                    ep = self._orch._selector.select(task_obj, workspace=ws_service)
                except Exception as e:
                    print(f"  ⚠️ ToolSelector 失败 ({t.get('id', '?')}: {e})，使用空 plan")
                    ep = ExecutionPlan(task=task_obj)
                execution_plans.append(ep)

            state["execution_plans"] = execution_plans
            state["plan"] = plan
            state["current_task_index"] = 0
            self._orch._timings["plan_llm"] = round(time.perf_counter() - t0, 3)
            return state, "EXECUTE", None

            state["execution_plans"] = execution_plans
            state["plan"] = plan
            state["current_task_index"] = 0
            self._orch._timings["plan_llm"] = round(time.perf_counter() - t0, 3)
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
        new_plan = await generate_plan(replan_input, "", "", "", None)
        for t in new_plan:
            t.setdefault("status", "pending")
            t.setdefault("observations", [])
            t.setdefault("error", "")

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
        ws_service = None
        try:
            ws_service = get_workspace_service()
        except Exception:
            pass

        execution_plans = []
        for t in new_plan:
            task_obj = self._dict_to_task(t)
            try:
                ep = self._orch._selector.select(task_obj, workspace=ws_service)
            except Exception:
                ep = ExecutionPlan(task=task_obj)
            execution_plans.append(ep)

        state["execution_plans"] = execution_plans + state.get("execution_plans", [])[len(old_unfinished):]
        state["plan"] = old_unfinished + new_plan
        state["current_task_index"] = 0
        print(f"  🔄 重新规划，共 {len(state['plan'])} 个任务（保留 {len(preserved_facts)} 个 Facts）")
        self._orch._timings["replan_llm"] = round(time.perf_counter() - t_replan, 3)
        return state, "EXECUTE"

    # ── Task 转换 ──

    def _dict_to_task(self, d: dict) -> Task:
        """将 Planner 输出的 dict 转为 Task 对象。

        支持新旧两种格式：
        - 新格式: {"verb": "read", "target": "solution.py", "goal": "..."}
        - 旧格式: {"goal": "读取 solution.py"} → 从 goal 提取 verb+target
        """
        goal = d.get("goal", "")

        # 尝试直接读取 verb/target（新格式）
        verb_str = d.get("verb", "")
        target = d.get("target", "")

        # 如果是旧格式（无 verb/target），从 goal 提取
        if not verb_str or not target:
            if goal:
                verb_hints = {
                    "read": ["读取", "读", "阅读", "打开", "查看", "read"],
                    "write": ["写入", "写", "创建", "输出", "保存", "write", "create"],
                    "modify": ["修改", "编辑", "更新", "更改", "重构", "优化", "modify", "edit", "update", "optimize", "refactor"],
                    "execute": ["运行", "执行", "run", "execute"],
                    "search": ["搜索", "查找", "查询", "search", "find"],
                    "list": ["列出", "列表", "浏览", "list"],
                    "explain": ["解释", "说明", "分析", "总结", "explain", "analyze"],
                    "design": ["设计", "规划", "design", "plan"],
                    "verify": ["验证", "测试", "检查", "verify", "test", "check"],
                }
                goal_lower = goal.lower()
                for v, hints in verb_hints.items():
                    if any(h in goal_lower for h in hints):
                        verb_str = v
                        break
                if not verb_str:
                    verb_str = "read"  # 默认

                # 从 goal 提取 target：优先找文件名
                if not target:
                    file_match = re.search(r'[\w./\\-]+\.\w+', goal, re.ASCII)
                    if file_match:
                        target = file_match.group(0)
                    else:
                        target = ""
            else:
                verb_str = verb_str or "read"
                target = target or ""

        # 解析 verb
        try:
            verb = Verb(verb_str.lower())
        except ValueError:
            verb = Verb.READ

        return Task(
            id=d.get("id", "task-1"),
            verb=verb,
            target=target,
            goal=goal,
            dependencies=d.get("dependencies", []),
            status=d.get("status", "pending"),
        )

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

