"""Planner — 纯 Goal 分解。

Planner 不知道工具、不知道执行细节。
只做一件事：将用户目标分解为可验证的子目标列表。
对于简单任务，只输出 1-2 个 task，避免过度拆分。
"""
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional
from langchain_core.messages import SystemMessage, HumanMessage
from agent.llm import llm
from agent.planner.constraint_extractor import extract_constraints, detect_abstention
from agent.planner.schemas import TaskList
from agent.task import Task

logger = logging.getLogger(__name__)


@dataclass
class PlanOutput:
    """v2 Planner 完整输出（Planning Quality 可评估载体）。

    - tasks: 分解后的 canonical Task dictionaries
    - constraints: 确定性提取的显式约束
    - abstain: 信息不足 → 应 Ask User / 不猜
    - abstain_reason: abstain 原因（缺少什么信息）
    """
    tasks: List[dict] = field(default_factory=list)
    constraints: List[dict] = field(default_factory=list)
    abstain: bool = False
    abstain_reason: str = ""
    raw: Optional[dict] = None



class PlannerPromptBuilder:
    """Planner Prompt 渲染（View）——GroundingContext 是纯数据（Model），
    Prompt 渲染与数据模型分离（ADR-0004）。"""

    @staticmethod
    def render_grounding(grounding) -> str:
        """把 GroundingContext 渲染为 prompt 段。"""
        if grounding is None:
            return ""
        parts = ["## 仓库候选（Grounding，搜索空间已缩小）"]
        for c in grounding.candidates[:8]:
            parts.append(
                f"- [{c.kind}] {c.name} (score={c.score:.2f}) {c.reason}"
            )
        return "\n".join(parts)

    @staticmethod
    def render_conversation(conversation) -> str:
        if not conversation:
            return ""
        return "## 当前上下文\n" + "\n".join(conversation[:6])

PLANNER_PROMPT = """你是一个目标分解专家。将用户请求分解为子任务列表。

每个子任务必须包含：
- verb: 动词（枚举值）: read, write, modify, execute, search, list, explain, delete, move, copy, resolve
- target: 操作对象
- target_type: 目标类型（枚举值）: file(文件路径) / symbol(符号名) / text(自由文本) / none
- goal: 简短的描述
- children: 子任务对象数组；每个元素都必须是完整 Task 对象，不能填写 task id 字符串。
  如果没有子任务，必须输出空数组 []。

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

## 约束遵守（Constraint Detection）
如果用户提出显式约束，你的计划必须遵守：
- 不要联网 → 禁止输出任何网络相关动作（search web / fetch / 查资料）
- 只能修改 <path>/ → 所有修改类 task（modify/write）的 target 必须位于该路径下
- 不要删除文件 → 禁止输出 delete 动作

## 信息不足 → Abstain（不猜）
如果用户请求信息不足（无法确定目标对象，如"修改一下那个模块"，
且上下文/仓库候选无法补全目标），**不要猜测**：
- 输出 tasks 为空列表 []
- 在 metadata.reasoning 中说明缺少什么信息
"乱猜一个目标"比"承认信息不足"更糟糕（False Confidence）。
"""


async def plan_with_metadata(
    user_input: str,
    memory_context: str = "",
    repo_context: str = "",
    skill_hint: str = "",
    intent=None,
    grounding=None,
) -> PlanOutput:
    """v2 完整入口（v2.0-A Planning Quality）。

    1. 确定性约束提取 → 注入 prompt（LLM 理解并遵守）
    2. 信息不足 → Abstain（返回空 plan + 原因，不猜）
    3. 返回 PlanOutput（tasks + constraints + abstain + raw）
    """
    # ── Abstention 检测（确定性，Uncertainty 横切） ──
    if detect_abstention(user_input, grounding, repo_context):
        return PlanOutput(
            tasks=[],
            abstain=True,
            abstain_reason=(
                "无法确定目标对象（输入模糊且无上下文/仓库候选可补全）——"
                "信息不足，应向用户澄清而不是猜测。"
            ),
        )

    # ── 约束提取（确定性） → prompt 注入 ──
    constraints = extract_constraints(user_input)

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    sections = [f"当前时间: {now}"]
    sections.append(f"用户需求: {user_input}")

    if constraints:
        lines = [
            f"- {c.get('detail', c.get('type', ''))}"
            + (f"（限定路径: {c['path']}/）" if c.get("path") else "")
            for c in constraints
        ]
        sections.append("## 用户显式约束（必须遵守）\n" + "\n".join(lines))

    if memory_context:
        sections.append(f"上下文:\n{memory_context[:500]}")
    if repo_context:
        sections.append(f"相关代码:\n{repo_context[:500]}")

    # Grounding 注入（搜索空间缩小后的候选文件/符号）
    if grounding is not None:
        grounding_text = PlannerPromptBuilder.render_grounding(grounding)
        if grounding_text:
            sections.append(grounding_text)
        conv_text = PlannerPromptBuilder.render_conversation(getattr(grounding, "conversation", []))
        if conv_text:
            sections.append(conv_text)

    prompt_text = "\n\n".join(sections)
    messages = [
        SystemMessage(content=PLANNER_PROMPT),
        HumanMessage(content=prompt_text),
    ]

    try:
        # Bug 1 fix: 先检查 structured output 是否可用
        tasks_out = None
        raw_out = None
        if llm.supports_structured_output:
            try:
                provider, _ = llm._get_active_provider()
                structured_llm = provider.with_structured_output(TaskList)
                result: TaskList = await structured_llm.ainvoke(messages)
                tasks_out = [t.model_dump() for t in result.tasks]
                if tasks_out:
                    logger.info(f"Planner: {len(tasks_out)} tasks")
                    return PlanOutput(
                        tasks=_normalize_tasks(tasks_out),
                        constraints=constraints,
                        raw={"tasks": tasks_out},
                    )
                # 空计划：落到 JSON 模式重试（避免过度 abstain）
                logger.warning("Structured 输出空计划，落 JSON 模式重试")
            except Exception as e:
                logger.warning(f"Structured output 失败，永久关闭: {e}")
                llm.disable_structured_output()
                # fall through to JSON mode

        # JSON 模式（无额外 API 调用）
        json_messages = messages + [
            HumanMessage(content="输出纯 JSON。不要其他文字。")
        ]
        # v2.0-A 鲁棒性：JSON 失败重试一次（严格格式），避免约束段导致格式漂移
        for attempt in range(2):
            response = await llm.ainvoke(json_messages)
            result = _parse_json(response.content)
            if result and "tasks" in result:
                # 非 abstain 场景空计划 = 过度 abstain → 重试强制非空
                if not result["tasks"]:
                    json_messages = messages + [
                        HumanMessage(
                            "用户已提供明确目标，必须输出至少一个 task。\n"
                            "严格输出 JSON。格式: {\"tasks\": [...]}"
                        )
                    ]
                    continue
                return PlanOutput(
                    tasks=_normalize_tasks(result["tasks"]),
                    constraints=constraints,
                    raw=result,
                )
            if attempt == 0:
                logger.warning(f"Planner JSON 解析失败（{response.content[:80]!r}），严格格式重试")
                json_messages = messages + [
                    HumanMessage(content="严格输出 JSON。不要任何解释、不要 markdown 代码块。格式: {\"tasks\": [{\"id\": \"task-1\", \"verb\": \"read\", \"target\": \"...\", \"target_type\": \"file\", \"goal\": \"...\", \"description\": \"...\", \"success_condition\": \"...\", \"dependencies\": []}], \"metadata\": {\"reasoning\": \"...\", \"estimated_steps\": 1}}")
                ]

        raise ValueError("无法解析 Planner 输出")
    except Exception as e:
        logger.error(f"Planner 失败: {e}")
        return PlanOutput(tasks=[{
            "id": "task-1", "verb": "modify", "target": "",
            "target_type": "text",
            "goal": user_input[:200],
            "description": user_input, "success_condition": "完成任务",
            "dependencies": [], "children": [],
            "status": "pending", "observations": [], "error": "",
        }], constraints=constraints)


def _normalize_tasks(tasks: list) -> list:
    """Validate and serialize Planner output through the canonical Task model."""
    normalized = []
    for index, raw in enumerate(tasks):
        data = dict(raw)
        data.setdefault("id", f"task-{index + 1}")
        data.setdefault("status", "pending")
        data.setdefault("observations", [])
        data.setdefault("error", "")
        data.setdefault("children", [])
        data.setdefault("description", "")
        data.setdefault("dependencies", [])
        normalized.append(Task.from_dict(data).to_dict())
    return normalized


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
