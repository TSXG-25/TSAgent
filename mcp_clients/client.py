from fastmcp import Client

class MCPAgentClient:
    def __init__(self):
        self.fs = Client("http://localhost:8000/mcp")
        self.shell = Client("http://localhost:8001/mcp")
        self.patch = Client("http://localhost:8002/mcp")

    async def read_file(self, path: str) -> str:
        async with self.fs:
            return await self.fs.call_tool("read_file", {"path": path})

    async def run_command(self, cmd: str) -> str:
        async with self.shell:
            return await self.shell.call_tool("shell", {"cmd": cmd})

    async def propose_patch(self, diff: str) -> str:
        async with self.patch:
            return await self.patch.call_tool("propose_patch", {"diff": diff})

