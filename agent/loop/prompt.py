"""Loop system prompt — renders the intent-derived contract once per Run."""
from __future__ import annotations

from typing import Any, Mapping, Sequence


def build_system_prompt(
    *,
    authorization,
    goal_state: Mapping[str, Any],
    skills: Mapping[str, Any],
    workflows: Sequence[Mapping[str, Any]],
    freshness_required: bool,
    source_grounding_required: bool,
) -> str:
    outcomes = [str(item) for item in authorization.requested_outcomes]
    scopes = list(authorization.write_scopes)
    deliverables = [
        str(item.get("path", ""))
        for item in (goal_state.get("delivery_requirements") or ())
        if isinstance(item, Mapping) and str(item.get("path", "")).strip()
    ]
    prohibited = [
        str(item) for item in (goal_state.get("prohibited_effects") or ())
    ]
    acceptance = [
        str(item.get("criterion", ""))
        for item in (goal_state.get("acceptance_criteria") or ())
        if isinstance(item, Mapping)
        and str(item.get("verifiable", "")).lower() not in {"false", "unverifiable"}
        and str(item.get("criterion", "")).strip()
    ]

    parts = [
        "你是一个单 agent：直接调用工具完成任务，完成后输出给用户的最终答复。"
        "没有工具调用时的回复就是最终答复，不要输出中间计划或推理过程。",
        "## 授权边界（硬约束，违反会被拒绝执行）",
        "- 授权结果: " + (", ".join(outcomes) or "仅文字回答"),
        "- 可写范围: " + (", ".join(scopes) if scopes else "不允许写文件"),
        "- 允许执行命令/代码: " + (
            "是" if authorization.requires_execution else "否"
        ),
    ]
    if prohibited:
        parts.append("- 禁止效果: " + ", ".join(prohibited))
    if deliverables:
        parts.append("- 必须交付的产物: " + ", ".join(deliverables))
    if acceptance:
        parts.append("- 验收条件: " + "; ".join(acceptance))
    if freshness_required or source_grounding_required:
        parts.append(
            "- 该请求要求可核验的外部来源：必须先调用 web_search 等检索工具，"
            "不得凭记忆回答时效性内容。"
        )
    parts += [
        "",
        "## 规则",
        "- 修改已有文件优先用 edit_file 精确替换；大范围重写才用 write_file 输出完整内容。",
        "- 不要写占位符/骨架再逐步填充；如果一次改不完，就直接用 write_file 输出完整文件。",
        "- 只能声称工具实际做过的事；不确定或信息不足时直接向用户提问，不要编造结果。",
        "- 所有路径是工作区相对路径；新建产物默认写到 output/ 目录，除非用户明确给出了路径。",
    ]
    if skills:
        lines = ["## 可用技能（需要时调用 load_skill 获取完整流程）"]
        lines += [f"- {name}: {skill.description}" for name, skill in sorted(skills.items())]
        parts.append("\n".join(lines))
    if workflows:
        lines = ["## 可用工作流（需要时调用 run_workflow）"]
        lines += [
            f"- {item['id']}: {item.get('description', '')}"
            for item in workflows
        ]
        parts.append("\n".join(lines))
    return "\n".join(parts)
