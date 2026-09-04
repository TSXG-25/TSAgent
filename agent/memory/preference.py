# agent/memory/preference.py
"""Non-authoritative utility for extracting candidate facts from text.

Production durable learning is owned by ``MemoryLearningProvider`` followed
by deterministic authorization and ``MemoryPersistenceBoundary``. This
module neither authorizes nor persists learned Memory.
"""
import json
from langchain_core.messages import SystemMessage, HumanMessage

from agent.llm import llm


# ========== Candidate fact extraction utility ==========

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


__all__ = ["extract_facts_with_llm"]
