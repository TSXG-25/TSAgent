"""ContextService — 组装 Executor 的 LLM Prompt。

核心改进:
1. Action History: 最近 2-3 条工具调用记录（含 args_preview + status + 时间）
2. Question Summary: LLM 总结的结构化题目信息（类型、目标、输入输出、约束）
3. Tool Affordance: 根据 goal 和 facts 过滤可用工具（3-5 个最相关）
4. Artifact Reference: 从 ArtifactService 读取可用 Artifact，而非另存一份
5. Scope Boundary: 可用资源标记（✓ 有 / ✗ 无）
6. Step Budget: "Step 3/8" 让 LLM 知道还剩多少次
"""
import json
import time as time_module
from typing import Any, Dict, List, Optional
from langchain_core.messages import SystemMessage, HumanMessage
from agent.services.tool_service import ToolService


class ContextService:
    """组装 Executor 的 LLM Think Prompt。

    Prompt 结构（按显示顺序）：
    1. Task info (goal, description, success_condition)
    2. Current State (Facts 压缩 + Question Summary)
    3. Action History (最近 2-3 条)
    4. Step Budget (Step 3/8)
    5. Scope Boundary (可用/不可用资源)
    6. Available Artifacts (ArtifactService 中的引用)
    7. Available Tools (Tool Affordance 过滤)
    8. Output format
    """

    @classmethod
    def build_think_prompt(
        cls,
        task: Dict,
        system_prompt: str = "",
        artifact_store=None,
    ) -> List:
        """为 Executor Think 步骤组装完整 Prompt。"""
        system_content = (
            system_prompt
            or "你是一个智能 Agent。根据当前目标和已有信息，决定下一步行动。"
        )

        sections = []

        # 1. Task info
        sections.append(f"""## 当前任务
目标: {task.get('goal', '?')}
说明: {task.get('description', '')}
成功条件: {task.get('success_condition', '?')}""")

        # 2. Current State (Facts + Question Summary)
        facts_section = cls._build_facts_section(task)
        sections.append(facts_section)

        # 3. Action History (最近 2-3 条工具调用)
        history_section = cls._build_action_history(task)
        sections.append(history_section)

        # 4. Step Budget
        budget_section = cls._build_step_budget(task)
        if budget_section:
            sections.append(budget_section)

        # 5. Scope Boundary
        scope_section = cls._build_scope_boundary(task)
        sections.append(scope_section)

        # 6. Available Artifacts
        art_section = cls._build_artifact_section(task, artifact_store=artifact_store)
        if art_section:
            sections.append(art_section)

        # 7. Recent Failures
        fail_section = cls._build_failure_section(task)
        if fail_section:
            sections.append(fail_section)

        # 8. Available Tools (Tool Affordance 过滤)
        sections.append(cls._build_tool_cards_section(task))

        # 9. Output format
        sections.append(cls._build_output_format_section())

        human_content = "\n\n".join(sections)
        return [
            SystemMessage(content=system_content),
            HumanMessage(content=human_content),
        ]

    @classmethod
    def _build_facts_section(cls, task: Dict) -> str:
        """Fix: 基于 Facts 构建状态摘要，包含 Question Summary。"""
        facts = task.get("facts", {})
        if not facts:
            return "## 当前状态\n  无（尚未开始）"

        lines = ["## 当前状态"]
        
        # Question summary (if available)
        if facts.get("question_loaded"):
            qlen = facts.get("question_length", "?")
            qpath = facts.get("question_path", "?")
            lines.append(f"  📄 问题文档: 已加载 ({qlen}字符)")
            lines.append(f"     路径: {qpath}")
            
            # Show LLM-generated structured summary
            qtype = facts.get("question_type", "")
            qsummary = facts.get("question_summary", "")
            qconstraints = facts.get("question_constraints", "")
            
            if qsummary:
                lines.append(f"  摘要:")
                for sline in qsummary.split("\n")[:6]:
                    sline = sline.strip()
                    if sline:
                        lines.append(f"    {sline}")
            
            lines.append("  ⚠️ 不要重新读取问题文档，摘要已完整可用。")
        
        if facts.get("directory_listed"):
            files = facts.get("files", "")
            lines.append(f"  📁 目录结构: 已知")
            if files:
                lines.append(f"    {files[:200]}")
        
        if facts.get("code_executed"):
            lines.append(f"  💻 代码: {'已生成' if facts.get('solution_generated') else '已执行'}")
        
        if facts.get("file_written"):
            lines.append(f"  📝 文件: 已写入")

        if not any(facts.get(k) for k in ["question_loaded", "directory_listed", "code_executed", "file_written"]):
            lines.append("  (其他 Facts: " + ", ".join(f"{k}={v}" for k, v in facts.items() if v) + ")")

        return "\n".join(lines)

    @classmethod
    def _build_action_history(cls, task: Dict) -> str:
        """Fix: Action History — 最近 2-3 条工具调用记录。
        
        显示 tool name、参数预览、状态、时间。
        不显示整个 params（避免浪费 token）。
        """
        obs = task.get("observations", [])
        if not obs:
            return "## 最近操作\n  无（尚未开始）"

        # Get last 3 observations (newest first)
        recent = list(reversed(obs[-3:]))
        lines = ["## 最近操作"]
        
        for i, o in enumerate(recent, 1):
            tool = o.get("tool", o.get("action", "?"))
            args_preview = o.get("args_preview", "")
            status = o.get("status", "?")
            time_s = o.get("time_s", 0)
            summary = o.get("summary", "")[:60]
            
            # Status icon
            icon = "✓" if status == "succeeded" else "✗" if status == "failed" else "⋯"
            
            entry = f"  {i}. {tool}"
            if args_preview:
                entry += f" {args_preview}"
            entry += f" {icon}"
            if time_s:
                entry += f" ({time_s}s)"
            lines.append(entry)
            
            # Show result summary (compact)
            if summary:
                lines.append(f"     {summary}")
        
        return "\n".join(lines)

    @classmethod
    def _build_step_budget(cls, task: Dict) -> str:
        """Fix: Step Budget — 让 LLM 知道还有多少次。"""
        obs_count = len(task.get("observations", []))
        # Count: each observation is one step, plus the current one being considered
        current_step = obs_count + 1
        total = 8  # MAX_THINK_ITERATIONS
        
        if current_step > total:
            return ""
        
        return f"## 步骤进度\n  Step {current_step}/{total}"

    @classmethod
    def _build_scope_boundary(cls, task: Dict) -> str:
        """Fix: Scope Boundary — 明确标记可用/不可用资源。"""
        facts = task.get("facts", {})
        lines = ["## 可用资源"]
        
        # The physical cwd is not a Runtime fact and must not leak into the
        # planner prompt.  Filesystem tools resolve relative paths through the
        # current RunContext.workspace instead.
        lines.append("  📂 当前 Run workspace 由运行时作用域管理（不暴露物理路径）")
        lines.append("  📁 可用相对路径范围由用户授权和 RunContext.workspace 决定")
        
        if facts.get("question_loaded"):
            lines.append("  ✓ 题目文本（已加载，无需重新读取）")
        else:
            lines.append("  · 文件系统（read_file, write_file, list_directory）")
        
        lines.append("  ✓ Python 运行环境（run_python, run_python_file）")
        lines.append(
            "  ✓ 文件系统（read_file, write_file, copy_file, move_file, "
            "delete_file, list_directory）"
        )
        
        # Check if web tools are available and should be restricted
        try:
            from agent.registry.tool_registry import registry
            has_web = registry.get("web_search") is not None
        except Exception:
            has_web = False
        
        if has_web and facts.get("question_loaded"):
            # Question is already loaded, no need for web search
            lines.append("  ✗ 网络搜索（不必要，题目已加载）")
        elif not has_web:
            lines.append("  ✗ 网络搜索（不可用）")
        else:
            lines.append("  · 网络搜索（可用但仅在必要时使用）")
        
        lines.append("  ✗ 外部安装（禁止 pip/brew/apt）")
        
        if facts.get("question_loaded"):
            lines.append("\n⚠️ 直接解题，无需联网。")
        
        return "\n".join(lines)

    @classmethod
    def _build_artifact_section(cls, task: Dict, *, artifact_store=None) -> str:
        """Fix: 从 ArtifactService 加载当前 Task 可用的 Artifact。"""
        if artifact_store is None:
            artifact_store = task.get("_artifact_store")
        if artifact_store is None:
            # Artifact visibility is a Run-owned dependency.  A prompt
            # renderer without that scope has no artifacts to project; it
            # must not consult the process-global ArtifactService.
            return ""

        task_id = task.get("id", "")
        artifacts = artifact_store.get_by_task(task_id)

        if not artifacts:
            # Also check for artifacts visible in the current explicit scope.
            if hasattr(artifact_store, "items"):
                all_artifacts = list(artifact_store.items())
            else:
                all_artifacts = list(getattr(artifact_store, "_store", {}).values())
            # Show last few useful artifacts
            useful = [a for a in all_artifacts if a.visibility in ("intermediate", "final")]
            if not useful:
                return ""
            artifacts = useful[-2:]  # Last 2
        
        lines = ["## 可用 Artifact"]
        for art in artifacts[-3:]:  # Max 3
            summary = art.summary[:150]
            lines.append(f"  [{art.type}] {summary}")
        
        return "\n".join(lines)

    @classmethod
    def _build_dependency_section(cls, task: Dict) -> str:
        """前置 Task 的 Artifact 摘要 + Facts。"""
        dep_artifacts = task.get("_dependency_artifacts", "")
        global_facts = task.get("_global_facts", "")
        
        parts = []
        if dep_artifacts:
            parts.append(f"## 前置任务产出\n{dep_artifacts[:500]}")
        if global_facts:
            parts.append(f"## 全局 Facts\n{global_facts[:300]}")
        
        return "\n\n".join(parts) if parts else ""

    @classmethod
    def _build_failure_section(cls, task: Dict) -> str:
        """Fix: 最近失败记录 — 带 signature 和 args_preview。"""
        failures = task.get("recent_failures", [])
        if not failures:
            return ""

        now = time_module.time()
        lines = ["## 最近失败"]
        
        # Check for duplicate signatures
        seen_signatures = set()
        for f in failures[-3:]:
            ago = int(now - f.get("time", now))
            sig = f.get("signature", "")
            args_preview = f.get("args_preview", "")
            error = f.get("error", "")[:60]
            
            # Skip if we've already shown this signature
            if sig and sig in seen_signatures:
                continue
            if sig:
                seen_signatures.add(sig)
            
            if args_preview:
                lines.append(f"  ✗ {args_preview} ({ago}秒前)")
                lines.append(f"    {error}")
            else:
                tool = f.get("tool", "?")
                lines.append(f"  ✗ {tool} ({ago}秒前): {error}")
        
        lines.append("提示：不要重复执行完全相同参数的工具调用。")
        return "\n".join(lines)

    @classmethod
    def _build_tool_cards_section(cls, task: Dict) -> str:
        """Fix: Tool Affordance — 根据 goal 和 facts 过滤工具。
        
        只显示最相关的 3-5 个工具，减少 LLM 选择错误的风险。
        """
        tools = ToolService.get_all_tools()
        if not tools:
            return "## 可用工具\n  （无工具注册）"

        goal = task.get("goal", "")
        facts = task.get("facts", {})
        
        # Use Tool Affordance to rank and filter
        ranked = ToolService.rank_tools(goal, facts)
        
        # Take top 5
        top_tools = ranked[:5]
        
        # Build tool details
        from agent.registry.tool_registry import registry
        tag_map = registry.tags if hasattr(registry, 'tags') else {}

        tool_caps: Dict[str, List[str]] = {}
        for tag, names in tag_map.items():
            if tag in ("all", "general"):
                continue
            for name in names:
                tool_caps.setdefault(name, []).append(tag)

        lines = ["## 可用工具"]
        for name in top_tools:
            tool = tools.get(name)
            if not tool:
                continue
            desc = (tool.description or "").split("\n")[0][:80]
            caps = tool_caps.get(name, ["general"])
            cap_str = ", ".join(f"[{c}]" for c in caps)
            lines.append(f"  {cap_str} {name}")
            lines.append(f"    {desc}")

            # Show parameter schema
            if hasattr(tool, 'args_schema') and tool.args_schema is not None:
                try:
                    schema = tool.args_schema.model_json_schema()
                    props = schema.get("properties", {})
                    required = schema.get("required", [])
                    if props:
                        param_lines = []
                        for pname, pinfo in props.items():
                            ptype = pinfo.get("type", "string")
                            pdesc = pinfo.get("description", "")
                            is_required = "必填" if pname in required else "可选"
                            param_lines.append(f"      {pname} ({ptype}, {is_required}) — {pdesc}")
                        if param_lines:
                            lines.append("    参数:")
                            lines.extend(param_lines)
                except Exception:
                    pass
            else:
                schema_hint = cls._get_known_tool_schema(name)
                if schema_hint:
                    lines.append(f"    参数: {schema_hint}")

        lines.append("\n选择工具时：根据工具的参数名和类型填写正确的值。")
        return "\n".join(lines)

    @classmethod
    def _get_known_tool_schema(cls, name: str) -> str:
        schemas = {
            "read_file": "path (string, 必填) — 文件路径，如 'output/solution.py'",
            "write_file": "path (string, 必填), content (string, 必填), mode (string, 可选, 默认overwrite)",
            "copy_file": "source (string, 必填), destination (string, 必填), exact (bool, 可选)",
            "move_file": "source (string, 必填), destination (string, 必填), exact (bool, 可选)",
            "delete_file": "path (string, 必填), exact (bool, 可选)",
            "list_directory": "path (string, 可选, 默认'.') — 目录路径，如 'output'",
            "set_working_directory": "path (string, 必填) — 切换工作目录，如 'output'、'src'、'.'",
            "find_file": "name (string, 必填) — 按文件名搜索，如 'solution.py'、'question.docx'",
            "shell": "cmd (string, 必填) — shell 命令, timeout (int, 可选, 默认30)",
            "run_python": "code (string, 必填) — Python 代码",
            "run_python_file": "path (string, 必填) — Python 文件路径",
        }
        return schemas.get(name, "")

    @classmethod
    def _build_output_format_section(cls) -> str:
        return """## 输出格式
输出 JSON，决定下一步 action：

1. 使用工具:
```json
{
  "action": "read_file",
  "params": {
    "path": "input/question.docx"
  },
  "reason": "需要读取问题文档"
}
```

2. 任务完成（仅在所有条件已满足时）:
```json
{
  "action": "finish",
  "reason": "所有目标已完成"
}
```

重要规则：
- action 必须是工具名
- params 必须完全匹配工具的参数字段名（如 path, cmd, code）
- 不要在工具名后面加不必要的文本
- 如果工具失败，不要再尝试相同参数"""

    @classmethod
    def build_finish_prompt(
        cls,
        task: Dict,
        final_artifacts: List,
        original_input: str,
    ) -> List:
        summaries = []
        for art in final_artifacts:
            summaries.append(f"- [{art.type}] {art.summary}")

        artifacts_text = "\n".join(summaries) if summaries else "（无）"

        content = f"""用户原始需求: {original_input}

## 任务执行结果
{task.get('goal', '?')}
成功条件: {task.get('success_condition', '?')}
状态: {task.get('status', '?')}

## 最终产出
{artifacts_text}

请根据以上信息生成最终回答。"""

        return [HumanMessage(content=content)]
