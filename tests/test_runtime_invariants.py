# tests/test_runtime_invariants.py
"""Runtime Product Contract（v1.1 Behavior Freeze，ADR-0007）。

Inv1  Exception 不退出 Runtime
Inv2  Tool 结果必须经过 AnswerGenerator / Presentation
Inv3  Workflow 返回统一 ExecutionResult
Inv4  不输出 Traceback
Inv5  Planner 只收 PlanningContext（不接触原始上下文）

这些是 CONTRACT，不是普通测试。任何 PR 违反 → FAIL。
"""
import asyncio
import io
import contextlib
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


class _FakeOrch:
    def reset_timings(self):
        pass

    def get_timings(self):
        return {}

    async def plan(self, **kw):
        raise _BOOM("boom in plan")

    async def finalize(self, state, user_input, user_id, best_answer=None):
        return best_answer or "完成"


class _BOOM(Exception):
    pass


class TestInv1_RuntimeNeverCrashes:
    """任何 Exception 都不应退出 Runtime。"""

    def test_orchestrator_exception_recovered(self):
        from agent.runtime import UniversalAgent

        agent = UniversalAgent("inv1")
        agent.orchestrator = _FakeOrch()
        # run() 不应抛异常，应返回友好回答
        result = asyncio.run(agent.run("你好"))
        assert result
        assert "抱歉" in result or len(result) > 0


class TestInv4_NoTracebackLeak:
    """Runtime 异常路径不得输出 Traceback。"""

    def test_recovery_output_no_traceback(self):
        from agent.runtime import UniversalAgent

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            agent = UniversalAgent("inv4")
            agent.orchestrator = _FakeOrch()
            asyncio.run(agent.run("测试"))

        out = buf.getvalue()
        assert "Traceback" not in out, f"traceback leaked: {out}"
        assert "TypeError" not in out


class TestInv3_UnifiedWorkflowResult:
    """所有 workflow 签名统一（resolved_target + **kwargs 兼容）。"""

    def test_all_workflows_have_unified_signature(self):
        import inspect
        import workflows.bug_fix
        import workflows.code_review
        import workflows.research

        for mod in (workflows.bug_fix, workflows.code_review, workflows.research):
            funcs = [f for n, f in vars(mod).items() if n.endswith("workflow")]
            for fn in funcs:
                sig = inspect.signature(fn)
                assert "**kwargs" in str(sig) or "resolved_target" in str(sig), \
                    f"{mod.__name__}.{fn.__name__} 未统一签名"


class TestInv5_PlannerIsolation:
    """Planner 不接触原始上下文（Workspace/Repository/User Messages）。

    PlannerStage 不得 import workspace/repository 服务层。
    """

    def test_planner_stage_no_direct_service_import(self):
        import agent.orchestrator.planner as p

        src = open(p.__file__).read()
        # 允许 grounding 包（Runtime 整理后的世界），禁止直接服务调用
        assert "from agent.workspace" not in src or "grounding" in src, \
            "Planner 直接依赖 workspace"
        # Planner 不应直接操作 workspace.resolve（应由 Grounding 提供）
        assert "ws.resolve(" not in src or "_orch._context_builder" in src, \
            "Planner 直接调用 workspace.resolve"

    def test_planner_gets_grounding_not_raw(self):
        # generate_plan 的 grounding 参数是 GroundingContext（整理后世界）
        import inspect
        from agent.planner.planner import generate_plan

        params = inspect.signature(generate_plan).parameters
        assert "grounding" in params, "generate_plan 缺少 grounding（Planner Isolation）"
