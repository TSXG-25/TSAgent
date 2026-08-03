"""test_reflector — Reflection Contract 单测（v2.0-C）。

覆盖 5 约束：
    1. 输入只允许 FailureEvent（reflect(event) 唯一入口）
    2. Correction 是 Proposal（不含执行逻辑）
    3. KPI 边界：Reflection 只负责 diagnosis/correction proposal
    4. Determinism Gate：同 event 多次 reflect → Diagnosis 完全一致
    5. FixCommit：FIXED transition 记录 commit，REGRESSION 可关联首次修复
"""
import sys
import unittest

sys.path.insert(0, ".")

from agent.reflection.reflector import reflect, diagnose, Correction
from evaluation.benchmark.failboard_v2 import (
    FailureEvent, Evidence, FailBoard,
)
from evaluation.benchmark.eval_reflection import load_scenarios, _to_event, determinism_gate


class ReflectorContractTest(unittest.TestCase):

    def _event(self, symptom="timeout", dim="completion"):
        return FailureEvent(
            benchmark="long_horizon", scenario="LH001", layer="long_horizon",
            dimension=dim, failure="run_python 超时",
            evidence=[Evidence(source="tool", location="run_python",
                               expected="30s 内完成", actual="timeout 120s")],
            symptom=symptom,
        )

    # ── 约束 1: 输入只允许 FailureEvent ──

    def test_reflect_rejects_non_event(self):
        with self.assertRaises(Exception):
            reflect({"failure": "dict 不是 FailureEvent"})  # 无 evidence/symptom 属性

    def test_reflect_accepts_event(self):
        r = reflect(self._event())
        self.assertTrue(hasattr(r, "diagnosis"))
        self.assertTrue(hasattr(r, "correction"))

    # ── 约束 2: Correction 是 Proposal（不执行） ──

    def test_correction_is_proposal(self):
        r = reflect(self._event())
        c = r.correction
        self.assertIsInstance(c, Correction)
        # Proposal 只含 action/reason/confidence，不含执行副作用
        self.assertEqual(set(c.__dict__.keys()), {"action", "reason", "confidence"})
        self.assertEqual(c.action, "switch_tool")

    # ── 约束 4: Determinism ──

    def test_determinism_gate(self):
        scenarios = load_scenarios()
        self.assertGreaterEqual(len(scenarios), 10)
        ok, checked = determinism_gate(scenarios, n_runs=100)
        self.assertTrue(ok)
        self.assertEqual(checked, len(scenarios))

    # ── 约束 5: FixCommit ──

    def test_fix_commit_lifecycle(self):
        board = FailBoard([self._event()])
        eid = "long_horizon:LH001:completion"
        board.transition(eid, "CONFIRMED", "确认")
        board.transition(eid, "FIXED", "修复", commit="6ce6b2ad")
        self.assertEqual(board.first_fix_commit(eid), "6ce6b2ad")
        # 回归后再次修复 → fix_commits 记录多次
        board.transition(eid, "REGRESSION", "半年后回归")
        board.transition(eid, "FIXED", "二次修复", commit="a1b2c3d")
        self.assertEqual(board.fix_commits(eid), ["6ce6b2ad", "a1b2c3d"])
        agg = board.aggregate()
        self.assertEqual(agg["regression_counts"].get(eid), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
