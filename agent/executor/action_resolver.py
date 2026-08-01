"""ActionResolver — Executor 与 ToolRegistry 之间的隔离层。

Executor (ReAct/LLM/Tool) 不知道 ToolRegistry、不知道工具实现。
Executor 只发送 Action，ActionResolver 返回 Observation。

职责：
1. 接收 Action { capabilities, params, reason }
2. 通过 CapabilityRegistry 解析能力 → 工具名
3. 通过 ToolRegistry 调用工具
4. 返回统一的 Observation 结果

这样以后：
- 加 MCP → 改 ActionResolver
- 加 REST API → 改 ActionResolver
- 加 Docker Worker → 改 ActionResolver
Executor 永远不需要改。
"""
import time
import json
import logging
from typing import Any, Dict, List, Optional

from agent.registry.capability_registry import registry as capability_registry
from agent.registry.tool_registry import registry as tool_registry
from agent.services.artifact_service import ArtifactService
from agent.llm import llm

logger = logging.getLogger(__name__)

SUMMARIZE_PROMPT = """你是一个智能摘要生成器。对下面的题目内容，提取关键信息：

- 题目类型（算法题/编程题/数学题等）
- 目标（用一句话概括）
- 输入格式说明
- 输出格式说明  
- 关键约束条件

请用以下格式输出（不要多余内容）：
类型: 
目标: 
输入: 
输出: 
约束: 
"""


class ActionResolver:
    """动作解析器。
    
    Executor 只发 Action:
        {
            "capabilities": ["file_read"],
            "params": {"path": "input/question.docx"},
            "reason": "需要读取问题文档"
        }
    
    ActionResolver 返回 Observation:
        {
            "action": "read_file",
            "tool": "read_file",
            "status": "succeeded" | "failed",
            "summary": "...",
            "artifact_ids": [...],
            "time_s": 0.5,
        }
    """

    def __init__(self):
        self._install_commands = [
            "pip install", "pip3 install",
            "brew install",
            "apt-get install", "apt install",
            "yum install",
            "npm install -g", "npm i -g",
            "curl.*|.*bash", "curl.*|.*sh",
            "wget.*|.*bash", "wget.*|.*sh",
        ]

    async def resolve(
        self,
        capabilities: List[str],
        params: Dict[str, Any],
        reason: str = "",
        task_id: str = "",
        task_goal: str = "",
    ) -> Dict[str, Any]:
        """解析 Action 并执行。
        
        Args:
            capabilities: 需要的能力标签列表
            params: 工具参数
            reason: LLM 给出的执行理由
            task_id: 当前任务 ID（用于 Artifact 关联）
            task_goal: 当前任务目标（用于上下文判断）
            
        Returns:
            Observation dict
        """
        # 1. 安全拦截（安装命令）
        if capabilities and capabilities[0] == "shell":
            cmd = params.get("cmd", params.get("command", ""))
            blocked, reason_msg = self._check_install_command(cmd)
            if blocked:
                return self._make_observation(
                    tool="shell",
                    args_preview=cmd[:80],
                    status="failed",
                    summary=f"安全策略拦截: {reason_msg}",
                    task_id=task_id,
                )

        # 2. 通过 CapabilityRegistry 解析能力 → 工具名
        matched_tools = []
        for cap in capabilities:
            tool_name = capability_registry.resolve(cap, context=task_goal)
            if tool_name and tool_name not in matched_tools:
                matched_tools.append(tool_name)

        # 2.5 如果没匹配到，尝试直接作为工具名
        if not matched_tools:
            for cap in capabilities:
                tool_obj = tool_registry.get(cap)
                if tool_obj and cap not in matched_tools:
                    matched_tools.append(cap)

        # 2.6 兼容旧 tags 语义：LLM 可能输出工具注册时的 capability tag
        #     （如 ["filesystem", "read"] → resolve_by_capability 按 tag 匹配工具）
        if not matched_tools:
            tag_matched = tool_registry.resolve_by_capability(capabilities)
            for t in tag_matched:
                if t.name not in matched_tools:
                    matched_tools.append(t.name)

        if not matched_tools:
            all_caps = capability_registry.get_all_capabilities()
            return self._make_observation(
                tool=capabilities[0] if capabilities else "unknown",
                args_preview=str(params)[:60],
                status="failed",
                summary=f"没有找到匹配 capabilities {capabilities} 的工具。可用能力: {all_caps}",
                task_id=task_id,
            )

        # 3. 使用第一个匹配的工具执行
        tool_name = matched_tools[0]
        tool_obj = tool_registry.get(tool_name)

        if not tool_obj:
            return self._make_observation(
                tool=tool_name,
                args_preview=self._get_args_preview(tool_name, params),
                status="failed",
                summary=f"工具 {tool_name} 已注册能力但未注册到 ToolRegistry",
                task_id=task_id,
            )

        # 4. 调用工具
        import asyncio
        start_time = time.time()

        try:
            if hasattr(tool_obj, 'ainvoke'):
                result = await tool_obj.ainvoke(params)
            else:
                result = await asyncio.to_thread(tool_obj.invoke, params)

            output = result.content if hasattr(result, 'content') else str(result)
            elapsed = time.time() - start_time

            if output and len(output) > 3:
                observation = self._make_observation(
                    tool=tool_name,
                    args_preview=self._get_args_preview(tool_name, params),
                    status="succeeded",
                    summary=output[:300],
                    task_id=task_id,
                    time_s=round(elapsed, 2),
                )

                # read_file 成功后自动摘要
                if tool_name == "read_file" and len(output) > 100:
                    summary_info = await self._summarize_question(output)
                    observation["question_summary"] = summary_info["question_summary"]
                    observation["question_type"] = summary_info["question_type"]
                    observation["question_constraints"] = summary_info["question_constraints"]
                    observation["summary"] = summary_info["question_summary"][:300]

                # 存入 ArtifactService
                artifact_id = ArtifactService.put(
                    artifact_type=tool_name,
                    storage_uri=params.get("path", params.get("url", "")),
                    summary=observation["summary"],
                    metadata={"task_id": task_id, "tool": tool_name, "params": params},
                    visibility="intermediate",
                )
                observation["artifact_ids"] = [artifact_id]
                return observation

            return self._make_observation(
                tool=tool_name,
                args_preview=self._get_args_preview(tool_name, params),
                status="failed",
                summary=f"{tool_name} 返回空结果",
                task_id=task_id,
                time_s=round(elapsed, 2),
            )

        except Exception as e:
            elapsed = time.time() - start_time
            error_msg = str(e)

            if "validation" in error_msg.lower() or "argument" in error_msg.lower():
                schema_info = ""
                if hasattr(tool_obj, 'args_schema') and tool_obj.args_schema is not None:
                    try:
                        schema = tool_obj.args_schema.model_json_schema()
                        schema_info = f"\n工具 {tool_name} 需要的参数格式:\n{json.dumps(schema, indent=2, ensure_ascii=False)}"
                    except Exception:
                        pass

                ArtifactService.put(
                    artifact_type="error",
                    summary=f"参数错误: {error_msg[:200]}{schema_info}",
                    metadata={"task_id": task_id, "tool": tool_name},
                    visibility="temporary",
                )
                return self._make_observation(
                    tool=tool_name,
                    args_preview=self._get_args_preview(tool_name, params),
                    status="failed",
                    summary=f"参数错误: {error_msg[:100]}{schema_info}",
                    task_id=task_id,
                    time_s=round(elapsed, 2),
                )

            ArtifactService.put(
                artifact_type="error",
                summary=f"{tool_name} 执行失败: {error_msg[:300]}",
                metadata={"task_id": task_id, "tool": tool_name},
                visibility="temporary",
            )
            return self._make_observation(
                tool=tool_name,
                args_preview=self._get_args_preview(tool_name, params),
                status="failed",
                summary=f"执行失败: {error_msg[:200]}",
                task_id=task_id,
                time_s=round(elapsed, 2),
            )

    def _make_observation(
        self,
        tool: str,
        args_preview: str = "",
        status: str = "succeeded",
        summary: str = "",
        task_id: str = "",
        time_s: float = 0,
    ) -> Dict:
        return {
            "action": tool,
            "tool": tool,
            "args_preview": args_preview[:100],
            "status": status,
            "summary": summary[:300],
            "artifact_ids": [],
            "tool_used": tool,
            "time_s": round(time_s, 2),
        }

    def _get_args_preview(self, tool: str, params: Dict) -> str:
        if tool == "read_file":
            return f'read_file("{params.get("path", "?")}")'
        elif tool == "shell":
            return f'shell("{params.get("cmd", params.get("command", "?"))[:60]}")'
        elif tool == "web_search":
            return f'web_search("{params.get("q", params.get("query", "?"))[:60]}")'
        elif tool == "run_python":
            return 'run_python(code=...)'
        elif tool == "write_file":
            return f'write_file("{params.get("path", "?")}")'
        else:
            return f'{tool}({json.dumps(params, ensure_ascii=False)[:60]})'

    def _check_install_command(self, cmd: str) -> tuple:
        if not cmd:
            return False, ""
        cmd_lower = cmd.lower().strip()
        for pattern in self._install_commands:
            if pattern in cmd_lower:
                return True, f"禁止执行安装命令。如需安装依赖，请在 requirements.txt 中添加。"
        return False, ""

    async def _summarize_question(self, raw_text: str) -> Dict:
        try:
            messages = [
                {"role": "system", "content": SUMMARIZE_PROMPT},
                {"role": "user", "content": f"题目内容:\n{raw_text[:2000]}"}
            ]
            response = await llm.ainvoke(messages)
            content = response.content.strip()

            result = {
                "question_summary": content[:500],
                "question_type": "",
                "question_constraints": "",
            }

            for line in content.split("\n"):
                line = line.strip()
                if line.startswith("类型:"):
                    result["question_type"] = line[3:].strip()
                elif line.startswith("约束:") or line.startswith("限制:"):
                    result["question_constraints"] = line[3:].strip()

            return result
        except Exception as e:
            logger.warning(f"Question summarization failed: {e}")
            return {"question_summary": raw_text[:300], "question_type": "", "question_constraints": ""}


# 全局单例
resolver = ActionResolver()