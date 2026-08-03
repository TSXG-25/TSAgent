"""test_constraint_extractor — v2.0-A Planning 确定性能力单测（ADR-0009/0010/0011）。

覆盖：
- extract_constraints：no_web / scope_only / no_delete 确定性提取
- detect_abstention：信息不足 → Abstain（不猜）；有目标/上下文 → 不 abstain

全部确定性，无 LLM 依赖。
"""
import sys
import unittest

sys.path.insert(0, ".")

from agent.planner.constraint_extractor import extract_constraints, detect_abstention


class ConstraintExtractorTest(unittest.TestCase):

    # ── extract_constraints ──

    def test_no_web(self):
        cons = extract_constraints("不要联网，用本地实现给工具加一个温度转换功能")
        types = [c["type"] for c in cons]
        self.assertIn("no_web", types)

    def test_scope_only(self):
        cons = extract_constraints("只能修改 test/ 目录下的文件，给 tests 补上边界用例")
        scope = [c for c in cons if c["type"] == "scope_only"]
        self.assertEqual(len(scope), 1)
        self.assertEqual(scope[0]["path"], "test")

    def test_no_delete(self):
        cons = extract_constraints("重构 utils.py，但不要删除任何文件，旧的导出要保留")
        types = [c["type"] for c in cons]
        self.assertIn("no_delete", types)

    def test_composite(self):
        cons = extract_constraints("不要联网，修改 parser.py 支持新语法")
        types = [c["type"] for c in cons]
        self.assertIn("no_web", types)
        self.assertNotIn("scope_only", types)

    def test_no_constraint(self):
        cons = extract_constraints("修复 config.py 里的一个 bug")
        self.assertEqual(cons, [])

    # ── detect_abstention（信息不足 → 不猜） ──

    def test_vague_abstain(self):
        # 无文件路径 + 无 grounding + 无 repo_context + 模糊代词 → abstain
        self.assertTrue(detect_abstention("修改一下那个模块"))

    def test_concrete_no_abstain(self):
        # 有具体文件路径 → 不 abstain
        self.assertFalse(detect_abstention("修改 parser.py 的 parse 函数"))

    def test_grounding_no_abstain(self):
        # grounding 有候选 → 不 abstain
        class _G:
            candidates = ["parser.py"]
        self.assertFalse(detect_abstention("修改一下那个模块", grounding=_G()))

    def test_repo_context_no_abstain(self):
        # repo_context 有内容 → 不 abstain（"那个模块"可由当前文件补全）
        self.assertFalse(detect_abstention("修改一下那个模块", repo_context="当前文件: parser.py"))

    def test_empty_input_abstain(self):
        self.assertTrue(detect_abstention(""))


if __name__ == "__main__":
    unittest.main(verbosity=2)
