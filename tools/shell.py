# tools/shell.py
"""Shell execution tool.

Executes shell commands in an isolated environment. Uses Docker sandbox
when available, with automatic fallback to local subprocess execution.
"""
from agent.registry.tool_registry import registry
from agent.sandbox import run_in_sandbox, run_in_workspace


def shell(cmd: str, timeout: int = 30) -> str:
    """在隔离沙箱中执行 shell 命令并返回输出。

    使用 Docker 沙箱（默认）或本地子进程（fallback）执行命令。
    支持任意 bash 命令，包括管道、重定向等。

    Args:
        cmd: 要执行的 shell 命令字符串（如 "ls -la" 或 "echo hello | grep hello"）
        timeout: 执行超时秒数（默认 30 秒，防止长时间运行）

    Returns:
        命令的标准输出（stdout）。如果命令失败或超时，返回错误描述。
    """
    return run_in_sandbox(cmd, timeout=timeout)


def shell_in_workspace(
    cmd: str,
    workspace_root: str,
    timeout: int = 30,
) -> str:
    """Execute a shell command with an explicit Run workspace as cwd."""

    return run_in_workspace(cmd, workspace_root, timeout=timeout)


registry.register(shell, category="shell", tags=["shell", "execution"])
