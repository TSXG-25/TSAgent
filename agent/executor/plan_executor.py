"""PlanExecutor — 执行 ExecutionPlan 的确定性步骤序列。

接收 Compiler 输出的 ExecutionPlan，按顺序执行每一步：
1. 变量替换：$path → workspace.resolve 结果（强制 str）
2. 通过 ToolRegistry 获取工具并调用
3. llm 步骤特殊处理：直接调用 agent.llm.llm

与 Workflow 体系的 ToolExecutor 无关。
Workflow ToolExecutor 消费的是 Stage，不是 ExecutionPlan。
"""
import asyncio
import ast
import json
import logging
import os
import re
from typing import Any, Dict, Optional

from agent.task import ExecutionPlan, ExecutionStep
from agent.tool_identity import CANONICAL_TOOL_ALIASES, registry_tool_name
from agent.security import redact_sensitive_text
from agent.execution_errors import classify_execution_error, stable_error_message
from agent.registry.tool_registry import registry as tool_registry
from agent.services.workspace_service import WorkspaceService
from agent.interruption import (
    CancellationView,
    RunInterruptionRequested,
    SafeCancellationBoundary,
    await_interruptibly,
    tool_cancellation_safety,
)

logger = logging.getLogger(__name__)
LLM_STEP_TIMEOUT = float(os.getenv("TSAGENT_LLM_TIMEOUT", "45"))


class PlanExecutor:
    """ExecutionPlan 执行器。

    不依赖任何 Workflow 概念（Stage、ExecutionContext、ToolPolicy）。
    只做三件事：变量替换 → 工具调用 → 结果传递。
    """

    async def execute(
        self,
        plan: ExecutionPlan,
        workspace: Optional[WorkspaceService] = None,
        cancellation_view: CancellationView | None = None,
    ) -> Dict[str, Any]:
        """执行 plan 的所有 steps。

        Args:
        plan: Compiler 输出的 ExecutionPlan
            workspace: WorkspaceService 实例（用于 workspace.resolve）

        Returns:
            outputs dict：每个 step 的 outputs 字段映射到实际结果。
            同时包含 "_last_output" 键（最后一步的纯文本摘要）。
            执行失败时不抛异常，在 outputs 中包含 "_error" 键。
        """
        if not plan or not plan.steps:
            return {"_last_output": "", "_error": "空 plan，无步骤可执行"}

        try:
            self._validate_tools(plan)
        except ValueError as exc:
            return {
                "_last_output": "",
                "_error": f"PlanExecutor: plan validation failed: {exc}",
                "_error_code": "UNKNOWN_TOOL",
                "_failed_tool": "plan_validation",
                "_files_written": [],
                "_file_operations": [],
                "_tools_called": [],
            }

        variables: Dict[str, Any] = {}
        last_output = ""
        files_written: list = []
        file_operations: list[dict[str, str]] = []
        tools_called: list[str] = []

        for step_idx, step in enumerate(plan.steps):
            tool_name = step.tool
            tools_called.append(tool_name)
            args = self._substitute_args(step.args, variables)
            safety_class = tool_cancellation_safety(tool_name)

            if cancellation_view is not None:
                cancellation_view.raise_if_requested(
                    SafeCancellationBoundary.BEFORE_TOOL,
                    safety_class,
                )

            try:
                if tool_name == "workspace":
                    result = await self._exec_workspace(step, args, workspace, variables)
                else:
                    if tool_name == "filesystem.write":
                        args = self._prepare_write_args(args)
                    if (
                        tool_name == "filesystem.write"
                        and not str(args.get("content", "")).strip()
                    ):
                        raise ValueError(
                            "EMPTY_WRITE_CONTENT: refusing to create an empty artifact"
                        )
                    if workspace is None:
                        # Preserve the unscoped legacy/test hook signature;
                        # scoped Runtime calls always take the explicit path.
                        if cancellation_view is None:
                            result = await self._exec_tool(tool_name, args)
                        else:
                            result = await self._exec_tool(
                                tool_name,
                                args,
                                cancellation_view=cancellation_view,
                            )
                    else:
                        if cancellation_view is None:
                            result = await self._exec_tool(
                                tool_name,
                                args,
                                workspace=workspace,
                            )
                        else:
                            result = await self._exec_tool(
                                tool_name,
                                args,
                                workspace=workspace,
                                cancellation_view=cancellation_view,
                            )

                # ── 收集世界状态痕迹（Verifier 的唯一输入，ADR-0012）──
                # 写入是否真正生效由 ExecutionVerifier 在 Pipeline 末端判定，
                # Tool 的返回字符串不代表成功。
                if tool_name == "filesystem.write":
                    files_written.append(str(args.get("path", "")))
                elif tool_name in {
                    "filesystem.copy",
                    "filesystem.move",
                    "filesystem.delete",
                }:
                    file_operations.append({
                        "operation": tool_name.split(".", 1)[1],
                        "path": str(args.get("path", "")),
                        "source": str(args.get("source", "")),
                        "destination": str(args.get("destination", "")),
                    })

                # 处理结果
                for out_key in step.outputs:
                    value = result.get(out_key)
                    if value is None and isinstance(result, dict) and "content" in result:
                        # 缺失的输出键回退到 content（如 llm 步骤声明 new_content 但返回 content）
                        value = result["content"]
                    if value is None and isinstance(result, dict) and len(result) == 1:
                        # Workspace uses the stable ``path`` result key while
                        # multi-source plans need distinct SSA output names.
                        value = list(result.values())[0]
                    variables[out_key] = str(value)

                # 记录文本摘要
                if isinstance(result, dict):
                    last_output = str(result.get("content", result.get("text", str(result))))[:300]
                else:
                    last_output = str(result)[:300]

                if cancellation_view is not None:
                    try:
                        cancellation_view.raise_if_requested(
                            SafeCancellationBoundary.AFTER_TOOL,
                            safety_class,
                        )
                    except RunInterruptionRequested as interruption:
                        interruption.execution_evidence.update({
                            "completed_tool": tool_name,
                            "files_written": list(files_written),
                            "file_operations": list(file_operations),
                            "last_output": last_output,
                        })
                        raise

            except RunInterruptionRequested:
                raise
            except Exception as e:
                error_msg = (
                    f"PlanExecutor: step {step_idx} ({tool_name}) 失败: "
                    f"{stable_error_message(e, fallback='tool step failed')}"
                )
                logger.error(error_msg)
                error_code = classify_execution_error(e)
                return {
                    "_last_output": last_output,
                    "_error": error_msg,
                    "_error_code": error_code or "TOOL_EXECUTION_FAILED",
                    "_failed_step": step_idx,
                    "_failed_tool": tool_name,
                    "_files_written": files_written,
                    "_file_operations": file_operations,
                    "_tools_called": tools_called,
                    **variables,
                }

        return {
            "_last_output": last_output,
            "_error": "",
            "_files_written": files_written,
            "_file_operations": file_operations,
            "_tools_called": tools_called,
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

    @staticmethod
    def _prepare_write_args(args: Dict[str, Any]) -> Dict[str, Any]:
        """Apply internal write guards and strip non-tool metadata."""
        prepared = dict(args)
        original = prepared.pop("preserve_original", None)
        instruction = str(prepared.pop("preserve_instruction", ""))
        if original is not None:
            PlanExecutor._validate_code_preservation(
                str(original),
                str(prepared.get("content", "")),
                instruction,
            )
        return prepared

    @staticmethod
    def _validate_code_preservation(
        original: str,
        updated: str,
        instruction: str,
    ) -> None:
        """Reject an edit that changes an unrequested top-level function.

        ModifyRule still uses a single full-file LLM edit, but this deterministic
        guard prevents that implementation detail from becoming an
        unrequested rewrite.  It is intentionally AST-based so formatting
        changes do not look like semantic collateral damage.
        """
        if not original.lstrip().startswith(("def ", "async def ", "import ", "from ")):
            # Non-Python text files retain the legacy append/edit behavior.
            return
        try:
            original_tree = ast.parse(original)
            updated_tree = ast.parse(updated)
        except SyntaxError as exc:
            raise ValueError(
                f"PRESERVATION_VIOLATION: 修改结果不是有效 Python: {exc.msg}"
            ) from exc

        def functions(tree: ast.Module) -> Dict[str, ast.AST]:
            return {
                node.name: node
                for node in tree.body
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            }

        before = functions(original_tree)
        requested = set(
            re.findall(r"([A-Za-z_]\w*)\s*(?:函数|方法)", instruction)
        )
        requested.update(
            re.findall(
                r"(?:给|修改|修复|新增|增加|添加|改成|改为)\s+([A-Za-z_]\w*)",
                instruction,
            )
        )
        # Some Chinese constructions put the symbol after punctuation (for
        # example ``：给 f 增加``), so use the original AST as a bounded
        # vocabulary rather than relying only on word-boundary regexes.
        requested.update(
            name for name in before if name and name in instruction
        )
        if not requested:
            return

        after = functions(updated_tree)
        for name, node in before.items():
            if name in requested:
                continue
            replacement = after.get(name)
            if replacement is None or ast.dump(node, include_attributes=False) != ast.dump(
                replacement, include_attributes=False
            ):
                raise ValueError(
                    f"PRESERVATION_VIOLATION: 未请求的函数被修改: {name}"
                )

    @staticmethod
    def _validate_tools(plan: ExecutionPlan) -> None:
        """Reject unknown tool steps before any earlier step can create effects."""
        builtin = {
            "workspace",
            "repository",
            "knowledge",
            "llm",
            "shell",
            "run_python",
            "run_python_file",
            "text.merge_unique",
            "text.materialize_research",
            "text.transform_upper",
        }
        lazy_modules = {
            "web_search": "web",
            "web_fetch": "web",
            "web_deep_search": "web",
            "web_news_search": "web",
            "query_memory": "memory",
            "get_user_preference": "memory",
            "save_fact": "memory",
            "get_session_info": "memory",
            "propose_patch": "patch",
            "apply_patch": "patch",
            "list_all_tools": "meta",
            "get_tool_info": "meta",
            "create_pptx": "office",
            "create_docx": "office",
            "list_workflows": "workflow",
            "get_workflow": "workflow",
            "run_workflow": "workflow",
        }
        modules_to_load = tuple(
            sorted({lazy_modules[step.tool] for step in plan.steps if step.tool in lazy_modules})
        )
        if modules_to_load:
            from agent.bootstrap import load_tool_modules

            load_tool_modules(modules_to_load)
        for step in plan.steps:
            if step.tool in builtin or step.tool in CANONICAL_TOOL_ALIASES:
                continue
            actual = registry_tool_name(step.tool)
            if step.tool.startswith("knowledge."):
                actual = step.tool.split(".", 1)[1]
            if tool_registry.get(actual) is None and tool_registry.get(step.tool) is None:
                raise ValueError(f"UNKNOWN_TOOL: 未注册工具 {step.tool}")

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
            return {"path": spec}

        # A write target is an exact destination, not a fuzzy discovery
        # query. Resolving a non-existent path through Workspace can select a
        # similarly named existing file and redirect the side effect.
        if str(args.get("operation", "")) in {"write", "source"}:
            return {"path": spec}

        # A trailing slash and the project root are explicit directory
        # requests.  Do not pass them through fuzzy file resolution.
        if spec in ("", ".", "./") or spec.endswith(("/", "\\")):
            try:
                root = workspace.current_workspace().root
                candidate = (root / spec).resolve()
                if candidate.is_dir():
                    return {"path": str(candidate)}
            except Exception:
                pass

        matches = await asyncio.to_thread(workspace.resolve, spec)
        if matches:
            best = matches[0]
            resolved_path = best.path if hasattr(best, 'path') else str(best)
            return {"path": str(resolved_path)}
        else:
            return {"path": spec}

    async def _exec_tool(
        self,
        tool_name: str,
        args: Dict[str, Any],
        *,
        workspace: Optional[WorkspaceService] = None,
        cancellation_view: CancellationView | None = None,
    ) -> Dict[str, Any]:
        """通过 ToolRegistry 调用工具。

        特殊处理：
        - llm: 直接调用 agent.llm.llm（未注册为工具）
        - filesystem.*: 映射为 read_file/write_file 等注册名
        - 其他: 走 ToolRegistry
        """
        # A scoped Runtime must never dispatch filesystem effects to the
        # legacy registry functions, whose compatibility implementation uses
        # a process-global project root.  Keep the registry path only for
        # unscoped legacy callers.
        if workspace is not None and tool_name.startswith("filesystem."):
            return await self._exec_scoped_filesystem(tool_name, args, workspace)

        if workspace is not None and tool_name in {
            "shell",
            "run_python",
            "run_python_file",
        }:
            return await self._exec_scoped_runtime_tool(tool_name, args, workspace)

        # ── 特殊处理：llm 未注册为工具 ──
        if tool_name == "llm":
            from agent.llm import llm as llm_engine
            from langchain_core.messages import SystemMessage, HumanMessage

            prompt = str(args.get("prompt", args.get("system_prompt", "")))
            user = str(args.get("user", args.get("input", "")))
            # edit/analyze 类：content 携带文件原文（ModifyRule 数据流）
            content = args.get("content", "")
            if content:
                prompt = prompt or (
                    "你是一个代码编辑助手。根据目标、修改要求与文件原文，"
                    "输出修改后的完整文件内容（不要任何解释、不要 markdown 代码块）。"
                    "除非用户明确要求重写，否则必须保留所有未请求的函数、导入和代码区域，"
                    "只修改目标符号或目标行为。"
                )
                user = f"目标: {args.get('target', '')}\n"
                if args.get("instruction"):
                    user += f"修改要求: {args['instruction']}\n"
                if args.get("description"):
                    user += f"详细说明: {args['description']}\n"
                user += f"\n文件原文:\n{content}"
            messages = []
            if prompt:
                messages.append({"role": "system", "content": prompt})
            if user:
                messages.append({"role": "user", "content": user})
            if not messages:
                messages.append({"role": "user", "content": str(args)})

            response = await await_interruptibly(
                llm_engine.ainvoke(messages),
                timeout=float(args.get("timeout", LLM_STEP_TIMEOUT)),
                view=cancellation_view,
            )
            content = response.content if hasattr(response, 'content') else str(response)
            if (
                args.get("verb") in {"write", "generate", "create", "edit"}
                or args.get("output_format") == "python_source"
            ):
                content = re.sub(r"^```[^\n]*\n", "", content.strip())
                content = re.sub(r"\n?```\s*$", "", content)
            return {"content": content, "text": content}

        if tool_name == "text.merge_unique":
            source_values = [
                str(value)
                for key, value in sorted(args.items())
                if key.startswith("content_")
            ]
            lines = [
                line.strip()
                for value in source_values
                for line in value.splitlines()
                if line.strip()
            ]
            unique_lines = sorted(set(lines))
            return {
                "content": "\n".join(unique_lines) + ("\n" if unique_lines else ""),
                "duplicate_count": len(lines) - len(unique_lines),
                "input_line_count": len(lines),
                "unique_line_count": len(unique_lines),
            }

        if tool_name == "text.transform_upper":
            source = str(args.get("content", ""))
            return {
                "content": source.upper(),
                "text": source.upper(),
            }

        if tool_name == "text.materialize_research":
            source = str(args.get("content", "")).strip()
            output_format = str(args.get("format", "markdown_summary"))
            if output_format == "sources_json":
                urls = list(dict.fromkeys(re.findall(r"https?://[^\s\]\[<>()\"']+", source)))
                content = json.dumps(
                    {
                        "sources": [{"url": url.rstrip(".,;，。；")} for url in urls],
                        "source_count": len(urls),
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            else:
                title = str(args.get("title", "研究摘要")).strip() or "研究摘要"
                content = f"# {title}\n\n{source}\n"
            return {"content": content, "text": content}

        # ── 特殊处理：repository 语义搜索（RepositoryService）──
        if tool_name == "repository":
            from agent.services import RepositoryService

            query = str(args.get("query", args.get("spec", "")))
            k = int(args.get("k", 5))
            hits = await asyncio.to_thread(
                RepositoryService.search_similar,
                query,
                k,
            )
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
                "copy": "copy_file",
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
            invocation = tool_obj.ainvoke(str_args)
            if tool_cancellation_safety(tool_name).value == "INTERRUPTIBLE":
                result = await await_interruptibly(
                    invocation,
                    view=cancellation_view,
                )
            else:
                result = await invocation
        else:
            result = await asyncio.to_thread(tool_obj.invoke, str_args)

        # 统一返回格式
        if hasattr(result, 'content'):
            content = str(result.content)
        else:
            content = str(result)
        content = redact_sensitive_text(content)

        if tool_name in {"web_search", "web_news_search", "web_deep_search"} and (
            content.startswith("网络搜索功能不可用")
            or content.startswith("未找到关于")
        ):
            raise RuntimeError(f"RESEARCH_TOOL_UNAVAILABLE: {content}")

        # Tools historically returned human-readable error strings.  Convert
        # those into an execution failure here so ExecutionResult.success
        # cannot be true for an operation that actually failed.
        if content.lstrip().startswith(("错误:", "错误：", "Error:", "ERROR:")):
            raise RuntimeError(content)
        return {"content": content}

    @staticmethod
    async def _exec_scoped_filesystem(
        tool_name: str,
        args: Dict[str, Any],
        workspace: WorkspaceService,
    ) -> Dict[str, Any]:
        """Execute a filesystem primitive against the explicit Run root."""
        operation = tool_name.split(".", 1)[1]

        def invoke() -> str:
            if operation == "read":
                return workspace.read_text(str(args.get("path", "")))
            if operation == "write":
                return workspace.write_text(
                    str(args.get("path", "")),
                    str(args.get("content", "")),
                    mode=str(args.get("mode", "overwrite")),
                )
            if operation == "copy":
                return workspace.copy_file(
                    str(args.get("source", "")),
                    str(args.get("destination", "")),
                )
            if operation == "move":
                return workspace.move_file(
                    str(args.get("source", "")),
                    str(args.get("destination", "")),
                )
            if operation == "delete":
                return workspace.delete_file(str(args.get("path", "")))
            if operation == "list":
                return workspace.list_directory(str(args.get("path", ".")))
            raise ValueError(f"UNKNOWN_TOOL: 未知 filesystem operation: {operation}")

        content = await asyncio.to_thread(invoke)
        return {"content": content, "text": content}

    @staticmethod
    async def _exec_scoped_runtime_tool(
        tool_name: str,
        args: Dict[str, Any],
        workspace: WorkspaceService,
    ) -> Dict[str, Any]:
        """Execute process/Python tools against the current Run workspace."""

        if tool_name == "shell":
            from tools.shell import shell_in_workspace

            content = await asyncio.to_thread(
                shell_in_workspace,
                str(args.get("cmd", "")),
                str(workspace.root),
                int(args.get("timeout", 30)),
            )
        else:
            from tools.python import run_python_in_workspace

            if tool_name == "run_python_file":
                code = workspace.read_text(str(args.get("path", "")))
            else:
                code = str(args.get("code", ""))
            content = await asyncio.to_thread(
                run_python_in_workspace,
                code,
                workspace.root,
                int(args.get("timeout", 10)),
            )

        if str(content).lstrip().startswith(("错误:", "错误：", "Error:", "ERROR:")):
            raise RuntimeError(str(content))
        return {"content": str(content), "text": str(content)}


# 全局单例
plan_executor = PlanExecutor()
