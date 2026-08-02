# agent/memory/preference.py
"""User preference & fact extraction.

Uses LLM to extract user facts from every input.
Facts are stored in long_term.py SQLite store for persistence.
"""
import json
import re
from pathlib import Path
from langchain_core.messages import SystemMessage, HumanMessage
from agent.llm import llm

# Legacy: keep old preferences table for backward compatibility
import sqlite3
DB_PATH = Path(__file__).parent.parent.parent / "data" / "prefs.db"
DB_PATH.parent.mkdir(parents=True, exist_ok=True)

def _init_legacy_db():
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS preferences (
                user_id TEXT PRIMARY KEY,
                data TEXT
            )
        """)
_init_legacy_db()

def get_user_preferences(user_id: str) -> dict:
    """Legacy: get user preferences dict."""
    try:
        with sqlite3.connect(DB_PATH) as conn:
            cur = conn.execute("SELECT data FROM preferences WHERE user_id = ?", (user_id,))
            row = cur.fetchone()
            return json.loads(row[0]) if row else {}
    except Exception:
        return {}

def save_user_preference(user_id: str, key: str, value):
    """Legacy: save single preference."""
    prefs = get_user_preferences(user_id)
    prefs[key] = value
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("REPLACE INTO preferences (user_id, data) VALUES (?, ?)", (user_id, json.dumps(prefs)))


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


async def async_extract_and_save_facts(user_id: str, text: str) -> dict:
    """Always attempt to extract facts from user input."""
    if not text or len(text) < 3:
        return {}

    facts = await extract_facts_with_llm(text)
    if not facts:
        return {}

    # Save to long-term facts store
    from agent.memory.long_term import save_fact

    for category, items in facts.items():
        if isinstance(items, dict):
            for key, value in items.items():
                if value and str(value).strip():
                    save_fact(user_id, str(category), str(key), str(value).strip())

    # Also keep legacy compatibility
    for category, items in facts.items():
        if isinstance(items, dict):
            for key, value in items.items():
                if value and str(value).strip():
                    save_user_preference(user_id, f"{category}.{key}", str(value).strip())

    return facts