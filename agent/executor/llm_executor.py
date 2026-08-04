"""LLMExecutor — 纯 LLM 推理执行器（开放式推理）。

消费统一 Task 模型。职责：LLM reasoning only。
- 不发起工具调用
- 不接触 ToolRegistry / CapabilityRegistry
- 输入：Task.goal（已渲染的完整 prompt 或目标描述）+ 可选上下文
- 输出：ExecutionResult

这是开放式推理执行器：
对于 design/analyze/explain 类 Task（无确定性工具链），
Compiler 返回 ExecutionPlan(executor="llm")，本执行器接管。
"""
import time
import asyncio
import os
from typing import Any, Dict, Optional

from langchain_core.messages import SystemMessage, HumanMessage

from agent.llm import llm
from agent.task import Task
from agent.workflow import ExecutionContext, ExecutionResult

LLM_EXECUTION_TIMEOUT = float(os.getenv("TSAGENT_LLM_TIMEOUT", "45"))


class LLMExecutor:
    """纯 LLM 推理执行器。

    用法:
        result = await llm_executor.execute(task, context)
    """

    async def execute(
        self,
        task: Task,
        context: Optional[ExecutionContext] = None,
    ) -> ExecutionResult:
        """执行一次纯 LLM 推理。

        Args:
            task: 统一 Task（goal 是 LLM 输入）
            context: 可选的 ExecutionContext（提供 artifacts/variables 上下文）

        Returns:
            ExecutionResult（成功/失败 + outputs["text"]）
        """
        t0 = time.time()

        # 构建上下文附加信息（不发起工具调用，只读取已有数据）
        extra_parts = []
        if context is not None:
            art_summaries = [
                f"[{a.type}] {a.summary[:200]}"
                for a in context.artifacts.values()
                if a.summary
            ]
            if art_summaries:
                extra_parts.append("## 已有产物\n" + "\n".join(art_summaries[-3:]))
            if context.facts:
                facts_text = "\n".join(f"{k}: {str(v)[:100]}" for k, v in context.facts.items())
                extra_parts.append("## 已知事实\n" + facts_text)

        system_prompt = (
            "你是一个智能推理引擎。基于给定信息和任务目标，输出高质量分析和结论。"
            "\n不要提议执行工具调用——你的职责是直接推理并给出答案。"
        )
        user_content = task.goal
        if extra_parts:
            user_content += "\n\n" + "\n\n".join(extra_parts)

        messages = [
            SystemMessage(content=system_prompt),
            HumanMessage(content=user_content),
        ]

        try:
            response = await asyncio.wait_for(
                llm.ainvoke(messages),
                timeout=float(task.policy.timeout or LLM_EXECUTION_TIMEOUT),
            )
            elapsed = time.time() - t0
            content = response.content.strip() if hasattr(response, 'content') else str(response)

            if context is not None:
                context.record_action({
                    "task": task.id,
                    "executor": "llm",
                    "success": True,
                    "time_s": round(elapsed, 2),
                })

            return ExecutionResult(
                success=True,
                outputs={"text": content},
                metadata={"time_s": round(elapsed, 2), "task_id": task.id},
            )
        except Exception as e:
            elapsed = time.time() - t0

            if context is not None:
                context.record_failure({
                    "tool": "llm",
                    "error": str(e)[:100],
                    "time": time.time(),
                })

            return ExecutionResult(
                success=False,
                error=str(e),
                metadata={"time_s": round(elapsed, 2), "task_id": task.id},
            )


# 全局单例
llm_executor = LLMExecutor()
