import json
from pathlib import Path
from typing import Dict, List, Optional

from agent.security import is_internal_storage_path

IGNORE_DIRS = {
    ".git",
    "venv",
    ".venv",
    "node_modules",
    "dist",
    "build",
    "__pycache__",
    ".repo_index",
}

MAX_FILE_SIZE = 1024 * 1024  # 1MB
INDEX_DIR_NAME = ".repo_index"
SYMBOL_INDEX_FILE = ".symbol_index.json"
FILE_SYMBOLS_FILE = ".file_symbols.json"


class RepositoryIndexer:
    def __init__(self, repo_root: Path):
        self.repo_root = repo_root

        self.embedding = None

        self.vector_store = None

        self.symbol_index: Dict[str, str] = {}

        self.file_symbols: Dict[str, list] = {}

        self.splitter = None

        self._load_existing_index()

    @property
    def index_dir(self) -> Path:
        return self.repo_root / INDEX_DIR_NAME

    @property
    def symbol_index_path(self) -> Path:
        return self.repo_root / SYMBOL_INDEX_FILE

    @property
    def file_symbols_path(self) -> Path:
        return self.repo_root / FILE_SYMBOLS_FILE

    def _load_existing_index(self) -> None:
        # Only load symbol index eagerly; vector store is lazy loaded
        if self.symbol_index_path.exists():
            try:
                self.symbol_index = json.loads(
                    self.symbol_index_path.read_text(encoding="utf-8")
                )
            except (OSError, json.JSONDecodeError):
                self.symbol_index = {}
        if self.file_symbols_path.exists():
            try:
                self.file_symbols = json.loads(
                    self.file_symbols_path.read_text(encoding="utf-8")
                )
            except (OSError, json.JSONDecodeError):
                self.file_symbols = {}

    def _get_embedding(self):
        if self.embedding is None:
            from agent.embeddings import get_embedding

            self.embedding = get_embedding()
        return self.embedding

    def _get_splitter(self):
        if self.splitter is None:
            from langchain_text_splitters import RecursiveCharacterTextSplitter

            self.splitter = RecursiveCharacterTextSplitter(
                chunk_size=500,
                chunk_overlap=50,
            )
        return self.splitter

    @staticmethod
    def _get_chroma():
        from langchain_chroma import Chroma

        return Chroma

    def _should_ignore(self, path: Path) -> bool:
        if any(part in IGNORE_DIRS for part in path.parts):
            return True

        # Repository grounding must never expose Agent-owned persistence
        # (memory databases, runtime stores, or vector indexes) to Planner.
        if is_internal_storage_path(path):
            return True

        if path.is_file():
            try:
                if path.stat().st_size > MAX_FILE_SIZE:
                    return True
            except Exception:
                return True

        return False

    def _extract_symbols(
        self,
        content: str,
        file_path: str,
    ) -> None:
        """
        简单提取 Python symbol（同时维护按文件的有序符号列表 file_symbols）。
        """
        if file_path not in self.file_symbols:
            self.file_symbols[file_path] = []

        for line in content.splitlines():
            line = line.strip()

            if line.startswith("def "):
                symbol = (
                    line.replace("def ", "")
                    .split("(")[0]
                    .strip()
                )
                self.symbol_index[symbol] = file_path
                self.file_symbols[file_path].append(symbol)

            elif line.startswith("class "):
                symbol = (
                    line.replace("class ", "")
                    .split("(")[0]
                    .replace(":", "")
                    .strip()
                )
                self.symbol_index[symbol] = file_path
                self.file_symbols[file_path].append(symbol)

    def build(self) -> None:
        docs = []
        vector_dependencies_available = True
        try:
            from langchain_core.documents import Document

            Chroma = self._get_chroma()
            splitter = self._get_splitter()
            embedding = self._get_embedding()
        except Exception:
            Document = None
            Chroma = None
            splitter = None
            embedding = None
            vector_dependencies_available = False

        self.symbol_index = {}
        self.file_symbols = {}

        for file_path in self.repo_root.rglob("*"):
            if file_path.is_dir():
                continue

            if self._should_ignore(file_path):
                continue

            try:
                content = file_path.read_text(
                    encoding="utf-8",
                    errors="ignore",
                )

                relative_path = str(
                    file_path.relative_to(self.repo_root)
                )

                if vector_dependencies_available:
                    chunks = splitter.split_text(content)

                    for i, chunk in enumerate(chunks):
                        docs.append(
                            Document(
                                page_content=chunk,
                                metadata={
                                    "path": relative_path,
                                    "chunk": i,
                                },
                            )
                        )

                self._extract_symbols(
                    content,
                    relative_path,
                )

            except Exception as e:
                print(
                    f"跳过文件 {file_path}: {e}"
                )

        if docs and Chroma is not None:
            self.vector_store = Chroma.from_documents(
                documents=docs,
                embedding=embedding,
                persist_directory=str(self.index_dir),
                collection_name="repository_code",
            )

        self.symbol_index_path.write_text(
            json.dumps(
                self.symbol_index,
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        self.file_symbols_path.write_text(
            json.dumps(
                self.file_symbols,
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    def ensure_built(self) -> None:
        if not self.index_dir.exists() or not self.symbol_index_path.exists():
            self.build()
            return

        self._load_existing_index()

    def rebuild(self) -> None:
        self.build()

    def search_similar(
        self,
        query: str,
        k: int = 5,
    ) -> List[Dict]:
        # Lazy load vector store on first search
        if self.vector_store is None and self.index_dir.exists():
            try:
                Chroma = self._get_chroma()
                self.vector_store = Chroma(
                    collection_name="repository_code",
                    embedding_function=self._get_embedding(),
                    persist_directory=str(self.index_dir),
                )
            except Exception:
                pass

        if self.vector_store is None:
            return []

        try:
            docs = self.vector_store.similarity_search(
                query=query,
                k=k,
            )
        except Exception:
            return []

        return [
            {
                "path": d.metadata["path"],
                "content": d.page_content[:200],
            }
            for d in docs
            if not is_internal_storage_path(d.metadata.get("path", ""))
        ]

    def find_symbol(
        self,
        symbol: str,
    ) -> Optional[str]:
        if not self.symbol_index:
            self._load_existing_index()

        return self.symbol_index.get(symbol)

    def symbols_in_file(
        self,
        path: str,
    ) -> List[str]:
        """返回指定文件的有序符号列表（文件内定义顺序，Ordinal 解析用）。"""
        if not self.file_symbols:
            self._load_existing_index()

        return list(self.file_symbols.get(path, []))


# 全局单例
repository_indexer: Optional[RepositoryIndexer] = None


def set_repository_indexer(indexer: RepositoryIndexer) -> None:
    global repository_indexer
    repository_indexer = indexer


def get_repository_indexer() -> Optional[RepositoryIndexer]:
    return repository_indexer
