import subprocess
from pathlib import Path
from mcp_clients.client import MCPAgentClient

client = MCPAgentClient()

async def read_code(path: str) -> str:
    """读取文件（路径相对于 TSAgent 根目录）"""
    result = await client.read_file(path)
    if hasattr(result, "content"):
        texts = [c.text for c in result.content if hasattr(c, "text")]
        return "\n".join(texts)
    return str(result)

async def run_shell(cmd: str) -> str:
    """通过 Docker 沙箱执行命令（用于测试）"""
    result = await client.run_command(cmd)
    if hasattr(result, "content"):
        texts = [c.text for c in result.content if hasattr(c, "text")]
        return "\n".join(texts)
    return str(result)

async def propose_patch_and_record(diff: str) -> dict:
    """生成 patch 文件并返回路径和内容"""
    raw_result = await client.propose_patch(diff)
    patch_path = ""
    if hasattr(raw_result, "content"):
        texts = [c.text for c in raw_result.content if hasattr(c, "text")]
        combined = "".join(texts)
        if "：" in combined:
            patch_path = combined.split("：", 1)[-1].strip()
        else:
            patch_path = combined.strip()
    else:
        patch_path = str(raw_result).strip()
        if "：" in patch_path:
            patch_path = patch_path.split("：", 1)[-1].strip()
    patch_path = str(Path(patch_path).resolve())
    return {"patch_path": patch_path, "patch_content": diff}

async def apply_patch(patch_path: str) -> str:
    """
    在宿主机上应用 patch，优先使用 git apply，失败则用 patch --fuzz
    """
    patch_path = Path(patch_path).resolve()
    if not patch_path.exists():
        return f"Patch 文件不存在: {patch_path}"
    project_root = Path(__file__).parent.parent.resolve()
    
    # 方法1: git apply（更智能）
    try:
        result = subprocess.run(
            ["git", "apply", "--whitespace=fix", "--verbose", str(patch_path)],
            cwd=project_root,
            capture_output=True,
            text=True,
            timeout=10
        )
        if result.returncode == 0:
            return f"Patch 应用成功 (git apply)\n{result.stdout}"
    except Exception:
        pass
    
    # 方法2: patch 命令增加模糊匹配和强制
    try:
        result = subprocess.run(
            ["patch", "-p1", "--fuzz=3", "--force", "-i", str(patch_path)],
            cwd=project_root,
            capture_output=True,
            text=True,
            timeout=10
        )
        if result.returncode == 0:
            return f"Patch 应用成功 (patch --fuzz)\n{result.stdout}"
        else:
            # 尝试去掉路径前缀（如果是 a/ b/ 格式）
            # 有些 patch 可能没有 -p1 匹配，试试 -p0
            result2 = subprocess.run(
                ["patch", "-p0", "--fuzz=3", "--force", "-i", str(patch_path)],
                cwd=project_root,
                capture_output=True,
                text=True,
                timeout=10
            )
            if result2.returncode == 0:
                return f"Patch 应用成功 (patch -p0 --fuzz)\n{result2.stdout}"
            else:
                return f"应用 patch 失败:\n{result.stderr}\n尝试 -p0:\n{result2.stderr}"
    except subprocess.TimeoutExpired:
        return "应用 patch 超时"
    except Exception as e:
        return f"应用 patch 出错: {e}"

TOOLS = [
    read_code,
    propose_patch_and_record,
]