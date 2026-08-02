"""Long-Term Memory (Layer 3) — 语义摘要存储 + 用户事实固化。

存储经 LLM 压缩后的对话摘要到 ChromaDB。
用户偏好事实存储到 SQLite。
"""
import sqlite3
import json
from pathlib import Path
from typing import Optional

from langchain_chroma import Chroma
from langchain_core.documents import Document

# === Storage ===
DATA_DIR = Path(__file__).parent.parent.parent / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

# ChromaDB for semantic summaries
CHROMA_PATH = DATA_DIR / "long_term_memory"
CHROMA_PATH.mkdir(parents=True, exist_ok=True)

# SQLite for user facts
FACTS_DB_PATH = DATA_DIR / "user_facts.db"

_vector_store: Optional[Chroma] = None


def _get_store() -> Chroma:
    global _vector_store
    if _vector_store is None:
        from agent.embeddings import get_embedding
        _vector_store = Chroma(
            collection_name="long_term",
            embedding_function=get_embedding(),
            persist_directory=str(CHROMA_PATH),
        )
    return _vector_store


# === Summaries (ChromaDB) ===

def store_summary(user_id: str, summary: str) -> None:
    """Store a conversation summary in long-term memory."""
    from datetime import datetime
    if not summary or len(summary) < 5:
        return
    doc = Document(
        page_content=summary,
        metadata={
            "user_id": user_id,
            "type": "summary",
            "timestamp": datetime.now().isoformat(),
        },
    )
    _get_store().add_documents([doc])


def retrieve_summaries(user_id: str, query: str, k: int = 5) -> str:
    """Retrieve semantically relevant long-term summaries."""
    store = _get_store()
    try:
        docs_scores = store.similarity_search_with_score(
            query,
            k=k,
            filter={"user_id": user_id},
        )
    except Exception:
        try:
            docs_scores = store.similarity_search_with_score(query, k=k)
        except Exception:
            return ""

    if not docs_scores:
        return ""

    texts = []
    for doc, score in docs_scores:
        ts = doc.metadata.get("timestamp", "") if doc.metadata else ""
        time_tag = f"[{ts[:16]}] " if ts else ""
        texts.append(f"- {time_tag}{doc.page_content}")

    return "\n".join(texts)


def retrieve_all_summaries(user_id: str) -> list[str]:
    """Get all long-term summaries for a user."""
    store = _get_store()
    try:
        results = store.get(where={"user_id": user_id})
    except Exception:
        return []

    if not results:
        return []

    docs = results.get("documents", [])
    metas = results.get("metadatas", []) or []
    output = []
    for i, doc in enumerate(docs):
        if not doc:
            continue
        ts = metas[i].get("timestamp", "") if i < len(metas) and metas[i] else ""
        time_tag = f"[{ts[:16]}] " if ts else ""
        output.append(f"{time_tag}{str(doc)}")
    return output


# === User Facts (SQLite) ===

def _init_facts_db() -> None:
    with sqlite3.connect(FACTS_DB_PATH) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS facts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                category TEXT NOT NULL,
                key TEXT NOT NULL,
                value TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(user_id, category, key)
            )
        """)


_init_facts_db()


def save_fact(user_id: str, category: str, key: str, value: str) -> None:
    """Save a user fact."""
    try:
        with sqlite3.connect(FACTS_DB_PATH) as conn:
            conn.execute(
                """INSERT OR REPLACE INTO facts (user_id, category, key, value)
                   VALUES (?, ?, ?, ?)""",
                (user_id, category, key, str(value)),
            )
    except Exception:
        pass


def get_facts(user_id: str) -> dict[str, dict[str, str]]:
    """Get all facts for a user, organized by category."""
    try:
        with sqlite3.connect(FACTS_DB_PATH) as conn:
            rows = conn.execute(
                "SELECT category, key, value FROM facts WHERE user_id = ?",
                (user_id,),
            ).fetchall()
    except Exception:
        return {}

    result = {}
    for category, key, value in rows:
        result.setdefault(category, {})[key] = value
    return result


def get_facts_text(user_id: str) -> str:
    """Get facts as readable text for system prompt."""
    facts = get_facts(user_id)
    if not facts:
        return ""

    lines = []
    for category, items in facts.items():
        for key, value in items.items():
            lines.append(f"- {category}.{key}: {value}")

    return "\n".join(lines)


# === Clear ===

def clear_all(user_id: str) -> None:
    """Clear all long-term memories for a user."""
    store = _get_store()
    try:
        store.delete(where={"user_id": user_id})
    except Exception:
        pass
    try:
        with sqlite3.connect(FACTS_DB_PATH) as conn:
            conn.execute("DELETE FROM facts WHERE user_id = ?", (user_id,))
    except Exception:
        pass