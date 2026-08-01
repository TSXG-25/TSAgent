"""ExecutionOrchestrator — 执行编排层。

Runtime 只做状态机迁移。
Orchestrator 负责任务编排：
- PLAN 阶段：构建 CognitiveContext → ReferenceResolver → IntentEngine → WorkflowRouter → Planner/ToolSelector
- EXECUTE 阶段：调用 Dispatcher → ToolExecutor / ReActExecutor
- 后处理：调用 AnswerGenerator / Memory Commit

认知链路（新）：
    User Input
       │
       ▼
    Build CognitiveContext
       │  ├── MemoryService (conversation, session, preferences)
       │  ├── WorkspaceService (current_file, opened_files, current_symbol)
       │  ├── ConversationState (last_file, last_symbol, last_intent)
       │  └── RepositoryService / Task / Artifacts
       │
       ▼
    ReferenceResolver.resolve(context)
       │  确定性消歧 → LLM 1-shot → ResolvedQuery
       │
       ▼
    IntentEngine.analyze(context)
       │  关键词匹配 → LLM 1-shot → IntentResult
       │
       ▼
    WorkflowRouter.route(intent)
       │  条件路由 → domain+action 路由 → domain 兜底
       │
       ▼
    Planner / ToolSelector / Executor
"""
import time
import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple
from langchain_core.messages import SystemMessage, HumanMessage

from agent.state import AgentState
from agent.executor.executors.react import ReactExecutor
from agent.executor.executors.workflow import WorkflowExecutor
from agent.planner.planner import generate_plan
from agent.answer_generator import generate_final_answer
from agent.services import (
    MemoryService, WorkflowService, EventService,
    RepositoryService, ArtifactService,
)
from agent.services.workspace_service import get_workspace_service, WorkspaceService
from agent.registry.skill_registry import skill_registry
from agent.router.workflow_router import router as workflow_router
from agent.cognition.cognitive_context import CognitiveContext, ConversationState, ResolvedQuery
from agent.cognition.reference_resolver import ReferenceResolver
from agent.cognition.intent_engine import engine as intent_engine
from agent.cognition.intent_schema import IntentResult, DOMAIN_CHAT
from agent.bootstrap import print_timings as print_bootstrap_timings
from agent.workflow import ExecutionContext, Artifact
from agent.workflow.budget import BudgetSpec, BudgetManager
from agent.workflow.node import Node, NodeGraph
from agent.query_normalizer import QueryNormalizer
from agent.task import Task, Verb, ExecutionPlan, ExecutionStep
from agent.selector.tool_selector import ToolSelector
from agent.selector.rules import DEFAULT_RULES
from agent.executor.contract import executor_factory

MAX_REPLAN = 2


class ExecutionOrchestrator:
    """执行编排器。

    Runtime 调用 Orchestrator 来处理每个阶段。
    Orchestrator 不知道状态机，只返回处理结果。
    """

    def __init__(self):
        self._timings: Dict[str, float] = {}
        self.replan_count = 0
        # 初始化 ToolSelector（注册所有规则）
        self._selector = ToolSelector()
        for rule in DEFAULT_RULES:
            self._selector.add_rule(rule)

        # 认知层组件
        self._reference_resolver = ReferenceResolver()
        self._conversation_state = ConversationState()

    def reset_timings(self):
        self._timings = {}

    def get_timings(self) -> Dict[str, float]:
        return dict(self._timings)

    # ── 认知上下文构建 ──

    def _build_cognitive_context(
        self,
        user_input: str,
        user_id: str,
        context: dict,
        repo_context: str,
        state: AgentState,
    ) -> CognitiveContext:
        """构建 CognitiveContext（认知层统一入口）。

        从 MemoryService、WorkspaceService、RepositoryService 等来源聚合数据。
        CognitiveContext 是纯数据容器，下游模块不 import 任何 Service。
        """
        # Workspace 上下文
        ws_context = None
        try:
            ws = get_workspace_service()
            ws_context = ws.current_context()
        except Exception:
            pass

        # 从 MemoryService 获取最近对话
        conversation = []
        try:
            session_text = MemoryService.get_session_context(user_id, n=8)
            if session_text:
                # 解析为结构化列表（简化：role/content 对）
                # 实际生产环境应使用 MemoryService 的结构化接口
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
        current_task = None
        task_idx = state.get("current_task_index", 0)
        if plan and task_idx < len(plan):
            current_task = plan[task_idx]

        # Artifacts
        artifacts = state.get("artifacts", {})

        # Memory（偏好、事实）
        memory = {
            "preferences": context.get("preferences", ""),
            "facts": context.get("facts", ""),
        }

        return CognitiveContext(
            query=user_input,
            conversation=conversation,
            conversation_state=self._conversation_state,
            workspace=ws_context,
            plan=plan,
            task=current_task,
            repository_context=repo_context,
            memory=memory,
            artifacts=artifacts,
        )

    def _update_conversation_state(self, intent: IntentResult) -> None:
        """在意图理解后更新跨轮对话状态。"""
        if intent.target:
            self._conversation_state.last_file = intent.target
            self._conversation_state.last_target = intent.target
        if intent.entities:
            # 如果 entities 中有符号名（驼峰命名），设为 last_symbol
            for entity in intent.entities:
                if entity and entity[0].isupper():
                    self._conversation_state.last_symbol = entity
                    break
        self._conversation_state.last_domain = intent.domain
        self._conversation_state.last_action = intent.action

    # ── PLAN 阶段编排 ──

    async def plan(
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
        system_content = self._render_context(context, now)
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
        cognitive_context = self._build_cognitive_context(
            user_input=user_input,
            user_id=user_id,
            context=context,
            repo_context=repo_context,
            state=state,
        )
        print(f"  🧠 认知上下文: {cognitive_context.short_summary()}")

        # ── Stage 0.5: ReferenceResolver 消歧 ──
        resolved = self._reference_resolver.resolve(user_input, cognitive_context)
        cognitive_context.resolved_query = resolved
        if resolved.resolution_trace:
            print(f"  🔍 引用消歧: {resolved.resolution_trace}")

        # ── Stage 1: IntentEngine 意图理解（消费 CognitiveContext）──
        intent = intent_engine.analyze(cognitive_context)
        print(f"  🧠 意图: {intent}")

        # 更新跨轮对话状态
        self._update_conversation_state(intent)

        # 闲聊/无意义输入 → 直接 LLM 回答
        if intent.is_chat or not intent.requires_execution:
            if intent.is_chat:
                try:
                    response = await llm.ainvoke([
                        SystemMessage(content=system_content),
                        HumanMessage(content=user_input),
                    ])
                    answer = response.content if hasattr(response, 'content') else str(response)
                except Exception as e:
                    answer = f"抱歉，我暂时无法回答。"
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
            self._conversation_state.last_workflow = wf_name

            if hasattr(wf_obj, 'stages'):
                # 新式 Workflow（stages）→ WorkflowExecutor
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
                self._timings["plan_llm"] = round(time.perf_counter() - t0, 3)

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
                        ep = self._selector.select(task_obj, workspace=ws_service)
                    except Exception:
                        ep = ExecutionPlan(task=task_obj)
                    execution_plans.append(ep)
                state["execution_plans"] = execution_plans

                state["plan"] = plan
                state["current_task_index"] = 0
                self._timings["plan_llm"] = round(time.perf_counter() - t0, 3)
                return state, "EXECUTE", None
        else:
            # 未命中 Workflow → Planner 兜底
            print(f"\n📋 无匹配 Workflow（{wf_reason}），使用 Planner 生成计划。")
            # Planner Prompt 可以注入 cognitive_context
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
                    ep = self._selector.select(task_obj, workspace=ws_service)
                except Exception as e:
                    print(f"  ⚠️ ToolSelector 失败 ({t.get('id', '?')}: {e})，使用空 plan")
                    ep = ExecutionPlan(task=task_obj)
                execution_plans.append(ep)

            state["execution_plans"] = execution_plans
            state["plan"] = plan
            state["current_task_index"] = 0
            self._timings["plan_llm"] = round(time.perf_counter() - t0, 3)
            return state, "EXECUTE", None

    # ── EXECUTE 阶段编排 ──

    async def execute(self, state: AgentState) -> Tuple[AgentState, str]:
        """EXECUTE 阶段：通过 Dispatcher 分发执行。

        对于每个 Task：
        - Dispatcher 判断 → ToolExecutor（确定性）或 ReActExecutor（开放式）
        - ToolExecutor 执行 ExecutionPlan.steps
        - ReActExecutor 执行传统 ReAct Loop
        """
        t_exec = time.perf_counter()
        tasks = state.get("plan", [])
        execution_plans = state.get("execution_plans", [])

        if not execution_plans:
            # 无 ExecutionPlan（旧 Workflow 路径），走老 ReAct
            executor = ReactExecutor()
            state = await executor.execute(state, tasks)
        else:
            # 新路径：Compiler 已决定执行器（plan.executor），ExecutorFactory 分发
            for idx, task_dict in enumerate(tasks):
                task_obj = self._dict_to_task(task_dict) if idx < len(tasks) else None
                plan = execution_plans[idx] if idx < len(execution_plans) else None

                if plan is not None and plan.executor == "tool":
                    # Phase B.2: ToolExecutor（确定性 ExecutionPlan 步骤序列）
                    print(f"  🔀 Compiler: {task_dict.get('id', '?')} → tool_executor")
                    ws_service = None
                    try:
                        ws_service = get_workspace_service()
                    except Exception:
                        pass
                    context = ExecutionContext(task=task_obj, variables={})
                    if ws_service:
                        context.set_var("workspace", ws_service)
                    context.set_var("execution_plan", plan)

                    tool_executor = executor_factory.get("tool")
                    exec_result = await tool_executor.execute(task_obj, context)

                    if not exec_result.success:
                        task_dict["status"] = "failed"
                        task_dict["error"] = exec_result.error
                    else:
                        task_dict["status"] = "succeeded"
                        exec_meta = exec_result.metadata or {}
                        task_dict["observations"].append({
                            "action": "tool_executor",
                            "tool": "tool_executor",
                            "status": "succeeded",
                            "summary": exec_result.text[:300],
                            "artifact_ids": [],
                            "time_s": round(exec_meta.get("time_s", 0), 2),
                        })
                        # 保存变量供后续任务使用
                        task_dict["facts"] = exec_meta.get("variables", {})
                else:
                    # ReActExecutor 执行开放式任务（Phase B.3 迁移到统一契约）
                    print(f"  🔀 Compiler: {task_dict.get('id', '?')} → react_executor")
                    executor = ReactExecutor()
                    sub_state = await executor.execute(state, [task_dict])
                    # 合并回主 state
                    updated = sub_state.get("plan", [])
                    if updated:
                        task_dict["status"] = updated[0].get("status", "failed")
                        task_dict["observations"] = updated[0].get("observations", [])
                        task_dict["error"] = updated[0].get("error", "")
                        task_dict["facts"] = updated[0].get("facts", {})

        self._timings["executor"] = round(time.perf_counter() - t_exec, 3)

        # 检查结果
        failed = [t for t in state.get("plan", []) if t.get("status") == "failed"]
        if not failed:
            return state, "NEXT_TASK"
        return state, "RECOVER"

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
                # verb_hints 同 _ensure_fields
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
                        # 如果没有找到文件路径，说明 goal 是纯描述性任务
                        # （如 "分析需求并确定需要创建或修改的文件"），
                        # 不应该赋值 target → Dispatcher 会走 ReActExecutor
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

    # ── 重规划 ──

    async def replan(
        self,
        state: AgentState,
        user_input: str,
        user_id: str,
    ) -> Tuple[AgentState, str]:
        """REPLAN 阶段：重规划失败任务。"""
        if self.replan_count >= MAX_REPLAN:
            print("❌ 达到最大重试次数")
            return state, "FAIL"

        self.replan_count += 1
        state["retries"] = self.replan_count
        print(f"\n⚠️ 任务失败，重规划 ({self.replan_count}/{MAX_REPLAN})...")

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
                ep = self._selector.select(task_obj, workspace=ws_service)
            except Exception:
                ep = ExecutionPlan(task=task_obj)
            execution_plans.append(ep)

        state["execution_plans"] = execution_plans + state.get("execution_plans", [])[len(old_unfinished):]
        state["plan"] = old_unfinished + new_plan
        state["current_task_index"] = 0
        print(f"  🔄 重新规划，共 {len(state['plan'])} 个任务（保留 {len(preserved_facts)} 个 Facts）")
        self._timings["replan_llm"] = round(time.perf_counter() - t_replan, 3)
        return state, "EXECUTE"

    # ── 最终输出 ──

    async def finalize(
        self,
        state: AgentState,
        user_input: str,
        user_id: str,
        best_answer: Optional[str] = None,
    ) -> str:
        """生成最终答案并提交记忆。"""
        if best_answer:
            MemoryService.record_full_exchange(user_id, user_input, best_answer)
            return best_answer

        t_answer = time.perf_counter()
        final_answer = await generate_final_answer(state, user_input)
        MemoryService.record_full_exchange(user_id, user_input, final_answer)
        self._timings["answer_gen"] = round(time.perf_counter() - t_answer, 3)
        return final_answer

    # ── 辅助方法 ──

    def _render_context(self, context: dict, now: datetime) -> str:
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
        prefs = context.get("preferences", "")
        if prefs:
            parts.append(f"\n## 用户偏好\n{prefs}")
        parts.append(
            "\n## 回答规则\n- 如果用户询问个人信息或时间，优先使用会话记录回答。\n"
            "- 禁止编造不存在的历史。\n- 不要描述执行过程。\n"
        )
        return "\n\n".join(parts)

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