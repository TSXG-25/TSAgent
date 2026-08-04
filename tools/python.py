# tools/python.py
"""Python execution tool.

Provides a safe Python code execution environment for the agent.
Uses the Docker sandbox when available; local execution requires explicit opt-in.
"""
import ast
import shlex
from pathlib import Path
from agent.registry.tool_registry import registry
from agent.sandbox import run_in_sandbox
from agent.security import is_sensitive_command, is_sensitive_path

ROOT = Path(__file__).parent.parent.resolve()


_FORBIDDEN_IMPORTS = {
    "asyncio", "ctypes", "importlib", "multiprocessing", "os", "pathlib",
    "resource", "signal", "shutil", "socket", "subprocess", "sys",
}
_FORBIDDEN_CALLS = {
    "__import__", "breakpoint", "compile", "eval", "exec", "input", "open",
}


def run_python(code: str, timeout: int = 10) -> str:
    """执行 Python 代码并返回 stdout/stderr 输出。

    在隔离环境中安全执行 Python 代码。支持打印语句和标准输出捕获。
    代码中定义的变量会在同一次调用中共存，但不同调用之间不共享状态。

    Args:
        code: 要执行的 Python 代码字符串
        timeout: 超时秒数（默认 10 秒）

    Returns:
        代码执行的 stdout 输出。如果发生异常，返回错误信息。
    """
    # 先做语法级拒绝，真正执行交给 sandbox，从而获得进程级 timeout。
    if is_sensitive_command(code):
        return "错误：代码被安全策略阻止（敏感信息访问）"
    try:
        tree = ast.parse(code)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.split(".")[0] in _FORBIDDEN_IMPORTS:
                        return f"错误：禁止导入模块 '{alias.name}'（安全限制）"
            elif isinstance(node, ast.ImportFrom):
                if node.module and node.module.split(".")[0] in _FORBIDDEN_IMPORTS:
                    return f"错误：禁止从模块 '{node.module}' 导入（安全限制）"
            elif isinstance(node, ast.Call):
                fn = node.func
                if isinstance(fn, ast.Name) and fn.id in _FORBIDDEN_CALLS:
                    return f"错误：禁止调用 {fn.id}（安全限制）"
    except SyntaxError as e:
        return f"语法错误: {e}"

    command = f"python3 -c {shlex.quote(code)}"
    result = run_in_sandbox(command, timeout=timeout)
    if not result:
        return "代码执行成功（无输出）"
    return result


def run_python_file(path: str, timeout: int = 10) -> str:
    """执行指定路径的 Python 文件并返回 stdout 输出。

    Args:
        path: Python 文件路径（相对于项目根目录）

    Returns:
        文件执行的 stdout 输出
    """
    full = (ROOT / str(path)).resolve()

    if is_sensitive_path(path):
        return "错误：出于安全原因，禁止执行敏感文件。"

    if not full.is_relative_to(ROOT):
        return f"错误：文件路径超出项目 workspace 范围: {path}"

    if not full.exists():
        return f"错误：文件不存在 {path}"
    if full.suffix != ".py":
        return f"错误：{path} 不是 .py 文件"

    code = full.read_text(encoding="utf-8")
    return run_python(code, timeout=timeout)


# 注册工具
registry.register(run_python, name="run_python", category="code", tags=["python", "code", "execution"])
registry.register(run_python_file, name="run_python_file", category="code", tags=["python", "file", "execution"])
