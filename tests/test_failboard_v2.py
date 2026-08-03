"""test_failboard_v2 — Diagnostic Backbone 单测（v2.0 统一诊断基础设施）。

覆盖三原则：
    1. Event 不可变（Event Sourcing）：immutable + Transition 追加
    2. Evidence 结构化：source/location/expected/actual
    3. Root Cause 唯一来源 = SYMPTOM_MAP（Benchmark 只产 symptom+evidence）
    4. 生命周期：NEW→CONFIRMED→FIXED→REGRESSION→CLOSED + regression_count
    5. query 定位：Long Horizon FAIL → query(layer, dimension) 直达
"""
import sys
import unittest

sys.path.insert(0, ".")

from evaluation.benchmark.failboard_v2 import (
    FailBoard, FailureEvent, FailureTransition, Evidence, SYMPTOM_MAP,
)


class FailBoardV2Test(unittest.TestCase):

    def _make_event(self, symptom="missing_constraint", dim="constraint"):
        return FailureEvent(
            benchmark="planning", scenario="PL002", layer="planning", dimension=dim,
            failure="no_web 违反",
            evidence=[Evidence(source="semantic_validator", location="task[2]",
                               expected="no_web", actual="search web")],
            symptom=symptom,
        )

    # ── 原则 1: Event 不可变（immutable dataclass） ──

    def test_event_immutable(self):
        e = self._make_event()
        with self.assertRaises(Exception):
            e.dimension = "goal"  # frozen dataclass → 抛异常

    def test_transition_append_only(self):
        board = FailBoard([self._make_event()])
        eid = "planning:PL002:constraint"
        board.transition(eid, "CONFIRMED", "确认")
        board.transition(eid, "FIXED", "修复")
        board.transition(eid, "REGRESSION", "回归")
        board.transition(eid, "FIXED", "二次修复")
        board.transition(eid, "CLOSED", "关闭")
        self.assertEqual(board.current_status(eid), "CLOSED")
        agg = board.aggregate()
        self.assertEqual(agg["regression_counts"].get(eid), 1)  # 修过 2 次（1 次回归）

    def test_illegal_transition(self):
        board = FailBoard([self._make_event()])
        eid = "planning:PL002:constraint"
        with self.assertRaises(ValueError):
            board.transition(eid, "CLOSED")  # NEW → CLOSED 非法

    # ── 原则 2: Evidence 结构化 ──

    def test_evidence_structured(self):
        e = self._make_event()
        ev = e.evidence[0]
        self.assertEqual(ev.expected, "no_web")
        self.assertEqual(ev.actual, "search web")
        self.assertEqual(ev.source, "semantic_validator")

    # ── 原则 3: Root Cause 唯一来源 = SYMPTOM_MAP ──

    def test_root_cause_from_symptom_map(self):
        board = FailBoard([self._make_event(symptom="missing_constraint")])
        resolved = board.resolve()[0]
        m = SYMPTOM_MAP["missing_constraint"]
        self.assertEqual(resolved["root_cause"], m["root_cause"])
        self.assertEqual(resolved["root_cause"], "planning")
        self.assertEqual(resolved["correction"], "replanning")

    def test_invalid_symptom_rejected(self):
        with self.assertRaises(ValueError):
            self._make_event(symptom="planner_bug")  # Benchmark 不能自造 symptom

    # ── query 定位（Long Horizon FAIL → query 直达） ──

    def test_query_by_layer_dimension(self):
        board = FailBoard([
            self._make_event(),
            FailureEvent(benchmark="long_horizon", scenario="LH001", layer="long_horizon",
                         dimension="drift", failure="Context Drift",
                         symptom="context_drift"),
        ])
        self.assertEqual(len(board.query(layer="long_horizon", dimension="drift")), 1)
        self.assertEqual(len(board.query(status="NEW")), 2)
        self.assertEqual(len(board.query(layer="planning", dimension="constraint")), 1)

    def test_same_bug_lifecycle_dedup(self):
        board = FailBoard()
        board.collect([self._make_event()])
        added = board.collect([self._make_event()])  # 同一 id → 不重复建档
        self.assertEqual(added, 0)
        self.assertEqual(len(board._events), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
