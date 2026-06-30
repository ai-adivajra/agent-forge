"""
search.py — Knowledge base search API.

Single responsibility: question → ranked list of SearchResult.

No CLI. No OpenClaw. No prompt engineering.
Called by search_demo.py (interactive) and inject.py (OpenClaw integration).

Contract:
    searcher = Searcher()
    results  = searcher.search("What is the OpenClaw session directory?", top_k=5)
    # → list[SearchResult], sorted by cosine similarity descending
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import SETTINGS, expand
from index.embedder import Embedder
from index.vector_store import VectorStore, SearchResult


class SearchError(Exception):
    pass


class Searcher:

    def __init__(
        self,
        db_path: str | None = None,
        model:   str | None = None,
    ):
        idx_cfg = SETTINGS.get("index", {})
        resolved_db = expand(db_path or idx_cfg.get("db", "~/.openclaw/embeddings.sqlite"))

        self._store    = VectorStore(resolved_db)
        self._embedder = Embedder(model=model)

    def search(
        self,
        question: str,
        top_k:    int = 5,
        domains:  list[str] | None = None,
    ) -> list[SearchResult]:
        """
        Embed the question and return the top_k most similar knowledge notes.

        domains — if provided, restrict search to notes in those domains.

        Raises SearchError if:
        - Ollama is unreachable
        - The index is empty
        """
        question = question.strip()
        if not question:
            return []

        if not self._embedder.ping():
            raise SearchError(
                "Ollama is not reachable. "
                "Start it with: systemctl --user start ollama"
            )

        stats = self._store.stats()
        if stats.total_notes == 0:
            raise SearchError(
                "Index is empty. "
                "Run: python build_index.py"
            )

        try:
            query_vector = self._embedder.embed(question)
        except Exception as e:
            raise SearchError(f"Embedding failed: {e}") from e

        results = self._store.search(query_vector, top_k=top_k, domains=domains)
        return results

    @property
    def index_stats(self):
        return self._store.stats()
