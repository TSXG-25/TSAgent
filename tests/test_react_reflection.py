"""test_react_reflection — ReAct 失败路径的 Reflection 接入单测（v2.0-C Stage 2）。

验证：
    - _symptom_from_observation 确定性判定（timeout/hallucination/missing_constraint/wrong_answer）
    - _reflect_failure 填充 task['_reflection']（诊断 + Correction Proposal）
    - Correction 是 Proposal：不执行，只注入 hint 供 LLM 决定
"""
import sys
import unittest

sys.path.insert(0, ".")


class ReactReflectionTest(unittest.TestCase):

    def setUp(self):
        from agent.executor.executors.react import ReactExecutor
        self.exec = object.__new__(ReactExecutor)

    def _obs(self, summary, tool="run_python"):
        return {"status": "failed", "summary": summary, "tool_used": tool, "action": tool}

    # ── symptom 确定性判定 ──

    def test_symptom_timeout(self):
        s = self.exec._symptom_from_observation(self._obs("命令执行超时 (timeout 120s)"))
        self.assertEqual(s, "timeout")

    def test_symptom_hallucination(self):
        s = self.exec._symptom_from_observation(self._obs("文件不存在: output/nonexist.py"))
        self.assertEqual(s, "hallucination")

    def test_symptom_missing_constraint(self):
        s = self.exec._symptom_from_observation(self._obs("安全策略拦截: 禁止安装命令"))
        self.assertEqual(s, "missing_constraint")

    def test_symptom_wrong_answer(self):
        s = self.exec._symptom_from_observation(self._obs("运行报错: TypeError: ..."))
        self.assertEqual(s, "wrong_answer")

    # ── reflect_failure：诊断 + Correction Proposal ──

    def test_reflect_failure_populates_task(self):
        task = {"id": "task-1", "goal": "读取文件"}
        self.exec._reflect_failure(task, self._obs("文件不存在: output/nonexist.py"))
        refl = task["_reflection"]
        self.assertEqual(refl["root_cause"], "grounding")
        self.assertIn("correction", refl)
        self.assertEqual(refl["correction"], "re_ground")

    def test_reflect_failure_timeout(self):
        task = {"id": "task-2", "goal": "运行测试"}
        self.exec._reflect_failure(task, self._obs("执行超时 (timeout)"))
        self.assertEqual(task["_reflection"]["root_cause"], "tool")
        self.assertEqual(task["_reflection"]["correction"], "switch_tool")

    def test_reflection_failure_does_not_block(self):
        """Reflection 异常不阻塞执行（兜底 correction 仍产出）。"""
        task = {"id": "task-3"}
        # None 观察 → obs={} → evidence 空 → symptom=wrong_answer → decision（symptom 默认）
        self.exec._reflect_failure(task, None)
        self.assertIn("_reflection", task)
        self.assertEqual(task["_reflection"]["root_cause"], "decision")

    def test_correction_is_proposal_only(self):
        """Reflection 只写入 task['_reflection']（hint），不执行任何工具/修正。"""
        task = {"id": "task-4"}
        self.exec._reflect_failure(task, self._obs("文件不存在: x.py"))
        self.assertIn("_reflection", task)
        # 只包含诊断/proposal 字段（不含执行副作用）
        self.assertEqual(
            set(task["_reflection"].keys()),
            {"root_cause", "confidence", "correction", "reason"},
        )

    # ── v2.0-D Decision 接入 ──

    def test_decide_next_grounding_switch(self):
        task = {"id": "task-5", "recent_failures": []}
        self.exec._reflect_failure(task, self._obs("文件不存在: x.py"))
        action = self.exec._decide_next(task, self._obs("文件不存在: x.py"))
        # grounding + hallucination → 细化 diagnosis=hallucination → 策略仍 switch
        self.assertEqual(action, "switch")
        self.assertEqual(task["_decision"]["diagnosis"], "hallucination")

    def test_decide_next_timeout_retry(self):
        task = {"id": "task-6", "recent_failures": []}
        self.exec._reflect_failure(task, self._obs("命令执行超时 (timeout)"))
        action = self.exec._decide_next(task, self._obs("命令执行超时 (timeout)"))
        self.assertEqual(action, "retry")
        self.assertEqual(task["_decision"]["diagnosis"], "tool_timeout")

    def test_decide_next_retry_exhausted_switch(self):
        # 3 次失败（重试耗尽）→ switch
        task = {"id": "task-7",
                "recent_failures": [
                    {"tool": "run_python", "error": "timeout", "time": 1},
                    {"tool": "run_python", "error": "timeout", "time": 2},
                    {"tool": "run_python", "error": "timeout", "time": 3},
                ]}
        self.exec._reflect_failure(task, self._obs("命令执行超时 (timeout)"))
        action = self.exec._decide_next(task, self._obs("命令执行超时 (timeout)"))
        self.assertEqual(action, "switch")
        self.assertEqual(task["_decision"]["rule"], "tool_timeout_exhausted")

    def test_refined_diagnosis_mapping(self):
        m = self.exec._refined_diagnosis
        self.assertEqual(m("tool", "timeout"), "tool_timeout")
        self.assertEqual(m("tool", "missing_constraint"), "permission_denied")
        self.assertEqual(m("grounding", "hallucination"), "hallucination")
        self.assertEqual(m("planning", "context_drift"), "context_drift")
        self.assertEqual(m("external", ""), "external_failure")
        self.assertEqual(m("unknown", ""), "unknown")


if __name__ == "__main__":
    unittest.main(verbosity=2)
