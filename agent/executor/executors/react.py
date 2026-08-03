"""ReactExecutor — ReAct Loop 执行器。

核心改进（迁移自 agent/executor/executor.py，逻辑不变）：
1. Compact Observation: 只存 args_preview 而非全参数
2. Question Summarize: read_file 成功后 LLM 自动总结题目
3. Failure Signature: hash(params) 防止完全相同重试
4. Validator: 真实验证而非 bool Facts
5. Tool Selection Rules: 根据任务类型注入工具选择策略
6. Recovery: 工具失败后不 finish，继续重试

Phase B.3 迁移：纯移动 + 改名（Executor → ReactExecutor）。
ActionResolver 已在 Phase B.1 接入（_execute_action 委托）。
"""
import asyncio
import hashlib
import json
import logging
import time
from typing import Any, Dict, List, Optional
from langchain_core.messages import AIMessage
from agent.llm import llm
from agent.event_bus import event_bus
from agent.services.artifact_service import ArtifactService
from agent.context.context_service import ContextService
from agent.state import AgentState
from agent.executor.dag import resolve_dag, flatten_tree
from agent.validators import validator
from agent.workflow.budget import BudgetManager, BudgetSpec

logger = logging.getLogger(__name__)

MAX_THINK_ITERATIONS = 8

INSTALL_COMMANDS = [
    "pip install", "pip3 install",
    "brew install",
    "apt-get install", "apt install",
    "yum install",
    "npm install -g", "npm i -g",
    "curl.*|.*bash", "curl.*|.*sh",
    "wget.*|.*bash", "wget.*|.*sh",
]

SUMMARIZE_PROMPT = """你是一个智能摘要生成器。对下面的题目内容，提取关键信息：

- 题目类型（算法题/编程题/数学题等）
- 目标（用一句话概括）
- 输入格式说明
- 输出格式说明  
- 关键约束条件

请用以下格式输出（不要多余内容）：
类型: 
目标: 
输入: 
输出: 
约束: 
"""

TOOL_SELECTION_SYSTEM_PROMPT = """你是一个智能 Agent。根据当前目标和已有信息，决定下一步行动。

工具选择原则：
🔹 外部信息查询（天气、新闻、搜索、股票）：优先使用 web_search。
🔹 文件/代码操作（读取、修改、运行）：优先使用 read_file / write_file / run_python。
🔹 调研类任务：优先使用 web_search 搜索资料。
🔹 不要读取无关的本地文件。
🔹 工具失败后不要 finish，换个方式重试。
"""


class ReactExecutor:
    """ReAct Loop 执行器。"""

    def __init__(self, budget: Optional[BudgetSpec] = None):
        """初始化执行器。

        Args:
            budget: 资源预算（max_steps 控制 ReAct 循环步数）。
                未提供时回退到模块级 MAX_THINK_ITERATIONS。
        """
        self._budget_manager = BudgetManager(budget) if budget is not None else None

    def _max_iterations(self) -> int:
        """获取最大 Think 迭代次数（budget 优先，默认 MAX_THINK_ITERATIONS）。"""
        if self._budget_manager is not None:
            return self._budget_manager.spec.max_steps
        return MAX_THINK_ITERATIONS

    async def execute(
        self,
        state: AgentState,
        tasks: List[Dict],
    ) -> AgentState:
        flat_tasks = flatten_tree(tasks[:])
        for t in flat_tasks:
            t.setdefault("status", "pending")
            t.setdefault("observations", [])
            t.setdefault("error", "")
            t.setdefault("facts", {})

        state["plan"] = flat_tasks
        state["current_task_index"] = 0

        global_facts: Dict[str, Any] = {}

        for batch in resolve_dag(flat_tasks):
            for task in batch:
                state["current_task_index"] = flat_tasks.index(task)
                self._load_dependency_artifacts(task, flat_tasks)
                self._inject_global_facts(task, global_facts)

                if self._check_skip_condition(task, global_facts):
                    task["status"] = "skipped"
                    task["observations"].append({
                        "action": "skip",
                        "status": "succeeded",
                        "summary": f"Task 已被全局 Facts 满足，跳过执行",
                        "artifact_ids": [],
                        "tool_used": "",
                        "time_s": 0,
                    })
                    print(f"   ⏭️ 跳过: {task['goal']} (已有 Facts)")
                    continue

                task_facts = task.setdefault("facts", {})
                for k, v in global_facts.items():
                    if k not in task_facts:
                        task_facts[k] = v

                state = await self._execute_task_react(state, task)

                task_facts = task.get("facts", {})
                global_facts.update(task_facts)

        state["plan"] = flat_tasks
        return state

    def _inject_global_facts(self, task: Dict, global_facts: Dict) -> None:
        if global_facts:
            facts_lines = [f"  {k}: {str(v)[:100]}" for k, v in global_facts.items()]
            task["_global_facts"] = "\n".join(facts_lines)

    def _check_skip_condition(self, task: Dict, global_facts: Dict) -> bool:
        goal = (task.get("goal", "") or "").lower()
        read_goals = ["读取", "读", "阅读", "打开", "查看", "read", "open", "view"]
        is_read_goal = any(g in goal for g in read_goals)
        if is_read_goal and global_facts.get("question_loaded"):
            return True
        list_goals = ["列出", "搜索", "查找", "list", "search", "find"]
        is_list_goal = any(g in goal for g in list_goals)
        if is_list_goal and global_facts.get("directory_listed"):
            return True
        return False

    def _load_dependency_artifacts(self, task: Dict, all_tasks: List[Dict]) -> None:
        dep_ids = task.get("dependencies", [])
        if not dep_ids:
            return
        dep_artifacts = []
        for dep_id in dep_ids:
            for t in all_tasks:
                if t.get("id") == dep_id:
                    for obs in t.get("observations", []):
                        for aid in obs.get("artifact_ids", []):
                            summary = ArtifactService.get_summary(aid)
                            if summary:
                                dep_artifacts.append(summary)
                    dep_facts = t.get("facts", {})
                    if dep_facts:
                        dep_artifacts.append("[Facts] " + ", ".join(
                            f"{k}={str(v)[:50]}" for k, v in dep_facts.items()
                        ))
                    break
        if dep_artifacts:
            ctx = "\n".join(dep_artifacts)
            task["_dependency_artifacts"] = ctx

    async def _execute_task_react(
        self,
        state: AgentState,
        task: Dict,
    ) -> AgentState:
        event_bus.emit("task_start", {"task": task["id"]})
        task["status"] = "running"
        print(f"\n🎯 [任务] {task['goal']}")

        iteration = 0
        consecutive_finish_fails = 0
        while iteration < self._max_iterations():
            iteration += 1
            if self._budget_manager is not None:
                self._budget_manager.count_step()
            action = await self._think(state, task)

            if action.get("action") == "finish":
                success, reason = validator.validate(task)
                if success:
                    task["status"] = "succeeded"
                    print(f"   ✅ 完成 (第 {iteration} 轮) — {reason}")
                    break
                else:
                    consecutive_finish_fails += 1
                    if consecutive_finish_fails >= 2:
                        task["status"] = "succeeded"
                        print(f"   ✅ 强制完成 (连续 {consecutive_finish_fails} 次 finish，兜底)")
                        break
                    print(f"   ⚠️ LLM 要求结束但验证不通过 ({reason})，继续执行")
                    continue

            capabilities = action.get("capabilities", [])
            params = action.get("params", {})
            reason = action.get("reason", "")

            action_name = action.get("action", "")
            if not action_name or action_name == "":
                print(f"   ⚠️ LLM 返回空白 action，跳过")
                continue

            if not capabilities:
                if action_name and action_name != "finish":
                    from agent.registry.tool_registry import registry as _reg
                    tool_obj = _reg.get(action_name)
                    if tool_obj:
                        for tag, names in _reg.tags.items():
                            if action_name in names and tag not in ("all", "general"):
                                capabilities.append(tag)
                                if len(capabilities) >= 2:
                                    break
                    if not capabilities:
                        capabilities = [action_name] if action_name else []

            if not capabilities:
                task["status"] = "failed"
                task["error"] = f"LLM 未指定 capabilities: {action}"
                break

            if capabilities[0] == "shell" or action.get("action") == "shell":
                cmd = params.get("cmd", params.get("command", ""))
                blocked, reason_msg = self._check_install_command(cmd)
                if blocked:
                    observation = self._make_observation(
                        tool="shell",
                        args_preview=cmd[:80],
                        status="failed",
                        summary=f"安全策略拦截: {reason_msg}",
                    )
                    task["observations"].append(observation)
                    print(f"   🚫 拦截安装命令: {reason_msg}")
                    self._record_failure(task, "shell", reason_msg, {"cmd": cmd})
                    continue

            observation = await self._execute_action(
                task, capabilities, params, reason
            )
            task["observations"].append(observation)

            self._update_facts(task, observation)

            if observation["status"] == "failed":
                print(f"   ⚠️ 失败: {observation.get('summary', '')[:100]}")
                self._record_failure(
                    task,
                    observation.get("tool_used", ""),
                    observation.get("summary", ""),
                    params,
                )
                # v2.0-C Reflection：失败 → 确定性诊断 → Correction Proposal（注入下一轮 Think）
                # 约束 1：reflect() 只消费 FailureEvent（evidence 结构化）
                # 约束 2：correction 是 Proposal，Executor/LLM 决定是否采纳
                self._reflect_failure(task, observation)
                # v2.0-D Decision：诊断 + 状态 → next action（retry/switch/ask/finish）
                # Decision 是 Policy + Confidence Gate 的确定性层，不是 LLM 自由发挥
                next_action = self._decide_next(task, observation)
                if next_action in ("ask", "finish"):
                    print(f"   ⏹️ Decision: {next_action}（策略性停止，不无限重试）")
                    task["status"] = "failed"
                    task["error"] = (
                        f"[Decision:{next_action}] "
                        f"{task.get('_decision', {}).get('rule', '')} — 需要用户介入或放弃"
                    )
                    break
                # P0-3 Recovery: tool 失败后不走 validator，直接继续 Think
                # (不要在这里 finish，让 LLM 换工具重试)
                print(f"   🔄 失败后不结束，继续尝试 (第 {iteration}/{self._max_iterations()} 轮)")
                continue

            print(f"   ➡️ {observation.get('summary', '')[:100]}")

            success, reason = validator.validate(task)
            if success:
                task["status"] = "succeeded"
                print(f"   ✅ 验证通过 (第 {iteration} 轮) — {reason}")
                break

        else:
            task["status"] = "failed"
            task["error"] = f"超过最大迭代次数 ({self._max_iterations()})"

        event_bus.emit("task_end", {
            "task": task["id"],
            "status": task["status"],
        })
        return state

    def _make_observation(self, tool: str, args_preview: str = "",
                          status: str = "succeeded", summary: str = "") -> Dict:
        return {
            "action": tool,
            "tool": tool,
            "args_preview": args_preview[:100],
            "status": status,
            "summary": summary[:300],
            "artifact_ids": [],
            "time_s": 0,
        }

    def _update_facts(self, task: Dict, observation: Dict) -> None:
        facts = task.setdefault("facts", {})
        tool = observation.get("action", "")
        status = observation.get("status", "")
        summary = observation.get("summary", "") or ""
        args_preview = observation.get("args_preview", "")

        if tool == "read_file":
            if status == "succeeded":
                facts["question_loaded"] = True
                if args_preview:
                    facts["question_path"] = args_preview
                facts["question_length"] = len(summary)
                if observation.get("question_summary"):
                    facts["question_summary"] = observation["question_summary"]
                    facts["question_type"] = observation.get("question_type", "")
                    facts["question_constraints"] = observation.get("question_constraints", "")

        elif tool == "list_directory":
            if status == "succeeded":
                facts["directory_listed"] = True
                if summary:
                    facts["files"] = summary[:500]

        elif tool == "run_python" or tool == "run_python_file":
            if status == "succeeded":
                facts["code_executed"] = True
                if "solution" in summary.lower():
                    facts["solution_generated"] = True

        elif "write" in tool:
            if status == "succeeded":
                facts["file_written"] = True

    async def _summarize_question(self, raw_text: str) -> Dict:
        try:
            messages = [
                {"role": "system", "content": SUMMARIZE_PROMPT},
                {"role": "user", "content": f"题目内容:\n{raw_text[:2000]}"}
            ]
            response = await llm.ainvoke(messages)
            content = response.content.strip()
            
            result = {
                "question_summary": content[:500],
                "question_type": "",
                "question_constraints": "",
            }
            
            for line in content.split("\n"):
                line = line.strip()
                if line.startswith("类型:"):
                    result["question_type"] = line[3:].strip()
                elif line.startswith("约束:") or line.startswith("限制:"):
                    result["question_constraints"] = line[3:].strip()
            
            return result
        except Exception as e:
            logger.warning(f"Question summarization failed: {e}")
            return {
                "question_summary": raw_text[:300],
                "question_type": "",
                "question_constraints": "",
            }

    def _record_failure(self, task: Dict, tool: str, error: str, params: Dict) -> None:
        failures = task.setdefault("recent_failures", [])
        now = time.time()
        param_str = json.dumps(params, sort_keys=True, ensure_ascii=False)
        sig = hashlib.md5(param_str.encode()).hexdigest()[:8]
        failures[:] = [f for f in failures if now - f.get("time", 0) < 30]
        failures.append({
            "tool": tool,
            "error": error[:100],
            "signature": sig,
            "args_preview": self._get_args_preview(tool, params),
            "time": now,
        })
        if len(failures) > 3:
            failures.pop(0)

    def _get_args_preview(self, tool: str, params: Dict) -> str:
        if tool == "read_file":
            return f'read_file("{params.get("path", "?")}")'
        elif tool == "shell":
            return f'shell("{params.get("cmd", params.get("command", "?"))[:60]}")'
        elif tool == "web_search":
            return f'web_search("{params.get("q", params.get("query", "?"))[:60]}")'
        elif tool == "run_python":
            return 'run_python(code=...)'
        elif tool == "write_file":
            return f'write_file("{params.get("path", "?")}")'
        else:
            return f'{tool}({json.dumps(params, ensure_ascii=False)[:60]})'

    def _check_install_command(self, cmd: str) -> tuple:
        if not cmd:
            return False, ""
        cmd_lower = cmd.lower().strip()
        for pattern in INSTALL_COMMANDS:
            if pattern in cmd_lower:
                return True, f"禁止执行安装命令。如需安装依赖，请在 requirements.txt 中添加。"
        return False, ""

    def _check_success_condition(self, task: Dict) -> bool:
        success, _ = validator.validate(task)
        return success

    def _symptom_from_observation(self, obs: Dict) -> str:
        """确定性 symptom 判定（从失败观察，ADR-0009：无 LLM）。"""
        s = f"{obs.get('summary', '')} {obs.get('error', '')}"
        sl = s.lower()
        if "timeout" in sl or "超时" in sl:
            return "timeout"
        if ("不存在" in s or "未找到" in s or "无匹配" in s
                or "not found" in sl or "no such file" in sl):
            return "hallucination"
        if ("拦截" in s or "拒绝" in s or "禁止" in s or "安全策略" in s):
            return "missing_constraint"
        return "wrong_answer"

    def _reflect_failure(self, task: Dict, observation: Dict) -> None:
        """v2.0-C Reflection：失败观察 → FailureEvent → reflect() → Correction Proposal。

        Correction 是 Proposal（约束 2）：只注入下一轮 Think 的提示，
        由 Executor/LLM 决定是否采纳。Reflection 不执行修正。
        """
        try:
            from agent.reflection.reflector import reflect
            from evaluation.benchmark.failboard_v2 import FailureEvent, Evidence

            obs = observation or {}
            event = FailureEvent(
                benchmark="executor",
                scenario=task.get("id", "task"),
                layer="long_horizon",
                dimension="completion",
                failure=(obs.get("summary", "") or "")[:120],
                evidence=[Evidence(
                    source="tool",
                    location=obs.get("tool_used", obs.get("action", "")),
                    expected="执行成功",
                    actual=(obs.get("summary", "") or "")[:120],
                )],
                symptom=self._symptom_from_observation(obs),
            )
            result = reflect(event)
            task["_reflection"] = {
                "root_cause": result.diagnosis.root_cause,
                "confidence": result.diagnosis.confidence,
                "correction": result.correction.action,
                "reason": result.correction.reason,
            }
            print(f"   🔬 反射诊断: root_cause={result.diagnosis.root_cause} "
                  f"(conf={result.diagnosis.confidence}) → 建议 correction={result.correction.action}")
        except Exception as e:
            # Reflection 是增强，失败不阻塞执行
            task["_reflection"] = {"root_cause": "unknown", "confidence": 0.0,
                                   "correction": "retry", "reason": f"Reflection 失败: {e}"}

    def _refined_diagnosis(self, root_cause: str, symptom: str) -> str:
        """root_cause（粗） + symptom → 细化 diagnosis（Decision Policy key）。"""
        if root_cause == "tool":
            if symptom == "timeout":
                return "tool_timeout"
            if symptom == "missing_constraint":
                return "permission_denied"
            return "tool_failure"
        if root_cause == "grounding":
            if symptom == "hallucination":
                return "hallucination"
            return "grounding_miss"
        if root_cause == "planning":
            if symptom == "context_drift":
                return "context_drift"
            if symptom == "missing_constraint":
                return "constraint_violation"
            return "planning_failure"
        return {
            "tool": "tool_failure", "decision": "decision_failure",
            "prompt": "prompt_failure", "runtime": "runtime_failure",
            "external": "external_failure", "unknown": "unknown",
        }.get(root_cause, "unknown")

    def _decide_next(self, task: Dict, observation: Dict) -> str:
        """v2.0-D Decision：diagnosis + ExecutionState → next action（retry/switch/ask/finish）。

        Decision 是确定性 Policy 层（不是 LLM 自由发挥）。DecisionTrace 记录 rule/confidence。
        """
        try:
            from agent.decision.decision import decide, DecisionInput, ExecutionState

            refl = task.get("_reflection", {})
            root_cause = refl.get("root_cause", "unknown")
            diagnosis = self._refined_diagnosis(root_cause, self._symptom_from_observation(observation or {}))
            failures = task.get("recent_failures", []) or []
            retry_count = len(failures)
            last_tool = (observation or {}).get("tool_used", "")
            same_tool = bool(last_tool) and any(
                f.get("tool") == last_tool for f in failures
            )

            inp = DecisionInput(
                diagnosis=diagnosis,
                diagnosis_confidence=refl.get("confidence", 0.2),
                state=ExecutionState(
                    retry_count=retry_count,
                    same_tool=same_tool,
                    evidence_completeness=1.0 if refl.get("confidence", 0) > 0 else 0.5,
                ),
                event_id=task.get("id", "task"),
            )
            decision, trace = decide(inp)
            task["_decision"] = {
                "action": decision.action,
                "confidence": decision.confidence,
                "rule": trace.policy_rule,
                "diagnosis": diagnosis,
            }
            print(f"   🎯 Decision: {decision.action} "
                  f"(rule={trace.policy_rule}, conf={decision.confidence})")
            return decision.action
        except Exception as e:
            # Decision 是增强，失败默认 retry（与旧行为一致）
            task["_decision"] = {"action": "retry", "confidence": 0.0,
                                 "rule": "fallback", "diagnosis": "unknown"}
            return "retry"

    async def _think(self, state: AgentState, task: Dict) -> Dict:
        # P0-2: Inject tool selection rules into the system prompt
        task_goal = (task.get("goal", "") or "").lower()
        rules = self._build_tool_selection_rules(task_goal)

        # v2.0-C Reflection：上一轮失败的 Correction Proposal 注入（Proposal，LLM 决定采纳）
        refl = task.get("_reflection")
        if refl:
            rules += (
                f"\n🔹 上一轮失败已诊断: root_cause={refl.get('root_cause')}"
                f"（confidence={refl.get('confidence')}）。"
                f"建议 correction={refl.get('correction')}（仅供参考，自行判断）。"
                f"如果该 correction 不适用，请给出你的替代方案。"
            )

        system_prompt = TOOL_SELECTION_SYSTEM_PROMPT + "\n\n" + rules
        
        messages = ContextService.build_think_prompt(task, system_prompt=system_prompt)

        try:
            response = await llm.ainvoke(messages)
            content = response.content.strip()
            action = self._parse_action(content)
            if action:
                return action
            return {"action": "finish", "reason": "LLM 无法决定下一步"}
        except Exception as e:
            logger.warning(f"Think 失败: {e}")
            return {"action": "finish", "reason": f"Think 错误: {e}"}

    def _build_tool_selection_rules(self, goal: str) -> str:
        """根据 task goal 生成工具选择规则。"""
        rules = []
        
        web_keywords = ["天气", "新闻", "股票", "搜索", "查询", "查一下", "weather", "news", "search"]
        if any(k in goal for k in web_keywords):
            rules.append("🔹 本任务需要外部信息，必须使用 web_search。")
            rules.append("🔹 不要读取本地文件（read_file/list_directory），这无关。")
            rules.append("🔹 如果 web_search 失败，换关键词重试，不要 finish。")
        
        code_keywords = ["读", "写", "修改", "代码", "文件", "运行", "执行", "read", "write", "code", "file"]
        if any(k in goal for k in code_keywords):
            rules.append("🔹 本任务是文件/代码操作，优先使用 read_file / write_file / run_python。")
        
        research_keywords = ["调研", "研究", "论文", "文献", "research", "paper", "survey"]
        if any(k in goal for k in research_keywords):
            rules.append("🔹 本任务是调研类，必须使用 web_search 搜索资料。")
        
        if not rules:
            rules.append("🔹 根据任务目标选择最合适的工具。")
        
        rules.append("🔹 工具失败后永远不要 finish，换个工具或方式重试。")
        return "\n".join(rules)

    async def _execute_action(
        self,
        task: Dict,
        capabilities: List[str],
        params: Dict,
        reason: str,
    ) -> Dict:
        # Phase B.1: 委托 ActionResolver（唯一解析入口）。
        # 解析链：CapabilityRegistry 能力名 → 工具名直查 → ToolRegistry tag 匹配。
        # 消除了本类与 action_resolver.py 的重复实现（ToolRegistry/CapabilityRegistry/摘要/错误处理）。
        from agent.executor.action_resolver import resolver as action_resolver

        return await action_resolver.resolve(
            capabilities=capabilities,
            params=params,
            reason=reason,
            task_id=task.get("id", ""),
            task_goal=task.get("goal", ""),
        )

    def _parse_action(self, content: str) -> Optional[Dict]:
        import re
        content = content.strip()
        try:
            obj = json.loads(content)
            if "action" in obj:
                return obj
        except json.JSONDecodeError:
            pass
        code_match = re.search(r'```(?:json)?\s*\n?(.*?)\n?```', content, re.DOTALL)
        if code_match:
            try:
                obj = json.loads(code_match.group(1))
                if "action" in obj:
                    return obj
            except json.JSONDecodeError:
                pass
        brace_start = content.find('{')
        brace_end = content.rfind('}')
        if brace_start != -1 and brace_end > brace_start:
            try:
                obj = json.loads(content[brace_start:brace_end + 1])
                if "action" in obj:
                    return obj
            except json.JSONDecodeError:
                pass
        return None