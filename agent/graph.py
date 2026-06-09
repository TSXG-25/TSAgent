import ast
import json
from pathlib import Path
from langgraph.graph import StateGraph, END
from langgraph.prebuilt import ToolNode
from langgraph.checkpoint.memory import MemorySaver
from langchain_core.messages import ToolMessage, HumanMessage

from agent.state import AgentState
from agent.tools import TOOLS, apply_patch, run_shell
from agent.llm import llm

llm_with_tools = llm.bind_tools(TOOLS)
tool_node = ToolNode(TOOLS)

MAX_RETRIES = 3

def call_model(state: AgentState):
    print("🧠 Agent 思考...")
    response = llm_with_tools.invoke(state["messages"])
    if response.tool_calls:
        print("🔧 调用工具:", [tc["name"] for tc in response.tool_calls])
    retries = state.get("retries", 0) + 1
    if retries > MAX_RETRIES:
        print("❌ 超过最大重试次数，结束")
        return {"messages": [response], "retries": retries, "should_exit": True}
    return {"messages": [response], "retries": retries}

def should_continue(state: AgentState) -> str:
    if state.get("should_exit"):
        return "finalize"
    last = state["messages"][-1]
    if last.tool_calls:
        return "tools"
    if state.get("patch_content") and not state.get("test_passed"):
        return "test"
    if state.get("test_passed"):
        return "human_review"
    return "finalize"

async def test_node(state: AgentState):
    print("🧪 运行 pytest ...")
    # --cache-clear 避免因只读挂载产生的缓存问题
    raw = await run_shell("pytest src/ --cache-clear")
    result = raw if isinstance(raw, str) else str(raw)
    if "FAILED" in result or "ERROR" in result:
        print("❌ 测试失败，返回 Agent 重试")
        patch_path = state.get("patch_path")
        if patch_path and Path(patch_path).exists():
            Path(patch_path).unlink()
        return {
            "messages": [HumanMessage(content=f"测试失败：\n{result}\n请重新生成 patch。")],
            "test_passed": False,
            "patch_content": None,
            "patch_path": None,
        }
    print("✅ 测试通过")
    return {"test_passed": True}

async def human_review(state: AgentState):
    patch_content = state.get("patch_content")
    patch_path = state.get("patch_path")
    if not patch_content:
        print("⚠️ 无 patch，跳过审核")
        return {"approved": False}
    print("\n📄 待审核的 Patch：")
    print(patch_content)
    choice = input("批准此 patch？(y/N): ").strip().lower()
    if choice == "y":
        result = await apply_patch(patch_path)
        print(result)
        # 无论成功与否，删除 patch 文件
        if patch_path and Path(patch_path).exists():
            Path(patch_path).unlink()
        if "成功" in result:
            print("✅ Patch 已应用")
            return {"approved": True}
        else:
            print("❌ Patch 应用失败，流程结束")
            return {"approved": False}
    else:
        print("❌ 审核拒绝，返回 Agent 重改")
        if patch_path and Path(patch_path).exists():
            Path(patch_path).unlink()
        return {
            "approved": False,
            "patch_content": None,
            "patch_path": None,
            "test_passed": False,
            "messages": [HumanMessage(content="人工审核拒绝，请重新生成 patch。")]
        }

def finalize(state: AgentState):
    print("✅ 流程结束")
    return state

def sync_tool_outputs(state: AgentState):
    for msg in reversed(state["messages"]):
        if isinstance(msg, ToolMessage) and msg.name == "propose_patch_and_record":
            try:
                data = ast.literal_eval(msg.content)
            except:
                try:
                    data = json.loads(msg.content)
                except:
                    print("无法解析 ToolMessage 内容，跳过")
                    return {}
            patch_path = data.get("patch_path")
            patch_content = data.get("patch_content")
            if isinstance(patch_path, str) and "：" in patch_path:
                patch_path = patch_path.split("：", 1)[-1].strip()
            if patch_path and not Path(patch_path).is_absolute():
                patch_path = str(Path.cwd() / patch_path)
            return {
                "patch_path": patch_path,
                "patch_content": patch_content,
            }
    return {}

def build_graph():
    g = StateGraph(AgentState)
    g.add_node("agent", call_model)
    g.add_node("tools", tool_node)
    g.add_node("test", test_node)
    g.add_node("human_review", human_review)
    g.add_node("finalize", finalize)
    g.add_node("sync_tool_outputs", sync_tool_outputs)
    g.set_entry_point("agent")
    g.add_conditional_edges("agent", should_continue, {
        "tools": "tools",
        "test": "test",
        "human_review": "human_review",
        "finalize": "finalize"
    })
    g.add_edge("tools", "sync_tool_outputs")
    g.add_edge("sync_tool_outputs", "agent")
    g.add_edge("test", "human_review")
    g.add_edge("human_review", "finalize")
    g.add_edge("finalize", END)
    return g.compile(checkpointer=MemorySaver())