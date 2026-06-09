import subprocess
import os

IMAGE = "agent-sandbox"

def run_in_sandbox(cmd: str, timeout=10) -> str:
    try:
        result = subprocess.run(
            [
                "docker", "run",
                "--rm",
                "-v", f"{os.getcwd()}:/workspace",
                IMAGE,
                "bash", "-c", f"cd /workspace && {cmd}"
            ],
            capture_output=True,
            text=True,
            timeout=timeout
        )
        return result.stdout or result.stderr
    except subprocess.TimeoutExpired:
        return "命令执行超时"
