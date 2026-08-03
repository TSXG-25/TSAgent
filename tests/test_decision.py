"""test_decision — Decision Policy 单测（v2.0-D）。

覆盖：
    - Policy Matrix 各诊断 → 正确决策（DS boundary）
    - retry exhausted → switch（重试耗尽剔除）
    - Confidence Gate：组合置信 < 0.5 → 降级 Ask
    - DecisionTrace 可解释性字段（rule/confidence/rejected）
    - 动作集合固定（retry/switch/ask/finish，不扩展）
"""
import sys
import unittest

sys.path.insert(0, ".")

from agent.decision.decision import (
    decide, DecisionInput, ExecutionState,
    POLICY_TABLE, ACTIONS,
)


def _decide(diagnosis, conf=0.9, retry=0, completeness=1.0):
    inp = DecisionInput(
        diagnosis=diagnosis, diagnosis_confidence=conf,
        state=ExecutionState(retry_count=retry, evidence_completeness=completeness),
    )
    return decide(inp)


class DecisionTest(unittest.TestCase):

    def test_actions_fixed(self):
        self.assertEqual(ACTIONS, {"retry", "switch", "ask", "finish"})

    # ── Policy Matrix 各诊断 ──

    def test_timeout_fresh_retry(self):
        d, t = _decide("tool_timeout", retry=0)
        self.assertEqual(d.action, "retry")
        self.assertIn(t.policy_rule, ("tool_timeout_default", "tool_timeout_exhausted"))

    def test_timeout_exhausted_switch(self):
        d, t = _decide("tool_timeout", retry=3)
        self.assertEqual(d.action, "switch")

    def test_permission_ask(self):
        d, _ = _decide("permission_denied", conf=0.9)
        self.assertEqual(d.action, "ask")

    def test_grounding_switch(self):
        d, _ = _decide("grounding_miss")
        self.assertEqual(d.action, "switch")

    def test_external_fresh_retry(self):
        d, _ = _decide("external_failure", retry=1)
        self.assertEqual(d.action, "retry")

    def test_external_exhausted_ask(self):
        d, _ = _decide("external_failure", retry=5)
        self.assertEqual(d.action, "ask")

    def test_context_drift_retry(self):
        d, _ = _decide("context_drift")
        self.assertEqual(d.action, "retry")

    # ── Confidence Gate ──

    def test_low_confidence_force_ask(self):
        # hallucination 默认 switch，但 conf=0.4 < 0.5 → ask
        d, t = _decide("hallucination", conf=0.4)
        self.assertEqual(d.action, "ask")
        self.assertIn("gate", t.policy_rule)

    def test_high_confidence_keeps_policy(self):
        d, _ = _decide("hallucination", conf=0.9)
        self.assertEqual(d.action, "switch")

    def test_unknown_ask(self):
        d, _ = _decide("unknown", conf=0.2)
        self.assertEqual(d.action, "ask")

    # ── DecisionTrace 可解释性 ──

    def test_trace_fields(self):
        _, t = _decide("tool_timeout", retry=3)
        self.assertEqual(t.chosen_action, "switch")
        self.assertIn("retry", t.rejected_actions)
        self.assertGreaterEqual(t.confidence, 0.0)
        self.assertTrue(t.decision_id)

    def test_all_diagnoses_have_policy(self):
        # 全部诊断都有策略（含兜底 unknown）
        for d in ["tool_timeout", "permission_denied", "grounding_miss",
                  "hallucination", "constraint_violation", "context_drift",
                  "external_failure", "unknown"]:
            self.assertIn(d, POLICY_TABLE)


if __name__ == "__main__":
    unittest.main(verbosity=2)
