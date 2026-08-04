# agent/memory/preference.py
"""User preference & fact extraction.

Uses LLM to extract user facts from every input.
Facts are stored in long_term.py SQLite store for persistence.
"""
import json
import os
import re
import asyncio
from langchain_core.messages import SystemMessage, HumanMessage

from agent.llm import llm

# LLM 事实抽取的本地超时：超时后立即走确定性兜底，避免被上层 wait_for 整体取消。
LLM_EXTRACT_TIMEOUT = float(os.getenv("TSAGENT_LLM_EXTRACT_TIMEOUT", "10"))


# ========== LLM-based fact extraction (always on) ==========

FACT_EXTRACTION_PROMPT = """你是一个事实抽取器。从用户输入中提取关于用户自身的客观事实，输出 JSON 对象。

可提取的字段（按类别组织，只包含有值的）：

个人信息 category=personal:
- name: 用户的名字
- location: 用户所在地或居住地
- occupation: 用户的职业
- email: 用户的电子邮件
- phone: 用户的电话号码

编程相关 category=programming:
- language: 用户使用的编程语言（可多个，逗号分隔）
- editor: 使用的编辑器或 IDE
- framework: 使用的框架
- os: 使用的操作系统

兴趣爱好 category=hobby:
- hobby: 兴趣或爱好
- project: 用户提到的项目名称或领域

其他 category=misc:
- preference: 任何其他偏好

输出格式：
{
  "category_name": {"key": "value", ...},
  ...
}

如果没有可提取的事实，输出空对象 {}。
不要输出 JSON 以外的任何文字。

示例：
用户：我叫张三，住在北京，喜欢用Go写后端，用VS Code。
输出：{"personal": {"name": "张三", "location": "北京"}, "programming": {"language": "Go", "editor": "VS Code"}}

用户：你能帮我写个排序算法吗？
输出：{}
"""


async def extract_facts_with_llm(text: str) -> dict:
    """Use LLM to extract facts from user input.

    Returns a dict with categories: {category: {key: value, ...}}
    """
    messages = [
        SystemMessage(content=FACT_EXTRACTION_PROMPT),
        HumanMessage(content=text)
    ]
    response = await llm.ainvoke(messages)
    content = response.content.strip()

    try:
        # Extract JSON
        if "```json" in content:
            start = content.find("```json") + 7
            end = content.find("```", start)
            content = content[start:end].strip()
        elif "```" in content:
            start = content.find("```") + 3
            end = content.find("```", start)
            content = content[start:end].strip()
        return json.loads(content)
    except json.JSONDecodeError:
        # Fallback: simple regex extraction
        import re
        facts = {}
        for key in ["name", "location", "occupation", "email", "phone", "hobby", "language", "project"]:
            match = re.search(rf'"{key}"\s*:\s*"([^"]+)"', content)
            if match:
                facts[key] = match.group(1)
        return {"personal": facts} if facts else {}


# ── 确定性抽取兜底（LLM 不可用/超时时保证常见事实仍被保存，ADR-0009） ──
_DETERMINISTIC_PATTERNS = [
    (re.compile(r"我(?:叫|是)(?P<name>[^，。！？；;\n]{1,12})"), "personal", "name"),
    (re.compile(r"我(?:住在|居住于|居住在|来自)(?P<location>[^，。！？；;\n]{1,16})"), "personal", "location"),
    (re.compile(r"(?:记住[:：]?(?:这个)?(?:项目)?|(?:这个)?项目)(?:叫|是|为)(?P<project>[^，。！？；;\n]{1,24})"), "misc", "project"),
    (re.compile(r"我(?:最)?喜欢(?:的)?(?:编程语言|语言)[是为:：]?(?P<lang>[^，。！？；;\n]{1,12})"), "programming", "language"),
    (re.compile(r"我(?:最)?喜欢(?P<hobby>[^，。！？；;\n]{1,12})"), "hobby", "preference"),
]
_INTERROGATIVE_RE = re.compile(r"什么|哪个|哪一|哪儿|哪里|谁|怎么|为什么|吗|呢|？|\?")


def _deterministic_extract(text: str) -> dict:
    """不依赖 LLM 的常见事实抽取。

    只处理陈述句；含疑问词时返回 {}，避免把"你喜欢什么颜色"误存为事实。
    """
    if _INTERROGATIVE_RE.search(text):
        return {}
    skip_hobby = ("编程语言" in text) or ("语言" in text)
    found: dict = {}
    for pattern, category, key in _DETERMINISTIC_PATTERNS:
        if key == "preference" and skip_hobby:
            continue
        m = pattern.search(text)
        if m and m.group(1) and m.group(1).strip():
            found.setdefault(category, {})[key] = m.group(1).strip()
    return found


async def async_extract_and_save_facts(user_id: str, text: str) -> dict:
    """Always attempt to extract facts from user input."""
    if not text or len(text) < 3:
        return {}

    try:
        facts = await asyncio.wait_for(
            extract_facts_with_llm(text), timeout=LLM_EXTRACT_TIMEOUT
        )
    except Exception:
        facts = {}
    if not facts:
        # LLM 失败/超时 → 确定性兜底，保证关键事实仍落库
        facts = _deterministic_extract(text)
    if not facts:
        return {}

    # Save to long-term facts store
    from agent.memory.long_term import save_fact

    for category, items in facts.items():
        if isinstance(items, dict):
            for key, value in items.items():
                if value and str(value).strip():
                    save_fact(user_id, str(category), str(key), str(value).strip())

    return facts
