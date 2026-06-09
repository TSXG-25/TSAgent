from fastmcp import FastMCP
from sandbox.runner import run_in_sandbox

mcp = FastMCP("shell")

@mcp.tool()
def shell(cmd: str) -> str:
    """
    在 Docker 沙箱中执行 shell 命令
    """
    return run_in_sandbox(cmd)

if __name__ == "__main__":
    mcp.run(transport="http", host="127.0.0.1", port=8001)
