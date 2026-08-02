import json
from langchain_core.messages import SystemMessage, HumanMessage
from agent.llm import llm

async def generate_final_answer(state, user_input: str) -> str:
    artifacts = state.get("artifacts", {})
    
    search_results = artifacts.get("search_results", "")
    
    # If we have real search results, present them directly
    if search_results and "未找到" not in search_results and len(search_results) > 50:
        from datetime import datetime
        now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        response = await llm.ainvoke(
            [
                SystemMessage(content=f"""
你是一个专业的问答助手。你需要根据网络搜索结果，生成一份**详细、完整、有条理**的回答。

当前时间：{now_str}

规则（非常重要）：
1. 直接根据搜索结果回答，不要说你找不到信息。即使搜索结果不完美，也要利用已有的信息尽力作答。
2. 使用自然流畅的中文，像 ChatGPT 那样输出详细的段落内容。
3. 回答要**充实**：综合多个来源的信息，展开论述，用段落、小标题、列表等方式组织内容。
4. 如果搜索结果包含具体数据、日期、数字等信息，一定要在回答中体现出来。
5. 如果有多个来源，可以交叉对比它们的说法。
6. 在回答末尾列出参考来源链接。
7. 不要道歉，不要说"抱歉"。
8. 回答长度：根据搜索结果的信息量，输出尽可能充实完整的内容。
"""),
                HumanMessage(content=f"用户问题：{user_input}\n\n网络搜索结果：\n{search_results[:8000]}"),
            ]
        )
        return response.content

    # Fallback: return raw search results as-is
    if search_results and len(search_results) > 10:
        return f"以下是搜索结果：\n\n{search_results[:4000]}"

    # Fallback: use last_output from artifacts (e.g. shell command output)
    last_output = artifacts.get("last_output", "")
    if last_output and len(last_output) > 3:
        response = await llm.ainvoke(
            [
                SystemMessage(content="你是一个问答助手。根据可用的信息直接回答用户问题，不要说你找不到。"),
                HumanMessage(content=f"用户问题：{user_input}\n\n可用的信息：\n{last_output[:2000]}"),
            ]
        )
        return response.content

    # Truly no data — return last observation summary if available
    # Fix: use isinstance() to check message type, NOT content.startswith("System")
    # which was incorrectly returning the entire SystemMessage (full memory context) as the answer.
    for task in reversed(state.get("plan", [])):
        obs = task.get("observations", [])
        if obs:
            last_obs = obs[-1]
            summary = last_obs.get("summary", "")
            if summary and len(summary) > 5:
                return summary

    # Last resort: return last non-System message content
    for msg in reversed(state.get("messages", [])):
        if not isinstance(msg, SystemMessage):
            if hasattr(msg, 'content') and isinstance(msg.content, str) and len(msg.content) > 10:
                return msg.content

    return f"抱歉，未能查到相关信息。"
