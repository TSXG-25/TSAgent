#!/usr/bin/env python3
"""Script to write all improvement files at once."""
import os

BASE = os.path.dirname(os.path.abspath(__file__))

files = {}

files['workflows/bug_fix.py'] = '''# workflows/bug_fix.py
"""
Bug Fix Workflow - 修复代码中的 bug。
步骤：读取文件 → 分析错误 → 生成补丁 → 应用补丁 → 验证
"""
from agent.registry.workflow_registry import workflow_registry


async def bug_fix_workflow(user_input: str, memory_context: str = "") -> list[dict]:
    """Bug 修复工作流：分析错误 → 定位文件 → 生成补丁 → 验证"""
    return [
        {"goal": "读取相关文件内容并定位 bug 位置", "status": "pending"},
        {"goal": "分析 bug 原因并生成修复补丁", "status": "pending"},
        {"goal": "应用补丁并进行验证测试", "status": "pending"},
    ]


workflow_registry.register("bug_fix", bug_fix_workflow)
'''

files['workflows/feature_dev.py'] = '''# workflows/feature_dev.py
"""
Feature Development Workflow - 从需求到代码实现。
步骤：分析需求 → 设计方案 → 编写代码 → 验证
"""
from agent.registry.workflow_registry import workflow_registry


async def feature_dev_workflow(user_input: str, memory_context: str = "") -> list[dict]:
    """功能开发工作流：需求分析 → 代码实现 → 验证"""
    return [
        {"goal": "分析需求并确定需要创建或修改的文件", "status": "pending"},
        {"goal": "编写代码实现需求功能", "status": "pending"},
        {"goal": "验证代码是否正确运行", "status": "pending"},
    ]


workflow_registry.register("feature_dev", feature_dev_workflow)
'''

files['workflows/code_review.py'] = '''# workflows/code_review.py
"""
Code Review Workflow - 代码审查。
步骤：读取代码 → 检查问题 → 输出审查报告
"""
from agent.registry.workflow_registry import workflow_registry


async def code_review_workflow(user_input: str, memory_context: str = "") -> list[dict]:
    """代码审查工作流：读取文件 → 分析问题 → 输出审查报告"""
    return [
        {"goal": "读取需要审查的代码文件", "status": "pending"},
        {"goal": "分析代码中的潜在问题（bug、性能、安全、风格）", "status": "pending"},
        {"goal": "汇总审查结果并输出改进建议", "status": "pending"},
    ]


workflow_registry.register("code_review", code_review_workflow)
'''

files['workflows/research.py'] = '''# workflows/research.py
"""
Research Workflow - 调研任务。
步骤：搜索资料 → 阅读摘要 → 整理报告
"""
from agent.registry.workflow_registry import workflow_registry


async def research_workflow(user_input: str, memory_context: str = "") -> list[dict]:
    """调研工作流：搜索 → 阅读 → 整理答案"""
    return [
        {"goal": "使用 web_search 搜索相关信息和最新资料", "status": "pending"},
        {"goal": "阅读搜索结果中的重要链接内容", "status": "pending"},
        {"goal": "综合所有信息整理出完整回答", "status": "pending"},
    ]


workflow_registry.register("research", research_workflow)
'''

files['tools/python.py'] = '''# tools/python.py
"""Python 代码执行工具"""
import sys
import io
import traceback
from contextlib import redirect_stdout, redirect_stderr
from agent.registry.tool_registry import registry


def run_python_code(code: str) -> str:
    """在隔离环境中执行 Python 代码并返回输出结果。
    注意：此工具会执行任意 Python 代码，仅用于验证简单逻辑。
    """
    try:
        local_scope = {}
        stdout_capture = io.StringIO()
        stderr_capture = io.StringIO()
        with redirect_stdout(stdout_capture), redirect_stderr(stderr_capture):
            exec(code, {"__builtins__": __builtins__}, local_scope)
        output = stdout_capture.getvalue()
        error = stderr_capture.getvalue()
        if error and not output:
            return error
        if output:
            return output
        # If no stdout output, return the last expression value
        last = local_scope.get("_", "")
        return str(last) if last else "代码执行成功，无输出。"
    except Exception as e:
        return f"执行错误:\\n{traceback.format_exc()}"


registry.register(run_python_code, name="run_python", category="code", tags=["python", "execution"])
'''

files['tools/memory.py'] = '''# tools/memory.py
"""记忆查询工具"""
from agent.registry.tool_registry import registry
from agent.services import MemoryService


def query_memory(query: str, k: int = 3) -> str:
    """查询语义记忆，检索与 query 相关的历史对话记录。"""
    result = MemoryService.retrieve_semantic("default", query, k=k)
    return result if result else "未找到相关记忆。"


registry.register(query_memory, category="memory", tags=["memory", "search"])
'''

files['tools/workflow.py'] = '''# tools/workflow.py
"""工作流管理工具"""
from agent.registry.tool_registry import registry
from agent.services import WorkflowService


def list_workflows() -> str:
    """列出所有已注册的工作流及其名称。"""
    workflows = WorkflowService.list_workflows()
    if not workflows:
        return "未注册任何工作流。"
    return "已注册的工作流:\\n" + "\\n".join(f"- {w}" for w in workflows)


registry.register(list_workflows, category="management", tags=["workflow", "list"])
'''

for relpath, content in files.items():
    fullpath = os.path.join(BASE, relpath)
    os.makedirs(os.path.dirname(fullpath), exist_ok=True)
    with open(fullpath, 'w', encoding='utf-8') as f:
        f.write(content)
    # Verify
    with open(fullpath, 'r') as f:
        lines = f.readlines()
    lines_count = len(lines)
    print(f"  {relpath}: {lines_count} lines, ends with: {repr(lines[-1][:60])}...")

print("\nAll files written successfully!")
