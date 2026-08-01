# tests/test_planner_contract.py
"""Contract tests — Task schema, Compiler four stages, SSA static check.

ADR-0001 / ADR-0002:
- Task is the only task model (Pydantic, model_validate is the validator)
- Compiler: Normalize → Semantic Check → Lower → Static Check
- Compile-time errors, not runtime errors
"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from pydantic import ValidationError

from agent.task import Task, Verb, ExecutionPlan, ExecutionStep
from agent.compiler.tool_selector import Compiler, CompileError
from agent.compiler.rules import DEFAULT_RULES


def make_compiler():
    c = Compiler()
    for rule in DEFAULT_RULES:
        c.add_rule(rule)
    return c


class TestTaskContract:
    """Task 是系统唯一任务模型，model_validate 即校验器。"""

    def test_from_dict_infers_target_type(self):
        t = Task.from_dict({"id": "t1", "verb": "read", "target": "output/solution.py", "goal": "g"})
        assert t.target_type == "file"

        t2 = Task.from_dict({"id": "t2", "verb": "explain", "target": "ExecutionOrchestrator", "goal": "g"})
        assert t2.target_type == "symbol"

        t3 = Task.from_dict({"id": "t3", "verb": "explain", "target": "", "goal": "解释"})
        assert t3.target_type == "none"

    def test_file_target_cannot_be_empty(self):
        with pytest.raises(ValidationError):
            Task(id="t1", verb=Verb.READ, target="", target_type="file")

    def test_invalid_target_type_rejected(self):
        with pytest.raises(ValidationError):
            Task(id="t1", verb=Verb.READ, target="x.py", target_type="bogus")

    def test_to_dict_roundtrip(self):
        t = Task(id="t1", verb=Verb.MODIFY, target="a.py", target_type="file", goal="g")
        d = t.to_dict()
        assert d["verb"] == "modify"
        t2 = Task.from_dict(d)
        assert t2.target_type == "file"


class TestCompilerStages:
    """Compiler 四阶段：Normalize → Semantic → Lower → Static。"""

    def setup_method(self):
        self.compiler = make_compiler()

    def test_normalize_strips_quotes(self):
        task = Task(id="t1", verb=Verb.READ, target='"a.py"', target_type="file", goal="g")
        plan = self.compiler.compile(task)
        assert plan.task.target == "a.py"

    def test_semantic_rejects_chinese_file_target(self):
        task = Task(id="t1", verb=Verb.MODIFY, target="计算模块文件", target_type="file", goal="g")
        with pytest.raises(CompileError):
            self.compiler.compile(task)

    def test_semantic_rejects_empty_file_target(self):
        # 模型层（Task 构造）即拒绝：空 file target 是 ValidationError
        from pydantic import ValidationError as VE
        with pytest.raises(VE):
            Task(id="t1", verb=Verb.READ, target="", target_type="file", goal="g")

    def test_text_target_lowers_to_llm(self):
        task = Task(id="t1", verb=Verb.EXPLAIN, target="为什么 Transformer", target_type="text", goal="g")
        plan = self.compiler.compile(task)
        assert plan.executor == "llm"

    def test_file_target_compiles_to_tool(self):
        task = Task(id="t1", verb=Verb.READ, target="output/solution.py", target_type="file", goal="g")
        plan = self.compiler.compile(task)
        assert plan.executor == "tool"
        assert len(plan.steps) >= 1


class TestExecutionPlanContract:
    """SSA 静态检查：编译期报错，不进运行时。"""

    def setup_method(self):
        self.compiler = make_compiler()

    def _compile_plan(self, steps):
        task = Task(id="t1", verb=Verb.READ, target="x.py", target_type="file", goal="g")
        return ExecutionPlan(task=task, steps=steps)

    def test_duplicate_output_rejected(self):
        plan = self._compile_plan([
            ExecutionStep(tool="workspace", args={"spec": "x"}, outputs=["path"]),
            ExecutionStep(tool="read_file", args={"path": "$path"}, outputs=["path"]),  # 重复 path
        ])
        with pytest.raises(CompileError, match="重复产出"):
            self.compiler._static_check(plan)

    def test_undefined_variable_rejected(self):
        plan = self._compile_plan([
            ExecutionStep(tool="read_file", args={"path": "$nope"}, outputs=["content"]),
        ])
        with pytest.raises(CompileError, match="未定义变量"):
            self.compiler._static_check(plan)

    def test_empty_outputs_rejected(self):
        plan = self._compile_plan([
            ExecutionStep(tool="workspace", args={"spec": "x"}, outputs=[]),
        ])
        with pytest.raises(CompileError, match="outputs 为空"):
            self.compiler._static_check(plan)

    def test_valid_chain_passes(self):
        plan = self._compile_plan([
            ExecutionStep(tool="workspace", args={"spec": "x.py"}, outputs=["path"]),
            ExecutionStep(tool="read_file", args={"path": "$path"}, outputs=["content"]),
        ])
        self.compiler._static_check(plan)  # 不抛异常

    def test_tool_must_exist_in_registry(self):
        # registry 提供时，不存在的工具 → 编译期错误
        class FakeRegistry:
            def get(self, name):
                return {"read_file": object(), "write_file": object()}.get(name)

        from agent.compiler.context import CompilerContext
        from agent.task import ExecutionPlan, ExecutionStep

        task = Task(id="t1", verb=Verb.READ, target="x.py", target_type="file", goal="g")
        plan = ExecutionPlan(task=task, steps=[ExecutionStep(tool="nonexistent_tool", args={}, outputs=["o"])])
        with pytest.raises(CompileError, match="工具不存在"):
            self.compiler._static_check(plan, CompilerContext(registry=FakeRegistry()))

    def test_filesystem_prefix_maps_to_registered_tool(self):
        from agent.compiler.context import CompilerContext
        from agent.task import ExecutionPlan, ExecutionStep

        class FakeRegistry:
            def get(self, name):
                return {"read_file": object()}.get(name)

        task = Task(id="t1", verb=Verb.READ, target="x.py", target_type="file", goal="g")
        plan = ExecutionPlan(task=task, steps=[
            ExecutionStep(tool="filesystem.read", args={"path": "a"}, outputs=["c"]),
        ])
        self.compiler._static_check(plan, CompilerContext(registry=FakeRegistry()))

    def test_compile_runs_static_check(self):
        # 完整编译入口也触发 static check：LLM executor 无步骤则跳过
        task = Task(id="t1", verb=Verb.EXPLAIN, target="解释", target_type="text", goal="g")
        plan = self.compiler.compile(task)
        assert plan.is_llm
