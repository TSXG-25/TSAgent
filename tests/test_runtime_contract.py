# tests/test_runtime_contract.py
"""Runtime Contract Test — 验证完整主链不中断。

链路：runtime → orchestrator(planner) → intent → chat 直答 → finalizer → memory commit。

通过 mock LLM 避免真实 API 调用，验证主链结构完整性
（这是组件测试之外的关键保障：组件全绿但主链断是最大的重构风险）。
"""
import asyncio
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


class FakeLLM:
    """假 LLM：所有调用返回固定内容。"""

    supports_structured_output = False  # 走 JSON 模式（planner 结构化路径需要真实 provider）

    def __init__(self, content="你好！我是 TSAgent。"):
        self.content = content

    async def ainvoke(self, messages):
        return self

    def invoke(self, messages):
        return self

    @property
    def text(self):
        return self.content

    @property
    def content(self):
        return self._content

    @content.setter
    def content(self, value):
        self._content = value

    def strip(self):
        return self.content


class TestRuntimeContract:
    """端到端主链验证。"""

    def test_agent_chat_pipeline(self, monkeypatch):
        """chat 路径完整走通：run() -> FINISH -> 返回非空答案。"""
        fake = FakeLLM("你好！我是 TSAgent。")
        monkeypatch.setattr("agent.llm.llm", fake)

        from agent.runtime import UniversalAgent

        agent = UniversalAgent("contract_test_user")
        result = asyncio.run(agent.run("你好"))

        assert result
        assert "TSAgent" in result

    def test_agent_execute_pipeline_runs(self, monkeypatch):
        """开放式任务路径：planner -> execute -> finalize 不抛异常。

        注意：真实环境下会调用 LLM 规划，这里验证 orchestrator 编排
        在 mock LLM 下也能完成一次完整往返（即使计划为空）。
        """
        fake = FakeLLM('{"action": "finish", "reason": "完成"}')
        monkeypatch.setattr("agent.llm.llm", fake)

        from agent.runtime import UniversalAgent

        agent = UniversalAgent("contract_test_user2")
        # 触发非 chat 路径（requires_execution），但 mock LLM 不产出有效 plan，
        # 验证整个状态机不会崩溃，总能回到 FINISH。
        result = asyncio.run(agent.run("读取 output/solution.py"))
        assert result is not None

    def test_orchestrator_import_contract(self):
        """orchestrator 包结构与公共 API 契约。"""
        from agent.orchestrator import ExecutionOrchestrator

        orch = ExecutionOrchestrator()
        for method in ("plan", "execute", "replan", "finalize", "get_timings", "reset_timings"):
            assert callable(getattr(orch, method)), f"missing {method}"
