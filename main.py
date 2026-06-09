from agent.graph import build_graph
from langchain_core.messages import SystemMessage, HumanMessage

async def main():
    graph = build_graph()

    initial_state = {
        "messages": [
            SystemMessage(content="""
你是一个代码修改助手。

重要规则：
- 所有文件路径必须相对于项目根目录 `TSAgent`，例如 `src/hello_world.py`。
- 生成 unified diff 时，必须使用 `--- a/src/hello_world.py` 和 `+++ b/src/hello_world.py` 格式。
- 必须包含至少 3 行上下文，确保 hunk header 准确。
- 必须使用 `propose_patch_and_record` 生成 patch。
- 生成 patch 后，**不要**自己调用 `apply_patch` 或 `run_shell`。
- 等待系统自动测试和人工审核。
- 每个修改只能生成一个 patch，不要重复生成。
"""),
            HumanMessage(content="在 src/hello_world.py 末尾加一行 print('hello')")
        ]
    }

    async for event in graph.astream(
        initial_state,
        config={"configurable": {"thread_id": "demo"}}
    ):
        for node_name in event:
            print(f"➡️  {node_name}")

if __name__ == "__main__":
    import asyncio
    asyncio.run(main())