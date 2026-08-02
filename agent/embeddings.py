import os
from typing import Optional

EMBEDDING_MODEL = "all-MiniLM-L6-v2"

_embedding_instance: Optional["HuggingFaceEmbeddings"] = None


def allow_model_downloads() -> bool:
    return os.getenv("TSAGENT_ALLOW_MODEL_DOWNLOAD", "").lower() in {
        "1",
        "true",
        "yes",
    }


def get_embedding():
    """Get singleton embedding instance (lazy loaded)"""
    global _embedding_instance
    if _embedding_instance is None:
        from langchain_huggingface import HuggingFaceEmbeddings

        _embedding_instance = HuggingFaceEmbeddings(
            model_name=EMBEDDING_MODEL,
            model_kwargs={"local_files_only": not allow_model_downloads()},
        )
    return _embedding_instance


def create_huggingface_embeddings():
    """Legacy function for backward compatibility"""
    return get_embedding()
