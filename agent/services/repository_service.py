from pathlib import Path

from agent.repository.indexer import (
    RepositoryIndexer,
    get_repository_indexer,
    set_repository_indexer,
)

class RepositoryService:
    @staticmethod
    def initialize(repo_root: Path):
        indexer = RepositoryIndexer(repo_root)
        set_repository_indexer(indexer)
        indexer.ensure_built()
        return indexer

    @staticmethod
    def search_similar(query: str, k: int = 5):
        repository_indexer = get_repository_indexer()
        if repository_indexer:
            return repository_indexer.search_similar(query, k)
        return []

    @staticmethod
    def find_symbol(symbol: str):
        repository_indexer = get_repository_indexer()
        if repository_indexer:
            return repository_indexer.find_symbol(symbol)
        return None

    @staticmethod
    def build_index():
        repository_indexer = get_repository_indexer()
        if repository_indexer:
            repository_indexer.rebuild()
