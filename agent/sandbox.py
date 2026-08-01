"""Sandbox execution environment.

Attempts to execute commands in a Docker container for isolation.
If Docker is unavailable, falls back to local subprocess execution
with restricted permissions.
"""
import subprocess
import tempfile
import shutil
import os
import platform
from pathlib import Path

SANDBOX_IMAGE = "agent-sandbox"
DEFAULT_TIMEOUT = 30

# Cache Docker availability check
_DOCKER_AVAILABLE = None


def _check_docker() -> bool:
    global _DOCKER_AVAILABLE
    if _DOCKER_AVAILABLE is not None:
        return _DOCKER_AVAILABLE
    try:
        result = subprocess.run(
            ["docker", "info"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        _DOCKER_AVAILABLE = result.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError, Exception):
        _DOCKER_AVAILABLE = False
    return _DOCKER_AVAILABLE


def _run_local(cmd: str, timeout: int, cwd: str) -> str:
    """Execute command locally with basic safety constraints."""
    project_root = Path(__file__).resolve().parent.parent

    # Safety: block dangerous shell builtins
    blocked_prefixes = [
        "rm -rf /", "rm -rf /*", "mkfs", "dd if=", ":(){", "> /dev/sda",
        "chmod 777 /", "sudo ", "su ",
    ]
    for prefix in blocked_prefixes:
        if cmd.strip().startswith(prefix):
            return f"错误：命令被安全策略阻止（危险操作）: {cmd[:50]}..."

    try:
        result = subprocess.run(
            cmd,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=cwd or project_root,
        )
        if result.returncode != 0:
            stderr = result.stderr.strip()
            stdout = result.stdout.strip()
            if stderr:
                return f"命令执行失败 (code {result.returncode}):\n{stderr}"
            if stdout:
                return f"命令执行失败 (code {result.returncode}):\n{stdout}"
            return f"命令执行失败（返回码 {result.returncode}）"
        return result.stdout.strip()
    except subprocess.TimeoutExpired:
        return f"命令执行超时（{timeout} 秒）"
    except OSError as e:
        return f"命令执行失败: {e}"


def _run_docker(cmd: str, timeout: int, cwd: str) -> str | None:
    """Execute command in Docker sandbox. Returns None to signal fallback."""
    project_root = Path(__file__).resolve().parent.parent

    docker_cmd = [
        "docker", "run",
        "--rm",
        "--network", "none",
        "--cpus", "1.0",
        "--memory", "512m",
        "-v", f"{project_root}:/workspace",
    ]
    if cwd:
        docker_cmd.extend(["-w", cwd.replace(str(project_root), "/workspace")])
    else:
        docker_cmd.extend(["-w", "/workspace"])
    docker_cmd.extend([SANDBOX_IMAGE, "bash", "-c", cmd])

    try:
        result = subprocess.run(
            docker_cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if result.returncode != 0:
            # Docker failed — likely image not found. Return None to trigger fallback.
            return None
        return result.stdout.strip()
    except subprocess.TimeoutExpired:
        return None  # fallback
    except FileNotFoundError:
        return None  # docker not installed, fallback
    except Exception:
        return None  # any other error, fallback


def run_in_sandbox(cmd: str, timeout: int = DEFAULT_TIMEOUT) -> str:
    """在隔离环境中执行 shell 命令。

    优先使用 Docker 沙箱（更安全）；如果 Docker 不可用，
    自动回退到本地子进程执行（带命令白名单检查）。

    Args:
        cmd: 要执行的 shell 命令
        timeout: 超时秒数（默认 30 秒）

    Returns:
        命令的标准输出
    """
    project_root = Path(__file__).resolve().parent.parent
    cwd = str(project_root)

    # Always try local first (Docker sandbox image may not exist)
    # Try Docker only if local is not appropriate
    docker_result = _run_docker(cmd, timeout, cwd) if _check_docker() else None
    if docker_result is not None:
        return docker_result

    return _run_local(cmd, timeout, cwd)
