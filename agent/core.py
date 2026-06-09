from agent.tools import calc, terminal, propose_patch, TOOLS
from agent.llm import chat
import json

class ReActAgent:
    def __init__(self, max_steps=10):
        self.messages = [{
            "role": "system",
            "content": ("你是一个需要调用工具给出正确行为的agent, 你不能直接修改 src 目录下的文件。你只能生成 unified diff 格式的 patch。所有 patch 会被保存到 patches/ 目录，由人类决定是否应用。")
        }]
        self.max_steps = max_steps

    def run(self, user_input: str):
        self.messages.append({"role": "user", "content": user_input})

        for step in range(self.max_steps):
            response = chat(self.messages, tools=TOOLS)
            message = response.choices[0].message

            # 没有工具调用 → 结束
            if not message.tool_calls:
                self.messages.append(message)
                print("\n🤖 Agent:", message.content)
                break

            # 处理 tool_calls
            self.messages.append(message)

            for tool_call in message.tool_calls:
                name = tool_call.function.name
                args = json.loads(tool_call.function.arguments)

                print(f"\n🔧 调用工具: {name} | 参数: {args}")

                if name == "calc":
                    result = calc(**args)
                elif name == "terminal":
                    result = terminal(**args)
                elif name == "propose_patch":
                    result == propose_patch(**args)
                else:
                    result = "未知工具"

                self.messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": result
                })

        return self.messages
