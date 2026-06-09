import asyncio
from mcp_clients.client import MCPAgentClient

async def main():
    client = MCPAgentClient()

    print("=== 读文件 ===")
    content = await client.read_file("hello_world.py")
    print(content[:200])

    print("\n=== 执行命令 ===")
    result = await client.run_command("ls")
    print(result)

    print("\n=== 提 patch ===")
    diff = """--- a/src/main.py
+++ b/src/main.py
@@ -1,3 +1,4 @@
 def hello():
     print("hello")
+    print("world")
"""
    msg = await client.propose_patch(diff)
    print(msg)

if __name__ == "__main__":
    asyncio.run(main())
