from pathlib import Path
from typing import Optional

from langchain_chroma import Chroma
from langchain_core.documents import Document

DATA_DIR = Path(__file__).parent.parent.parent / "data"
CHROMA_PATH = DATA_DIR / "semantic_memory"
CHROMA_PATH.mkdir(parents=True, exist_ok=True)

_vector_store: Optional[Chroma] = None


def _get_vector_store() -> Chroma:
    global _vector_store
    if _vector_store is None:
        from agent.embeddings import get_embedding

        _vector_store = Chroma(
            collection_name="conversation",
            embedding_function=get_embedding(),
            persist_directory=str(CHROMA_PATH),
        )
    return _vector_store


def retrieve_similar_memories(
    user_id: str,
    query: str,
    k: int = 3,
) -> str:
    store = _get_vector_store()
    try:
        docs_scores = store.similarity_search_with_score(
            query,
            k=k,
            filter={"user_id": user_id},
        )
    except Exception:
        docs_scores = store.similarity_search_with_score(query, k=k)

    if not docs_scores:
        return ""

    # Chroma L2 距离：越小越相似；取 top-k，不做硬截断
    return "\n---\n".join(doc.page_content for doc, _ in docs_scores)


def add_conversation_memory(user_id: str, user_input: str, assistant_response: str):
    doc = Document(
        page_content=f"用户: {user_input}\n助手: {assistant_response}",
        metadata={"user_id": user_id},
    )
    _get_vector_store().add_documents([doc])
