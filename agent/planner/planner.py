"""Planner — 纯 Goal 分解。

Planner 不知道工具、不知道执行细节。
只做一件事：将用户目标分解为可验证的子目标列表。
对于简单任务，只输出 1-2 个 task，避免过度拆分。
"""
import json
import logging
import re
from datetime import datetime
from langchain_core.messages import SystemMessage, HumanMessage
from agent.llm import llm
from agent.planner.schemas import TaskList

logger = logging.getLogger(__name__)

PLANNER_PROMPT = """你是一个目标分解专家。将用户请求分解为子任务列表。

每个子任务必须包含：
- verb: 动词（枚举值）: read, write, modify, execute, search, list, explain, delete, move, resolve
- target: 操作对象
- target_type: 目标类型（枚举值）: file(文件路径) / symbol(符号名) / text(自由文本) / none
- goal: 简短的描述

target_type 规则（严格遵守）：
- file: target 必须是具体文件路径，含扩展名（如 "output/solution.py"）
- symbol: target 必须是标识符名（如 "ExecutionOrchestrator"）
- text: target 是自由文本（research/explain 类任务）
- none: 无目标对象

INVALID / VALID 对照（LLM 最怕模糊）：
- INVALID: target="计算模块" (target_type=file)     → 没有具体路径
- VALID:   target="output/solution.py" (target_type=file)
- INVALID: target="修改用户模块" (target_type=file) → 中文描述
- VALID:   target="src/user.py" (target_type=file)
- INVALID: target="数据库" (target_type=file)
- VALID:   target="database.py" (target_type=file)
- VALID:   target="ExecutionOrchestrator" (target_type=symbol)
- VALID:   target="为什么 Transformer 有效" (target_type=text)

Never output:
"用户模块", "数据库文件", "计算模块", "相关代码" 等模糊描述作为 file target。
Always output 具体路径: src/user.py, database.py, output/solution.py

规则：
1. 简单的"读取→修改→验证"任务，只输出 2-3 个 task
2. 复杂任务可以增加更多 task
3. 依赖关系用 dependencies（DAG 结构）
4. 不知道任何工具，只输出 verb + target + target_type
5. id 格式 "task-1", "task-2"...

输出格式 (JSON):
{
  "tasks": [
    {
      "id": "task-1",
      "verb": "read",
      "target": "output/solution.py",
      "target_type": "file",
      "goal": "读取当前实现代码",
      "description": "详细说明",
      "success_condition": "成功读取文件内容",
      "dependencies": [],
      "children": []
    }
  ],
  "metadata": {
    "reasoning": "为什么这样分解",
    "estimated_steps": 2
  }
}

⚠️ 如果用户请求很简单（如"读取文件"），最多输出 1 个 task。
保持 verb 和 target 尽可能精确。
"""


async def generate_plan(
    user_input: str,
    memory_context: str = "",
    repo_context: str = "",
    skill_hint: str = "",
    intent=None,
) -> list[dict]:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    sections = [f"当前时间: {now}"]
    sections.append(f"用户需求: {user_input}")

    if memory_context:
        sections.append(f"上下文:\n{memory_context[:500]}")
    if repo_context:
        sections.append(f"相关代码:\n{repo_context[:500]}")

    prompt_text = "\n\n".join(sections)
    messages = [
        SystemMessage(content=PLANNER_PROMPT),
        HumanMessage(content=prompt_text),
    ]

    try:
        # Bug 1 fix: 先检查 structured output 是否可用
        if llm.supports_structured_output:
            try:
                provider, _ = llm._get_active_provider()
                structured_llm = provider.with_structured_output(TaskList)
                result: TaskList = await structured_llm.ainvoke(messages)
                tasks = [t.model_dump() for t in result.tasks]
                logger.info(f"Planner: {len(tasks)} tasks")
                return _ensure_fields(tasks)
            except Exception as e:
                logger.warning(f"Structured output 失败，永久关闭: {e}")
                llm.disable_structured_output()
                # fall through to JSON mode
        
        # JSON 模式（无额外 API 调用）
        json_messages = messages + [
            HumanMessage(content="输出纯 JSON。不要其他文字。")
        ]
        response = await llm.ainvoke(json_messages)
        result = _parse_json(response.content)
        if result and "tasks" in result:
            return _ensure_fields(result["tasks"])
        
        raise ValueError("无法解析 Planner 输出")
    except Exception as e:
        logger.error(f"Planner 失败: {e}")
        return [{
            "id": "task-1", "goal": user_input[:200],
            "description": user_input, "success_condition": "完成任务",
            "dependencies": [], "children": [],
            "status": "pending", "observations": [], "error": "",
        }]


def _ensure_fields(tasks: list) -> list:
    for t in tasks:
        t.setdefault("status", "pending")
        t.setdefault("observations", [])
        t.setdefault("error", "")
        t.setdefault("children", [])
        t.setdefault("description", "")
        t.setdefault("dependencies", [])
        # 新格式: verb + target
        t.setdefault("verb", "")
        t.setdefault("target", "")
        # 旧格式向后兼容: 如果没有 verb/target，从 goal 猜
        if not t.get("verb") and t.get("goal"):
            goal_lower = t["goal"].lower()
            # 尝试从 goal 推断 verb
            verb_hints = {
                "read": ["读取", "读", "阅读", "打开", "查看", "read"],
                "write": ["写入", "写", "创建", "输出", "保存", "write", "create"],
                "modify": ["修改", "编辑", "更新", "更改", "重构", "优化", "modify", "edit", "update", "optimize", "refactor"],
                "execute": ["运行", "执行", "run", "execute"],
                "search": ["搜索", "查找", "查询", "search", "find"],
                "list": ["列出", "列表", "浏览", "list"],
                "explain": ["解释", "说明", "分析", "总结", "explain", "analyze"],
                "design": ["设计", "规划", "design", "plan"],
                "verify": ["验证", "测试", "检查", "verify", "test", "check"],
            }
            for verb, hints in verb_hints.items():
                if any(h in goal_lower for h in hints):
                    t["verb"] = verb
                    break
            if not t.get("verb"):
                t["verb"] = "read"  # 默认
        # 旧格式向后兼容: 如果没有 goal，从 verb + target 生成
        if not t.get("goal") and t.get("verb"):
            target_str = t.get("target", "") or ""
            t["goal"] = f"{t['verb']} {target_str}".strip()
    return tasks


def _parse_json(content: str):
    content = content.strip()
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        pass
    code_match = re.search(r'```(?:json)?\s*\n?(.*?)\n?```', content, re.DOTALL)
    if code_match:
        try:
            return json.loads(code_match.group(1))
        except json.JSONDecodeError:
            pass
    brace_start = content.find('{')
    brace_end = content.rfind('}')
    if brace_start != -1 and brace_end > brace_start:
        try:
            return json.loads(content[brace_start:brace_end + 1])
        except json.JSONDecodeError:
            pass
    return None
