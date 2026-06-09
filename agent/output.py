from agent.state import AgentState

def format_result(state: AgentState) -> str:
    if not state["patch"]:
        return "未生成 patch。"

    status = "✅ 已批准" if state["approved"] else "❌ 已拒绝"

    return f"""
Patch 路径：{state['patch']}
审批结果：{status}

Patch 内容：
{state['patch']}
""".strip()
