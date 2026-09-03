# agent/memory/preference.py
"""User preference & fact extraction.

Uses LLM to extract user facts from every input.
Facts are stored in long_term.py SQLite store for persistence.
"""
import json
import os
import re
import asyncio
import uuid
from typing import Iterable
from langchain_core.messages import SystemMessage, HumanMessage

from agent.llm import llm
from agent.memory.learning import (
    ExistingMemory,
    InteractionEvidence,
    MemoryPolicyProjection,
    decide_memory_learning,
)
from agent.memory.persistence import MemoryCommitEvidence, MemoryPersistenceBoundary

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
_EXPLICIT_PERSIST_RE = re.compile(
    r"记住|保存|存下来|长期|以后|默认|优先|偏好|请",
    re.IGNORECASE,
)
_SECRET_FIELD_RE = re.compile(
    r"api[ _-]?key|access[ _-]?token|refresh[ _-]?token|password|passwd|"
    r"credential|secret|private[ _-]?key|密钥|密码|口令|令牌|私钥",
    re.IGNORECASE,
)
_SECRET_VALUE_RE = re.compile(
    r"(?:^|\b)(?:sk-[A-Za-z0-9_-]+|gh[pousr]_[A-Za-z0-9_]+|AKIA[A-Z0-9]{12,})",
    re.IGNORECASE,
)
_SENSITIVE_FIELD_RE = re.compile(
    r"email|e-mail|phone|mobile|address|身份证|邮箱|手机号|电话|住址|生日",
    re.IGNORECASE,
)
_VOLATILE_FIELD_RE = re.compile(
    r"price|weather|stock|market|news|exchange|rate|today|current|latest|"
    r"价格|天气|股票|股市|新闻|汇率|行情|今天|今日|当前|最新|实时|近期|本周",
    re.IGNORECASE,
)


def _classify_extracted_fact(
    category: str,
    key: str,
    value: str,
    text: str,
) -> tuple[bool, bool, bool, bool]:
    """Project deterministic safety flags before a fact reaches policy.

    Ordinary user statements remain persistable evidence for the existing
    fact-capture contract. Sensitive statements need an explicit persistence
    request, while secrets and volatile observations never reach a store.
    """
    field = f"{category}.{key}"
    generic_field = key.lower() in {"fact", "preference", "value"}
    field_secret = bool(_SECRET_FIELD_RE.search(field))
    field_sensitive = bool(_SENSITIVE_FIELD_RE.search(field))
    secret = bool(
        field_secret
        or (generic_field and _SECRET_FIELD_RE.search(text))
        or _SECRET_VALUE_RE.search(value)
    )
    sensitive = bool(
        field_sensitive
        or (generic_field and _SENSITIVE_FIELD_RE.search(text))
    )
    volatile = bool(_VOLATILE_FIELD_RE.search(field))
    explicit_persist = bool(_EXPLICIT_PERSIST_RE.search(text))
    if not sensitive:
        # Existing non-sensitive fact capture treats a direct first-person
        # statement as durable evidence even without the word "记住".
        explicit_persist = True
    return explicit_persist, sensitive, secret, volatile


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


class FactExtractionResult(dict[str, dict[str, str]]):
    """Committed fact projection returned by the learning boundary.

    It remains a dict for the existing answer projection, while exposing the
    durable commit evidence needed for truthful user-facing acknowledgements.
    Facts rejected or failed by the boundary are intentionally absent.
    """

    def __init__(
        self,
        facts: dict[str, dict[str, str]] | None = None,
        commits: Iterable[MemoryCommitEvidence] = (),
    ) -> None:
        super().__init__(facts or {})
        self.commit_evidence = tuple(commits)

    @property
    def committed(self) -> bool:
        return bool(self)


async def async_extract_and_save_facts(
    user_id: str,
    text: str,
    *,
    scope: str = "user",
) -> FactExtractionResult:
    """Extract facts and persist only decisions approved by the boundary."""
    if not text or len(text) < 3:
        return FactExtractionResult()

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
        return FactExtractionResult()

    committed_facts: dict[str, dict[str, str]] = {}
    commit_evidence: list[MemoryCommitEvidence] = []
    for category, items in facts.items():
        if isinstance(items, dict):
            for key, value in items.items():
                if value and str(value).strip():
                    category_name = str(category).strip()
                    key_name = str(key).strip()
                    value_text = str(value).strip()
                    canonical_key = f"{category_name}.{key_name}"
                    from agent.memory.long_term import get_fact

                    existing_value = get_fact(
                        user_id,
                        category_name,
                        key_name,
                        scope=scope,
                    )
                    existing = (
                        ExistingMemory(
                            scope=scope,
                            canonical_key=canonical_key,
                            value=existing_value,
                        )
                        if existing_value is not None
                        else None
                    )
                    explicit_persist, sensitive, secret, volatile = (
                        _classify_extracted_fact(
                            category_name,
                            key_name,
                            value_text,
                            text,
                        )
                    )
                    evidence_id = f"fact-{uuid.uuid4().hex}"
                    evidence = InteractionEvidence(
                        evidence_id=evidence_id,
                        source_kind="user_statement",
                        source_ref=f"user-input:{evidence_id}",
                        text=text,
                        memory_type=(
                            "preference"
                            if key_name in {"preference", "language", "editor", "framework", "os"}
                            else "fact"
                        ),
                        requested_scope=scope,
                        canonical_key=canonical_key,
                        value=value_text,
                        explicit_persist=explicit_persist,
                        sensitive=sensitive,
                        secret=secret,
                        volatile=volatile,
                        existing=existing,
                    )
                    policy = MemoryPolicyProjection(
                        scope=scope,
                        namespace=user_id,
                    )
                    decision = decide_memory_learning(evidence, policy)
                    commit = MemoryPersistenceBoundary.commit(decision, policy)
                    commit_evidence.append(commit)
                    if commit.committed:
                        committed_facts.setdefault(category_name, {})[key_name] = value_text

    return FactExtractionResult(committed_facts, commit_evidence)


__all__ = [
    "FactExtractionResult",
    "async_extract_and_save_facts",
    "extract_facts_with_llm",
]
