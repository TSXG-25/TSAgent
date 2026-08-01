"""PlanExecutor — 执行 ExecutionPlan 的确定性步骤序列。

接收 ToolSelector 输出的 ExecutionPlan，按顺序执行每一步：
1. 变量替换：$path → workspace.resolve 结果（强制 str）
2. 通过 ToolRegistry 获取工具并调用
3. llm 步骤特殊处理：直接调用 agent.llm.llm

与 Workflow 体系的 ToolExecutor 无关。
Workflow ToolExecutor 消费的是 Stage，不是 ExecutionPlan。
"""
import asyncio
import logging
from typing import Any, Dict, Optional

from agent.task import ExecutionPlan, ExecutionStep
from agent.registry.tool_registry import registry as tool_registry
from agent.services.workspace_service import WorkspaceService

logger = logging.getLogger(__name__)


class PlanExecutor:
    """ExecutionPlan 执行器。

    不依赖任何 Workflow 概念（Stage、ExecutionContext、ToolPolicy）。
    只做三件事：变量替换 → 工具调用 → 结果传递。
    """

    async def execute(
        self,
        plan: ExecutionPlan,
        workspace: Optional[WorkspaceService] = None,
    ) -> Dict[str, Any]:
        """执行 plan 的所有 steps。

        Args:
            plan: ToolSelector 输出的 ExecutionPlan
            workspace: WorkspaceService 实例（用于 workspace.resolve）

        Returns:
            outputs dict：每个 step 的 outputs 字段映射到实际结果。
            同时包含 "_last_output" 键（最后一步的纯文本摘要）。
            执行失败时不抛异常，在 outputs 中包含 "_error" 键。
        """
        if not plan or not plan.steps:
            return {"_last_output": "", "_error": "空 plan，无步骤可执行"}

        variables: Dict[str, Any] = {}
        last_output = ""

        for step_idx, step in enumerate(plan.steps):
            tool_name = step.tool
            args = self._substitute_args(step.args, variables)

            try:
                if tool_name == "workspace":
                    result = await self._exec_workspace(step, args, workspace, variables)
                else:
                    result = await self._exec_tool(tool_name, args)

                # 处理结果
                for out_key in step.outputs:
                    value = result.get(out_key)
                    if value is None and isinstance(result, dict) and "content" in result:
                        # 缺失的输出键回退到 content（如 llm 步骤声明 new_content 但返回 content）
                        value = result["content"]
                    variables[out_key] = str(value)

                # 记录文本摘要
                if isinstance(result, dict):
                    last_output = str(result.get("content", result.get("text", str(result))))[:300]
                else:
                    last_output = str(result)[:300]

            except Exception as e:
                error_msg = f"PlanExecutor: step {step_idx} ({tool_name}) 失败: {e}"
                logger.error(error_msg)
                return {
                    "_last_output": last_output,
                    "_error": error_msg,
                    "_failed_step": step_idx,
                    "_failed_tool": tool_name,
                    **variables,
                }

        return {
            "_last_output": last_output,
            "_error": "",
            **variables,
        }

    def _substitute_args(self, args: Dict[str, Any], variables: Dict[str, Any]) -> Dict[str, Any]:
        """替换参数中的 $variable 引用，所有值强制转为 str。"""
        result = {}
        for k, v in args.items():
            if isinstance(v, str) and v.startswith("$"):
                var_name = v[1:]
                raw = variables.get(var_name, v)
                result[k] = str(raw)  # 强制 str，避免 PosixPath 传递
            else:
                result[k] = v
        return result

    async def _exec_workspace(
        self,
        step: ExecutionStep,
        args: Dict[str, Any],
        workspace: Optional[WorkspaceService],
        variables: Dict[str, Any],
    ) -> Dict[str, Any]:
        """执行 workspace 步骤（特殊处理：解析路径）。"""
        spec = str(args.get("spec", ""))
        if not workspace:
            return {"path": spec, "content": spec}

        matches = workspace.resolve(spec)
        if matches:
            best = matches[0]
            resolved_path = best.path if hasattr(best, 'path') else str(best)
            return {"path": str(resolved_path), "content": str(resolved_path)}
        else:
            return {"path": spec, "content": spec}

    async def _exec_tool(self, tool_name: str, args: Dict[str, Any]) -> Dict[str, Any]:
        """通过 ToolRegistry 调用工具。

        特殊处理：
        - llm: 直接调用 agent.llm.llm（未注册为工具）
        - filesystem.*: 映射为 read_file/write_file 等注册名
        - 其他: 走 ToolRegistry
        """
        # ── 特殊处理：llm 未注册为工具 ──
        if tool_name == "llm":
            from agent.llm import llm as llm_engine
            from langchain_core.messages import SystemMessage, HumanMessage

            prompt = str(args.get("prompt", args.get("system_prompt", "")))
            user = str(args.get("user", args.get("input", "")))
            messages = []
            if prompt:
                messages.append({"role": "system", "content": prompt})
            if user:
                messages.append({"role": "user", "content": user})
            if not messages:
                messages.append({"role": "user", "content": str(args)})

            response = await llm_engine.ainvoke(messages)
            content = response.content if hasattr(response, 'content') else str(response)
            return {"content": content, "text": content}

        # ── 特殊处理：repository 语义搜索（RepositoryService）──
        if tool_name == "repository":
            from agent.services import RepositoryService

            query = str(args.get("query", args.get("spec", "")))
            k = int(args.get("k", 5))
            hits = RepositoryService.search_similar(query, k=k)
            if hits:
                content = "\n\n".join(
                    f"[{h['path']}]\n{h['content'][:300]}" for h in hits
                )
            else:
                content = "未找到相关代码"
            return {"content": content, "results": content, "text": content}

        # ── 特殊处理：knowledge 列出能力（工具 + 工作流）──
        if tool_name == "knowledge":
            from agent.registry.tool_registry import registry as tr
            from agent.registry.workflow_registry import workflow_registry

            tools = ", ".join(sorted(tr.get_all().keys()))
            wfs = ", ".join(workflow_registry.list())
            content = f"可用工具: {tools}\n可用工作流: {wfs}"
            return {"content": content, "items": content, "text": content}

        # ── 规范化工具名 ──
        actual_name = tool_name
        if tool_name.startswith("filesystem."):
            actual_name = tool_name.split(".", 1)[1]
            name_map = {
                "read": "read_file",
                "write": "write_file",
                "list": "list_directory",
                "delete": "delete_file",
                "move": "move_file",
            }
            actual_name = name_map.get(actual_name, actual_name)

        if tool_name.startswith("knowledge."):
            actual_name = tool_name.split(".", 1)[1]

        # ── 确保参数值为 str ──
        str_args = {k: str(v) if not isinstance(v, dict) else v for k, v in args.items()}

        # ── 查找工具 ──
        tool_obj = tool_registry.get(actual_name) or tool_registry.get(tool_name)
        if not tool_obj:
            raise ValueError(f"未找到工具: {tool_name} (尝试: {actual_name})")

        if hasattr(tool_obj, 'ainvoke'):
            result = await tool_obj.ainvoke(str_args)
        else:
            result = await asyncio.to_thread(tool_obj.invoke, str_args)

        # 统一返回格式
        if hasattr(result, 'content'):
            return {"content": result.content}
        return {"content": str(result)}


# 全局单例
plan_executor = PlanExecutor()