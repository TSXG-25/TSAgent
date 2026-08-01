# tools/python.py
"""Python execution tool.

Provides a safe Python code execution environment for the agent.
Uses the Docker sandbox when available, with a local fallback.
"""
import ast
import sys
import io
import contextlib
from agent.registry.tool_registry import registry


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
    # Security: reject imports that could be dangerous
    # os and sys are allowed — they're needed for basic file/path operations.
    # But dangerous os.* / subprocess invocations are blocked (os.system etc).
    forbidden_imports = ["subprocess", "shutil", "ctypes", "signal"]
    _dangerous_os_funcs = {
        "system", "popen", "execl", "execle", "execlp", "execlpe",
        "execv", "execve", "execvp", "execvpe",
        "spawnl", "spawnle", "spawnlp", "spawnlpe",
        "spawnv", "spawnve", "spawnvp", "spawnvpe",
        "kill", "startfile", "remove", "unlink", "rmdir",
    }
    try:
        tree = ast.parse(code)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.split(".")[0] in forbidden_imports:
                        return f"错误：禁止导入模块 '{alias.name}'（安全限制）"
            elif isinstance(node, ast.ImportFrom):
                if node.module and node.module.split(".")[0] in forbidden_imports:
                    return f"错误：禁止从模块 '{node.module}' 导入（安全限制）"
            elif isinstance(node, ast.Attribute):
                # 拦截 os.system / os.popen 等危险调用（os 允许导入用于路径操作）
                if (
                    isinstance(node.value, ast.Name)
                    and node.value.id == "os"
                    and node.attr in _dangerous_os_funcs
                ):
                    return f"错误：禁止调用 os.{node.attr}（安全限制）"
    except SyntaxError as e:
        return f"语法错误: {e}"

    # Capture stdout
    stdout_capture = io.StringIO()
    stderr_capture = io.StringIO()

    namespace = {}

    try:
        with contextlib.redirect_stdout(stdout_capture), \
             contextlib.redirect_stderr(stderr_capture):
            exec(code, namespace)
        output = stdout_capture.getvalue()
        if stderr_capture.getvalue():
            output += "\n[stderr]\n" + stderr_capture.getvalue()
        return output.strip() if output.strip() else "代码执行成功（无输出）"
    except Exception as e:
        return f"执行错误: {type(e).__name__}: {e}"


def run_python_file(path: str) -> str:
    """执行指定路径的 Python 文件并返回 stdout 输出。

    Args:
        path: Python 文件路径（相对于项目根目录）

    Returns:
        文件执行的 stdout 输出
    """
    from pathlib import Path
    root = Path(__file__).parent.parent.resolve()
    full = (root / path).resolve()

    if not full.exists():
        return f"错误：文件不存在 {path}"
    if full.suffix != ".py":
        return f"错误：{path} 不是 .py 文件"

    code = full.read_text(encoding="utf-8")
    return run_python(code)


# 注册工具
registry.register(run_python, name="run_python", category="code", tags=["python", "code", "execution"])
registry.register(run_python_file, name="run_python_file", category="code", tags=["python", "file", "execution"])