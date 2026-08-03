"""ContextBuilder — 认知上下文构建。

职责：
1. build()：聚合 MemoryService / WorkspaceService / State → CognitiveContext
2. render_context()：渲染系统 Prompt 的内存上下文部分
3. update_conversation_state()：意图理解后更新跨轮对话状态

Phase C.1：从 orchestrator.py 的 _build_cognitive_context / _render_context / _update_conversation_state 迁移。
"""
from datetime import datetime
from typing import Dict, Optional

from agent.state import AgentState
from agent.services import MemoryService
from agent.services.workspace_service import get_workspace_service
from agent.cognition.cognitive_context import CognitiveContext, ConversationState
from agent.cognition.intent_schema import IntentResult


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
    ) -> CognitiveContext:
        """构建 CognitiveContext（认知层统一入口）。

        从 MemoryService、WorkspaceService 等来源聚合数据。
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
            conversation_state=self._orch._conversation_state,
            workspace=ws_context,
            plan=plan,
            task=current_task,
            repository_context=repo_context,
            memory=memory,
            artifacts=artifacts,
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
        prefs = context.get("preferences", "")
        if prefs:
            parts.append(f"\n## 用户偏好\n{prefs}")
        parts.append(
            "\n## 回答规则\n- 如果用户询问个人信息或时间，优先使用会话记录回答。\n"
            "- 禁止编造不存在的历史。\n- 不要描述执行过程。\n"
        )
        return "\n\n".join(parts)
