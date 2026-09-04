"""Long-Term Memory (Layer 3) — 语义摘要存储 + 用户事实固化。

存储经 LLM 压缩后的对话摘要到 ChromaDB。
用户偏好事实存储到 SQLite。
"""
import sqlite3
import json
import hashlib
from pathlib import Path
from typing import Any, Optional

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

def _persist_summary(
    user_id: str,
    summary: str,
    *,
    scope: str,
    canonical_key: str,
    evidence_id: str,
    source_kind: str,
    source_ref: str,
) -> dict[str, Any]:
    """Persist one authorized summary and return its storage receipt."""
    from datetime import datetime

    if not summary or len(summary.strip()) < 5:
        raise ValueError("summary must contain at least five non-whitespace characters")
    record_id = "summary-" + hashlib.sha256(
        f"{scope}\0{user_id}\0{canonical_key}\0{evidence_id}".encode("utf-8")
    ).hexdigest()
    doc = Document(
        page_content=summary.strip(),
        metadata={
            "user_id": user_id,
            "scope": scope,
            "canonical_key": canonical_key,
            "evidence_id": evidence_id,
            "source_kind": source_kind,
            "source_ref": source_ref,
            "record_id": record_id,
            "type": "summary",
            "timestamp": datetime.now().isoformat(),
        },
    )
    store = _get_store()
    existing = store.get(ids=[record_id])
    if existing.get("ids"):
        return {"record_id": record_id, "revision": 1}
    store.add_documents([doc], ids=[record_id])
    return {"record_id": record_id, "revision": 1}


def retrieve_summaries(
    user_id: str,
    query: str,
    k: int = 5,
    *,
    scope: str = "user",
) -> str:
    """Retrieve semantically relevant long-term summaries."""
    try:
        store = _get_store()
    except Exception:
        return ""
    try:
        docs_scores = store.similarity_search_with_score(
            query,
            k=k,
            filter={"$and": [{"user_id": user_id}, {"scope": scope}]},
        )
    except Exception:
        # A failed scoped query must never degrade into a global query.
        return ""

    if not docs_scores:
        return ""

    texts = []
    for doc, score in docs_scores:
        ts = doc.metadata.get("timestamp", "") if doc.metadata else ""
        time_tag = f"[{ts[:16]}] " if ts else ""
        texts.append(f"- {time_tag}{doc.page_content}")

    return "\n".join(texts)


def retrieve_all_summaries(user_id: str, *, scope: str = "user") -> list[str]:
    """Get all long-term summaries for a user."""
    try:
        store = _get_store()
    except Exception:
        return []
    try:
        results = store.get(where={"$and": [{"user_id": user_id}, {"scope": scope}]})
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
                scope TEXT NOT NULL DEFAULT 'user',
                evidence_id TEXT NOT NULL DEFAULT '',
                source_kind TEXT NOT NULL DEFAULT '',
                source_ref TEXT NOT NULL DEFAULT '',
                revision INTEGER NOT NULL DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(user_id, scope, category, key)
            )
        """)
        columns = {
            str(row[1])
            for row in conn.execute("PRAGMA table_info(facts)").fetchall()
        }
        migrations = {
            "scope": "TEXT NOT NULL DEFAULT 'user'",
            "evidence_id": "TEXT NOT NULL DEFAULT ''",
            "source_kind": "TEXT NOT NULL DEFAULT ''",
            "source_ref": "TEXT NOT NULL DEFAULT ''",
            "revision": "INTEGER NOT NULL DEFAULT 1",
        }
        for name, definition in migrations.items():
            if name not in columns:
                conn.execute(f"ALTER TABLE facts ADD COLUMN {name} {definition}")

        scoped_unique = False
        for index in conn.execute("PRAGMA index_list('facts')").fetchall():
            if not int(index[2]):
                continue
            index_name = str(index[1]).replace('"', '""')
            index_columns = tuple(
                str(row[2])
                for row in conn.execute(
                    f'PRAGMA index_info("{index_name}")'
                ).fetchall()
            )
            if index_columns == ("user_id", "scope", "category", "key"):
                scoped_unique = True
                break

        if not scoped_unique:
            conn.execute("""
                CREATE TABLE facts_scope_migration (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL,
                    category TEXT NOT NULL,
                    key TEXT NOT NULL,
                    value TEXT NOT NULL,
                    scope TEXT NOT NULL DEFAULT 'user',
                    evidence_id TEXT NOT NULL DEFAULT '',
                    source_kind TEXT NOT NULL DEFAULT '',
                    source_ref TEXT NOT NULL DEFAULT '',
                    revision INTEGER NOT NULL DEFAULT 1,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(user_id, scope, category, key)
                )
            """)
            conn.execute("""
                INSERT INTO facts_scope_migration (
                    id, user_id, category, key, value, scope, evidence_id,
                    source_kind, source_ref, revision, created_at
                )
                SELECT id, user_id, category, key, value, scope, evidence_id,
                       source_kind, source_ref, revision, created_at
                FROM facts
                ORDER BY id
            """)
            conn.execute("DROP TABLE facts")
            conn.execute("ALTER TABLE facts_scope_migration RENAME TO facts")


_init_facts_db()


def _persist_fact(
    user_id: str,
    category: str,
    key: str,
    value: str,
    *,
    scope: str,
    action: str,
    evidence_id: str,
    source_kind: str,
    source_ref: str,
) -> dict[str, Any]:
    """Persist one authorized fact/preference and return its receipt."""
    normalized_value = str(value).strip()
    if not normalized_value:
        raise ValueError("fact value must be non-empty")
    with sqlite3.connect(FACTS_DB_PATH) as conn:
        row = conn.execute(
            """SELECT id, revision, evidence_id, value FROM facts
               WHERE user_id = ? AND scope = ? AND category = ? AND key = ?""",
            (user_id, scope, category, key),
        ).fetchone()
        if row is not None:
            record_id, revision, previous_evidence_id, previous_value = row
            if previous_evidence_id == evidence_id and previous_value == normalized_value:
                return {"record_id": str(record_id), "revision": int(revision)}
            if action != "UPDATE":
                raise ValueError("canonical fact already exists")
            next_revision = int(revision) + 1
            conn.execute(
                """UPDATE facts
                   SET value = ?, scope = ?, evidence_id = ?, source_kind = ?,
                       source_ref = ?, revision = ?
                   WHERE id = ?""",
                (
                    normalized_value,
                    scope,
                    evidence_id,
                    source_kind,
                    source_ref,
                    next_revision,
                    record_id,
                ),
            )
            return {"record_id": str(record_id), "revision": next_revision}

        cursor = conn.execute(
            """INSERT INTO facts (
                   user_id, category, key, value, scope, evidence_id,
                   source_kind, source_ref, revision
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1)""",
            (
                user_id,
                category,
                key,
                normalized_value,
                scope,
                evidence_id,
                source_kind,
                source_ref,
            ),
        )
        return {"record_id": str(cursor.lastrowid), "revision": 1}


def get_facts(user_id: str, *, scope: str | None = None) -> dict[str, dict[str, str]]:
    """Get all facts for a user, organized by category."""
    try:
        with sqlite3.connect(FACTS_DB_PATH) as conn:
            if scope is None:
                rows = conn.execute(
                    "SELECT category, key, value FROM facts WHERE user_id = ?",
                    (user_id,),
                ).fetchall()
            else:
                rows = conn.execute(
                    """SELECT category, key, value FROM facts
                       WHERE user_id = ? AND scope = ?""",
                    (user_id, scope),
                ).fetchall()
    except Exception:
        return {}

    result: dict[str, dict[str, str]] = {}
    for category, key, value in rows:
        result.setdefault(category, {})[key] = value
    return result


def get_fact(
    user_id: str,
    category: str,
    key: str,
    *,
    scope: str | None = None,
) -> str | None:
    """Read one existing fact for decision-time deduplication."""
    try:
        with sqlite3.connect(FACTS_DB_PATH) as conn:
            if scope is None:
                row = conn.execute(
                    "SELECT value FROM facts WHERE user_id = ? AND category = ? AND key = ?",
                    (user_id, category, key),
                ).fetchone()
            else:
                row = conn.execute(
                    """SELECT value FROM facts
                       WHERE user_id = ? AND category = ? AND key = ? AND scope = ?""",
                    (user_id, category, key, scope),
                ).fetchone()
    except sqlite3.Error:
        return None
    return None if row is None else str(row[0])


def get_facts_text(user_id: str, *, scope: str | None = None) -> str:
    """Get facts as readable text for system prompt."""
    facts = get_facts(user_id, scope=scope)
    if not facts:
        return ""

    lines = []
    for category, items in facts.items():
        for key, value in items.items():
            lines.append(f"- {category}.{key}: {value}")

    return "\n".join(lines)


# === Clear ===

def clear_summaries(user_id: str, *, scope: str | None = None) -> None:
    """Clear semantic summaries for one user namespace."""
    try:
        where: dict[str, object] = (
            {"user_id": user_id}
            if scope is None
            else {"$and": [{"user_id": user_id}, {"scope": scope}]}
        )
        _get_store().delete(where=where)
    except Exception:
        pass


def clear_facts(user_id: str) -> None:
    """Clear extracted facts for one user namespace."""
    try:
        with sqlite3.connect(FACTS_DB_PATH) as conn:
            conn.execute("DELETE FROM facts WHERE user_id = ?", (user_id,))
    except Exception:
        pass


def clear_all(user_id: str) -> None:
    """Clear summaries and facts for one user namespace."""
    clear_summaries(user_id)
    clear_facts(user_id)
