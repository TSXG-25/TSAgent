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
import signal
from pathlib import Path
from agent.failure.taxonomy import FailureCode
from agent.security import is_sensitive_command, redact_sensitive_text

SANDBOX_IMAGE = "agent-sandbox"
DEFAULT_TIMEOUT = 30
LOCAL_EXECUTION_ENV = "TSAGENT_ALLOW_LOCAL_EXECUTION"

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


def _env_flag(name: str, default: bool = False) -> bool:
    """读取显式布尔环境变量。安全相关配置默认关闭。"""
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def local_execution_allowed() -> bool:
    """本地执行必须显式 opt-in；生产默认只能使用隔离环境。"""
    return _env_flag(LOCAL_EXECUTION_ENV, default=False)


def _within_project(path: str | Path) -> bool:
    """检查工作目录是否位于项目根目录内。"""
    project_root = Path(__file__).resolve().parent.parent
    try:
        Path(path).resolve().relative_to(project_root)
        return True
    except ValueError:
        return False


def _run_local(
    cmd: str,
    timeout: int,
    cwd: str,
    *,
    allow_external_workspace: bool = False,
) -> str:
    """Execute command locally with explicit opt-in and process-group timeout."""
    project_root = Path(__file__).resolve().parent.parent

    if not local_execution_allowed():
        return (
            f"错误：{FailureCode.EXECUTION_ENVIRONMENT_UNAVAILABLE.value}: "
            "没有可用的隔离执行环境；本地执行默认关闭。"
            f"请启动 Docker，或仅在受信任的开发环境显式设置 {LOCAL_EXECUTION_ENV}=1。"
        )

    if is_sensitive_command(cmd):
        return "错误：命令被安全策略阻止（敏感信息访问）"

    if allow_external_workspace:
        if not cwd or not Path(cwd).resolve().is_dir():
            return "错误：执行工作目录不是有效的 Run workspace。"
    elif not _within_project(cwd or project_root):
        return "错误：执行工作目录超出项目 workspace 范围。"

    # Safety: block dangerous shell builtins
    blocked_patterns = [
        "rm -rf /", "rm -rf /*", "mkfs", "dd if=", ":(){", "> /dev/sda",
        "chmod 777 /", "sudo ", "su ",
    ]
    normalized = cmd.strip().lower()
    for pattern in blocked_patterns:
        if pattern in normalized:
            return f"错误：命令被安全策略阻止（危险操作）: {cmd[:50]}..."

    process = None
    try:
        # 独立进程组使 timeout 能连同 shell 的子进程一起终止。
        process = subprocess.Popen(
            cmd,
            shell=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=cwd or project_root,
            start_new_session=True,
        )
        stdout, stderr = process.communicate(timeout=timeout)
        returncode = process.returncode
        if returncode != 0:
            stderr = stderr.strip()
            stdout = stdout.strip()
            if stderr:
                return redact_sensitive_text(f"命令执行失败 (code {returncode}):\n{stderr}")
            if stdout:
                return redact_sensitive_text(f"命令执行失败 (code {returncode}):\n{stdout}")
            return f"命令执行失败（返回码 {returncode}）"
        return redact_sensitive_text(stdout.strip())
    except subprocess.TimeoutExpired:
        if process is not None:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except (ProcessLookupError, OSError):
                process.kill()
            process.communicate()
        return f"命令执行超时（{timeout} 秒）"
    except OSError as e:
        return f"命令执行失败: {e}"


def _run_docker(
    cmd: str,
    timeout: int,
    cwd: str,
    *,
    mount_root: str | Path | None = None,
) -> str | None:
    """Execute command in Docker sandbox. Returns None to signal fallback."""
    project_root = Path(__file__).resolve().parent.parent

    workspace_root = Path(mount_root or project_root).resolve()
    cwd_path = Path(cwd or workspace_root).resolve()
    try:
        relative_cwd = cwd_path.relative_to(workspace_root)
    except ValueError:
        return None

    docker_cmd = [
        "docker", "run",
        "--rm",
        "--network", "none",
        "--cpus", "1.0",
        "--memory", "512m",
        "-v", f"{workspace_root}:/workspace",
    ]
    docker_cwd = Path("/workspace") / relative_cwd
    docker_cmd.extend(["-w", str(docker_cwd)])
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
        return redact_sensitive_text(result.stdout.strip())
    except subprocess.TimeoutExpired:
        return None  # fallback
    except FileNotFoundError:
        return None  # docker not installed, fallback
    except Exception:
        return None  # any other error, fallback


def run_in_sandbox(cmd: str, timeout: int = DEFAULT_TIMEOUT) -> str:
    """在隔离环境中执行 shell 命令。

    优先使用 Docker 沙箱（更安全）；如果 Docker 不可用，
    默认拒绝执行。只有显式设置 TSAGENT_ALLOW_LOCAL_EXECUTION=1
    时才允许在受信任的开发环境中本地执行。

    Args:
        cmd: 要执行的 shell 命令
        timeout: 超时秒数（默认 30 秒）

    Returns:
        命令的标准输出
    """
    project_root = Path(__file__).resolve().parent.parent
    cwd = str(project_root)

    if is_sensitive_command(cmd):
        return "错误：命令被安全策略阻止（敏感信息访问）"

    docker_result = (
        _run_docker(cmd, timeout, cwd, mount_root=project_root)
        if _check_docker()
        else None
    )
    if docker_result is not None:
        return docker_result

    return _run_local(cmd, timeout, cwd)


def run_in_workspace(
    cmd: str,
    workspace_root: str | Path,
    timeout: int = DEFAULT_TIMEOUT,
) -> str:
    """Run a command against an explicit Run workspace.

    This is the scoped counterpart to :func:`run_in_sandbox`.  The caller
    supplies a Run-owned root, so Docker mounts that root and local fallback
    uses the same cwd.  No process-global project root is consulted.
    """

    root = Path(workspace_root).resolve()
    if not root.is_dir():
        return "错误：执行工作目录不是有效的 Run workspace。"
    if is_sensitive_command(cmd):
        return "错误：命令被安全策略阻止（敏感信息访问）"

    docker_result = (
        _run_docker(cmd, timeout, str(root), mount_root=root)
        if _check_docker()
        else None
    )
    if docker_result is not None:
        return docker_result
    return _run_local(
        cmd,
        timeout,
        str(root),
        allow_external_workspace=True,
    )
