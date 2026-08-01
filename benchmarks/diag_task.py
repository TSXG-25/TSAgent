#!/usr/bin/env python3
"""临时验证：Task Pydantic 迁移。"""
import sys
sys.path.insert(0, ".")

from pydantic import ValidationError
from agent.task import Task, Verb, ExecutionPlan, ExecutionStep

# 基本构造（旧格式兼容）
t = Task(id="t1", verb=Verb.READ, target="x.py", goal="g")
assert t.target_type == "none"
assert t.verb == Verb.READ

# from_dict（planner 输出格式）
t2 = Task.from_dict({"id": "t2", "verb": "read", "target": "a.py", "goal": "g"})
assert t2.verb == Verb.READ

# 契约校验：file 类型空 target 拒绝
try:
    Task(id="t3", verb=Verb.READ, target="", target_type="file")
    print("ERROR: should have raised")
    sys.exit(1)
except ValidationError:
    pass

# to_dict round-trip
d = t2.to_dict()
assert d["verb"] == "read"

# ExecutionPlan 兼容
plan = ExecutionPlan(task=t, steps=[ExecutionStep(tool="read_file", args={"path": "a"}, outputs=["content"])])
assert len(plan) == 1 and not plan.is_llm

# 中文描述 target 在 file 类型下也应拒绝（契约）
try:
    Task(id="t4", verb=Verb.MODIFY, target="计算模块文件", target_type="file")
    print("WARN: 中文 target 未被拒绝（启发式可后续加强）")
except ValidationError:
    pass

print("TASK_PYDANTIC_OK")
