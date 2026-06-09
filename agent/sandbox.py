import subprocess
import tempfile
from pathlib import Path

SANDBOX_IMAGE = "agent-sandbox"
DEFAULT_TIMEOUT = 10

def run_in_sandbox(cmd: str, timeout: int = DEFAULT_TIMEOUT) -> str:
    project_root = Path(__file__).resolve().parent.parent
    with tempfile.TemporaryDirectory() as tmpdir:
        try:
            result = subprocess.run(
                [
                    "docker", "run",
                    "--rm",
                    "--network", "none",
                    "--cpus", "1.0",
                    "--memory", "512m",
                    "-v", f"{project_root}:/workspace",   # 可读写挂载
                    "-w", "/workspace",
                    SANDBOX_IMAGE,
                    "bash", "-c", cmd
                ],
                capture_output=True,
                text=True,
                timeout=timeout
            )
            if result.returncode != 0:
                return f"命令执行失败:\n{result.stderr}"
            return result.stdout.strip()
        except subprocess.TimeoutExpired:
            return "命令执行超时"