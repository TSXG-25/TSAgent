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
# Docker 网络模式：默认 none（最强隔离）；TSAGENT_SANDBOX_NETWORK=bridge
# 时沙箱内命令可以联网（pip/curl/API），文件与资源限额不变。
SANDBOX_NETWORK_ENV = "TSAGENT_SANDBOX_NETWORK"
# 沙箱层级偏好：docker（默认，强隔离优先）| seatbelt（宿主可见性优先——
# Seatbelt 读全机文件、跑宿主二进制，写限 workspace；需要整机资源/网络
# 硬隔离时改回 docker）。
SANDBOX_PREFER_ENV = "TSAGENT_SANDBOX_PREFER"
# 隐藏文件读取开关：deny（默认）时 Seatbelt 拒读 $HOME 顶层 .*
# 隐藏文件/目录及任意路径的 .env——secrets 聚集地默认保护；
# TSAGENT_SANDBOX_DOTFILES=allow 放开全部读面。
SANDBOX_DOTFILES_ENV = "TSAGENT_SANDBOX_DOTFILES"

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


def _sandbox_prefer() -> str:
    return os.getenv(SANDBOX_PREFER_ENV, "docker").strip().lower()


def _sandbox_network() -> str:
    """Docker 网络模式，默认 none；bridge 放开联网。"""
    mode = os.getenv(SANDBOX_NETWORK_ENV, "none").strip().lower()
    return mode if mode in {"none", "bridge", "host"} else "none"


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


def _blocked_command_error(cmd: str) -> str | None:
    """危险命令黑名单；未命中返回 None。"""
    blocked_patterns = [
        "rm -rf /", "rm -rf /*", "mkfs", "dd if=", ":(){", "> /dev/sda",
        "chmod 777 /", "sudo ", "su ",
    ]
    normalized = cmd.strip().lower()
    for pattern in blocked_patterns:
        if pattern in normalized:
            return f"错误：命令被安全策略阻止（危险操作）: {cmd[:50]}..."
    return None


# macOS Seatbelt：workspace-write 语义（dsh 同款），文件读放开、
# 写限定在 ROOT + 系统临时区 + 标准 sink；网络不在该词汇内。
_SEATBELT_PROFILE = """(version 1)
(allow default)
{dotfile_rules}
(deny file-write*)
(allow file-write*
    (subpath (param "ROOT"))
    (subpath "/private/tmp")
    (subpath "/private/var/folders")
    (literal "/dev/null")
    (literal "/dev/stdout")
    (literal "/dev/stderr"))
"""


def _seatbelt_profile() -> str:
    """构建 Seatbelt profile；deny 规则必须在 (allow default) 之后出现。"""
    if os.getenv(SANDBOX_DOTFILES_ENV, "deny").strip().lower() == "allow":
        rules = ""
    else:
        import re as _re
        home = _re.escape(str(Path.home()))
        rules = (
            f'(deny file-read* (regex #"^{home}/\\.[^/]+(/.*)?$"))\n'
            '    (deny file-read* (regex #"/\\.env$"))'
        )
    return _SEATBELT_PROFILE.format(dotfile_rules=rules)

_SEATBELT_AVAILABLE = None


def _check_seatbelt() -> bool:
    global _SEATBELT_AVAILABLE
    if _SEATBELT_AVAILABLE is not None:
        return _SEATBELT_AVAILABLE
    if platform.system() != "Darwin" or not shutil.which("sandbox-exec"):
        _SEATBELT_AVAILABLE = False
        return False
    try:
        result = subprocess.run(
            ["sandbox-exec", "-p", _seatbelt_profile(),
             "-D", f"ROOT={Path(__file__).resolve().parent.parent}",
             "bash", "-c", "true"],
            capture_output=True, timeout=10,
        )
        _SEATBELT_AVAILABLE = result.returncode == 0
    except Exception:
        _SEATBELT_AVAILABLE = False
    return _SEATBELT_AVAILABLE


def _run_seatbelt(
    cmd: str,
    timeout: int,
    cwd: str,
    *,
    writable_root: str | Path,
) -> str:
    """Execute command under macOS Seatbelt workspace-write confinement."""
    blocked = _blocked_command_error(cmd)
    if blocked:
        return blocked
    root = Path(writable_root).resolve()
    process = None
    try:
        process = subprocess.Popen(
            ["sandbox-exec", "-p", _seatbelt_profile(),
             "-D", f"ROOT={root}", "bash", "-c", cmd],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=cwd,
            start_new_session=True,
        )
        stdout, stderr = process.communicate(timeout=timeout)
        if process.returncode != 0:
            detail = "\n".join(p for p in (stdout.strip(), stderr.strip()) if p)
            return redact_sensitive_text(
                f"错误：COMMAND_NONZERO_EXIT: 命令执行失败 "
                f"(code {process.returncode}):\n{detail}"
            )
        return redact_sensitive_text(stdout)
    except subprocess.TimeoutExpired:
        if process is not None:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except (ProcessLookupError, OSError):
                process.kill()
            process.communicate()
        return f"命令执行超时（{timeout} 秒）"
    except OSError as e:
        return f"错误：命令执行失败: {e}"


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
            f"请启动 Docker 或确保系统支持 sandbox-exec；仅在受信任的开发环境显式设置 {LOCAL_EXECUTION_ENV}=1。"
        )

    if is_sensitive_command(cmd):
        return "错误：命令被安全策略阻止（敏感信息访问）"

    if allow_external_workspace:
        if not cwd or not Path(cwd).resolve().is_dir():
            return "错误：执行工作目录不是有效的 Run workspace。"
    elif not _within_project(cwd or project_root):
        return "错误：执行工作目录超出项目 workspace 范围。"

    blocked = _blocked_command_error(cmd)
    if blocked:
        return blocked

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
            detail = "\n".join(part for part in (stdout, stderr) if part)
            return redact_sensitive_text(
                f"错误：COMMAND_NONZERO_EXIT: 命令执行失败 (code {returncode}):\n{detail}"
            )
        return redact_sensitive_text(stdout)
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
        "--network", _sandbox_network(),
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
        return redact_sensitive_text(result.stdout)
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

    prefer_seatbelt = _sandbox_prefer() == "seatbelt"
    if prefer_seatbelt and _check_seatbelt():
        return _run_seatbelt(cmd, timeout, cwd, writable_root=project_root)

    docker_result = (
        _run_docker(cmd, timeout, cwd, mount_root=project_root)
        if _check_docker()
        else None
    )
    if docker_result is not None:
        return docker_result

    if _check_seatbelt():
        return _run_seatbelt(cmd, timeout, cwd, writable_root=project_root)

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

    prefer_seatbelt = _sandbox_prefer() == "seatbelt"
    if prefer_seatbelt and _check_seatbelt():
        return _run_seatbelt(cmd, timeout, str(root), writable_root=root)

    docker_result = (
        _run_docker(cmd, timeout, str(root), mount_root=root)
        if _check_docker()
        else None
    )
    if docker_result is not None:
        return docker_result

    if _check_seatbelt():
        return _run_seatbelt(cmd, timeout, str(root), writable_root=root)
    return _run_local(
        cmd,
        timeout,
        str(root),
        allow_external_workspace=True,
    )
