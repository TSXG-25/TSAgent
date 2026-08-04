# main.py
import os
os.environ["TRANSFORMERS_NO_TF"] = "1"
os.environ["TF_CPP_MIN_LOG_LEVEL"] = "3"
os.environ["TOKENIZERS_PARALLELISM"] = "false"

import asyncio
from pathlib import Path
from agent import TSAgent
from agent.bootstrap import load_all, load_all_async
from agent.services.repository_service import RepositoryService
from agent.event_bus import event_bus

class StreamingCLI:
    """CLI with streaming output and event visualization."""
    
    def __init__(self):
        self.current_task = None
        self.task_start_time = None
        
    def setup_event_listeners(self):
        """Subscribe to events for real-time visualization."""
        event_bus.subscribe("task_start", self._on_task_start)
        event_bus.subscribe("task_end", self._on_task_end)
        event_bus.subscribe("reflection", self._on_reflection)
        
    def _on_task_start(self, data):
        """Handle task start event."""
        self.current_task = data.get("task", "Unknown task")
        self.task_start_time = asyncio.get_event_loop().time()
        print(f"\n🔄 开始任务: {self.current_task}")
        
    def _on_task_end(self, data):
        """Handle task end event."""
        task = data.get("task", "Unknown task")
        status = data.get("status", "unknown")
        duration = ""
        if self.task_start_time:
            duration = f" ({asyncio.get_event_loop().time() - self.task_start_time:.2f}s)"
        
        if status == "succeeded":
            output = data.get("output", "")
            if output and len(output) > 100:
                output = output[:100] + "..."
            print(f"✅ 任务完成: {task}{duration}")
            if output:
                print(f"   输出: {output}")
        else:
            error = data.get("error", "Unknown error")
            print(f"❌ 任务失败: {task}{duration}")
            print(f"   错误: {error}")
        
        self.current_task = None
        self.task_start_time = None
        
    def _on_reflection(self, data):
        """Handle reflection event."""
        success = data.get("success", False)
        confidence = data.get("confidence", 0.0)
        reason = data.get("replan_reason", "")
        
        if success:
            print(f"🤔 反思: 成功 (置信度: {confidence:.2f})")
        else:
            print(f"🤔 反思: 需要重规划 (置信度: {confidence:.2f})")
            if reason:
                print(f"   原因: {reason}")

async def main():
    # ── Boot sequence (frozen order) ──
    # 1. load_config()        ← placeholder
    # 2. init_event_bus()     ← already in event_bus.py
    # 3. init_workspace()     ← inside load_all() now
    # 4-6. tools, skills, workflows
    # 7. build_knowledge()    ← placeholder
    # 8. repository (async)
    load_all()
    await load_all_async()  # background: symbols + repository index

    print("🤖 TSAgent 启动")
    user_id = "TSXG"

    # Setup streaming CLI with event visualization
    cli = StreamingCLI()
    cli.setup_event_listeners()

    agent = TSAgent(user_id)
    while True:
        user_input = input("\n你: ")
        if user_input.lower() in ["exit", "quit"]:
            break
        print("\n📡 处理中...")
        response = await agent.run(user_input)
        print(f"\n🤖 Agent: {response}")

if __name__ == "__main__":
    asyncio.run(main())
