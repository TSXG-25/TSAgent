from fastmcp import FastMCP
from pathlib import Path
import uuid

mcp = FastMCP("patch")

PATCH_DIR = Path("patches")
PATCH_DIR.mkdir(exist_ok=True)

@mcp.tool()
def propose_patch(diff: str) -> str:
    """
    生成一个 unified diff 格式的 patch
    保存到 patches/ 目录
    """
    patch_id = uuid.uuid4().hex[:8]
    patch_path = PATCH_DIR / f"{patch_id}.patch"

    patch_path.write_text(diff)
    return f"Patch 已保存：{patch_path.resolve()}"

if __name__ == "__main__":
    mcp.run(transport="http", host="127.0.0.1", port=8002)

