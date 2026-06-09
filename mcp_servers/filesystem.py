from fastmcp import FastMCP
from pathlib import Path

mcp = FastMCP("filesystem")
# 项目根目录为 TSAgent，即本文件所在目录的父目录的父目录
ROOT = Path(__file__).parent.parent.resolve()

def safe_resolve(path: str):
    target = (ROOT / path).resolve()
    if not str(target).startswith(str(ROOT)):
        return {"error": f"非法路径：{path}，必须在 {ROOT} 目录下"}
    if not target.exists():
        return {"error": f"文件不存在：{target}"}
    return {"content": target.read_text()}

@mcp.tool()
def read_file(path: str) -> dict:
    """读取 TSAgent 根目录下的任意文件（如 src/hello_world.py 或 main.py）"""
    return safe_resolve(path)

if __name__ == "__main__":
    mcp.run(transport="http", host="127.0.0.1", port=8000)