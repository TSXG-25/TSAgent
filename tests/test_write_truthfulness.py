# tests/test_write_truthfulness.py
"""回归测试：意图层文件写入兜底 / 追加模式 / 事实抽取兜底 / 假成功校验。

覆盖第三轮聚焦测试残留的 D00 类 / F04 / H01 / H04 根因：
- "写一个X函数保存到 output/fib.py" 不再被数学关键词误判为纯聊天（exec=False）
- "追加到 <path>" 识别为 append
- LLM 事实抽取失败时确定性兜底仍能保存常见事实（且不吞疑问句）
- Finalizer 声称"已写入"但文件不存在时如实纠正
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from agent.cognition.intent_engine import engine
from agent.cognition.cognitive_context import CognitiveContext, ResolvedQuery
from agent.cognition.intent_schema import DOMAIN_FILE, DOMAIN_MATH
from agent.memory.preference import _deterministic_extract
from agent.orchestrator.finalizer import Finalizer
from agent.orchestrator.planner import _ensure_explicit_output_write_task
from agent.compiler.rules.write_rule import WriteRule
from agent.executor.verifier import ExecutionVerifier, ExecutionArtifacts
from agent.task import Task, Verb


def _intent(text: str):
    ctx = CognitiveContext(query=text, resolved_query=ResolvedQuery(raw=text))
    return engine.analyze(ctx)


class TestIntentFileWriteOverride:
    """D00 类：含"函数"的写文件请求必须进入执行链。"""

    def test_write_code_to_path_is_exec(self):
        for u in [
            "用 Python 写一个斐波那契函数，保存到 output/fib.py",
            "写一个冒泡排序函数保存到 output/bubble.py",
            "写一个判断素数的函数保存到 output/isprime.py",
            "写一个二分查找函数到 output/bsearch.py",
            "写一个递归阶乘函数到 output/fact.py",
            "写一个判断回文的函数保存到 output/palindrome.py",
        ]:
            r = _intent(u)
            assert r.requires_execution, f"应进入执行链: {u}"
            assert ".py" in (r.target or ""), f"target 应含文件路径: {u}"

    def test_append_is_exec(self):
        r = _intent("把字符串 hello-append 追加到 output/notes.txt")
        assert r.requires_execution, "追加请求必须进入执行链"
        assert "notes.txt" in (r.target or "")

    def test_non_file_requests_unchanged(self):
        assert _intent("你好").requires_execution is False
        assert _intent("1+1等于几").domain == DOMAIN_MATH
        assert _intent("1+1等于几").requires_execution is False
        assert _intent("把 hello world 翻译成中文").requires_execution is False
        # 读取类请求不应被写入兜底误伤
        assert _intent("读取 output/solution.py").requires_execution is True
        assert _intent("读取 output/solution.py").domain == DOMAIN_FILE

    def test_search_and_write_request_stays_execution_intent(self):
        result = _intent(
            "搜索 Python 怎么抓取股票行情数据，然后写一个示例程序保存到 output/fetch_stock.py"
        )
        assert result.requires_execution is True
        assert result.target.endswith("output/fetch_stock.py")

    def test_search_and_write_plan_contains_materialization_task(self):
        request = "搜索 Python 怎么抓取股票行情数据，然后写一个示例程序保存到 output/fetch_stock.py"
        plan = _ensure_explicit_output_write_task(
            [{"id": "task-1", "verb": "search", "target": "股票行情", "target_type": "text"}],
            request,
        )
        assert [task["verb"] for task in plan] == ["search", "write"]
        assert plan[-1]["target"] == "output/fetch_stock.py"
        assert plan[-1]["dependencies"] == ["task-1"]
        assert plan[-1]["inputs"]["use_prior_facts"] is True


class TestDeterministicFactExtract:
    """H01/H04 类：LLM 不可用时确定性兜底仍保存关键事实。"""

    def test_extracts_common_facts(self):
        assert _deterministic_extract("我喜欢蓝色") == {"hobby": {"preference": "蓝色"}}
        assert _deterministic_extract("我住在北京") == {"personal": {"location": "北京"}}
        assert _deterministic_extract("记住：这个项目叫 TSAgent") == {"misc": {"project": "TSAgent"}}
        assert _deterministic_extract("我最喜欢的编程语言是 Rust") == {"programming": {"language": "Rust"}}
        assert _deterministic_extract("我叫张三") == {"personal": {"name": "张三"}}

    def test_questions_not_extracted(self):
        assert _deterministic_extract("我喜欢什么颜色？") == {}
        assert _deterministic_extract("这个项目叫什么名字？") == {}
        assert _deterministic_extract("我住在哪里") == {}


class TestFinalizerTruthfulness:
    """假成功拦截：声称已写入但文件不存在 → 如实纠正。"""

    def _write_state(self, target):
        return {"plan": [{"verb": "write", "target": target, "status": "succeeded"}]}

    def test_missing_file_with_claim(self, tmp_path):
        target = str(tmp_path / "nope_x.py")
        state = self._write_state(target)
        assert Finalizer._verify_written_files(state, f"已写入 {target}") is not None
        assert Finalizer._verify_written_files(state, f"已生成 {target}") is not None

    def test_existing_file_ok(self, tmp_path):
        target = tmp_path / "ok.py"
        target.write_text("x")
        state = self._write_state(str(target))
        assert Finalizer._verify_written_files(state, f"已写入 {target}") is None

    def test_no_write_task_or_no_claim(self):
        assert Finalizer._verify_written_files({"plan": []}, "已写入 x") is None
        assert Finalizer._verify_written_files(self._write_state("/tmp/nope.py"), "好的") is None

    def test_missing_file_claim_is_checked_without_write_task(self, tmp_path):
        target = str(tmp_path / "missing_without_task.py")
        state = {"plan": [{"verb": "explain", "target": "", "status": "succeeded"}]}
        answer = f"我已整理示例，并保存到 {target} 文件里。"
        assert Finalizer._verify_written_files(state, answer) is not None

    def test_existing_file_claim_is_allowed_without_write_task(self, tmp_path):
        target = tmp_path / "existing_without_task.py"
        target.write_text("print('ok')", encoding="utf-8")
        state = {"plan": [{"verb": "explain", "target": "", "status": "succeeded"}]}
        answer = f"我已整理示例，并保存到 {target} 文件里。"
        assert Finalizer._verify_written_files(state, answer) is None


class TestModifyRuleInstruction:
    """F04 类根因：ModifyRule 必须把任务目标/说明传给 LLM 编辑步骤。"""

    def test_instruction_and_description_passed(self):
        from agent.compiler.rules.modify_rule import ModifyRule
        t = Task(id="t", verb=Verb.MODIFY, target="output/notes.txt",
                 goal="将字符串 hello-append 追加到 notes.txt",
                 description="在文件末尾追加 hello-append，保留已有内容")
        plan = ModifyRule().build(t)
        llm_step = [s for s in plan.steps if s.tool == "llm"][0]
        assert llm_step.args["instruction"] == "将字符串 hello-append 追加到 notes.txt"
        assert llm_step.args["description"].startswith("在文件末尾")

    def test_write_step_order(self):
        from agent.compiler.rules.modify_rule import ModifyRule
        t = Task(id="t", verb=Verb.MODIFY, target="output/x.py", goal="改一下")
        plan = ModifyRule().build(t)
        tools = [s.tool for s in plan.steps]
        assert tools == ["workspace", "filesystem.read", "llm", "filesystem.write"]


class TestWriteVerifier:
    """WriteVerifier — 写入成功的唯一真相来源（v2.1A Contract）。"""

    def test_write_ok(self, tmp_path):
        from agent.executor.verifier import verify_write
        f = tmp_path / "ok.txt"
        f.write_text("hello", encoding="utf-8")
        assert verify_write(str(f)) is True
        assert verify_write(str(f), expect_content="hello") is True
        assert verify_write(str(f), expect_content="nope") is False

    def test_write_fail_cases(self, tmp_path):
        from agent.executor.verifier import verify_write
        assert verify_write("") is False
        assert verify_write(str(tmp_path / "missing.py")) is False
        empty = tmp_path / "empty.txt"
        empty.write_text("", encoding="utf-8")
        assert verify_write(str(empty)) is False
        assert verify_write(str(tmp_path), min_size=0) is False  # 目录不算成功

    def test_absent_and_updated(self, tmp_path):
        from agent.executor.verifier import verify_absent, verify_updated
        f = tmp_path / "x.txt"
        f.write_text("v1", encoding="utf-8")
        st = f.stat()
        assert verify_absent(str(tmp_path / "nope")) is True
        assert verify_absent(str(f)) is False
        assert verify_updated(str(f), st.st_mtime_ns, st.st_size) is False
        f.write_text("v2", encoding="utf-8")
        assert verify_updated(str(f), st.st_mtime_ns, st.st_size) is True


class TestExecutionVerifier:
    # ADR-0012: ExecutionResult 只能由 ExecutionVerifier 产生。

    def _plan(self, verb):
        from agent.task import Task, Verb, ExecutionPlan
        t = Task(id="t", verb=verb, target="output/x.py", goal="写入 x.py")
        return ExecutionPlan(task=t, steps=[])

    def test_write_verification_fail_on_missing(self, tmp_path):
        v = ExecutionVerifier()
        plan = self._plan("write")
        target = str(tmp_path / "missing.py")
        r = v.verify(plan, ExecutionArtifacts(files_written=[target]))
        assert r.success is False
        assert r.verifier == "write"

    def test_write_verification_pass(self, tmp_path):
        v = ExecutionVerifier()
        plan = self._plan("write")
        f = tmp_path / "ok.py"
        f.write_text("print(1)", encoding="utf-8")
        r = v.verify(plan, ExecutionArtifacts(files_written=[str(f)]))
        assert r.success is True

    def test_non_write_verb_no_verifier(self):
        v = ExecutionVerifier()
        plan = self._plan("read")
        r = v.verify(plan, ExecutionArtifacts(files_written=[]))
        assert r.success is True and r.verifier == "none"

    def test_verifier_registry_extensible(self):
        from agent.executor.verifier import WriteVerification
        v = ExecutionVerifier()
        assert v.get("write") is not None
        assert isinstance(v.get("write"), WriteVerification)


class TestWriteRuleAppendMode:
    """F04 类：写任务带追加语义时 mode=append。"""

    def test_append_mode(self):
        t = Task(id="t", verb=Verb.WRITE, target="output/notes.txt",
                 goal="把 hello 追加到 notes.txt", inputs={"content": "hello"})
        plan = WriteRule().build(t)
        ws = [s for s in plan.steps if s.tool == "filesystem.write"][0]
        assert ws.args["mode"] == "append"

    def test_overwrite_default(self):
        t = Task(id="t", verb=Verb.WRITE, target="output/x.txt",
                 goal="创建 x.txt", inputs={"content": "x"})
        plan = WriteRule().build(t)
        ws = [s for s in plan.steps if s.tool == "filesystem.write"][0]
        assert ws.args["mode"] == "overwrite"

    def test_research_context_reaches_generated_file_prompt(self):
        t = Task(
            id="t",
            verb=Verb.WRITE,
            target="output/fetch_stock.py",
            goal="生成股票行情抓取示例",
            inputs={"research_context": "results: requests 与 yfinance 的用法"},
        )
        plan = WriteRule().build(t)
        llm_step = [step for step in plan.steps if step.tool == "llm"][0]
        assert "yfinance" in llm_step.args["user"]
