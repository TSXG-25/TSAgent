from agent.repository.indexer import get_repository_indexer

class RepositoryMemory:
    @staticmethod
    def search(query: str, k: int = 5):
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
