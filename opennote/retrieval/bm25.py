"""BM25 keyword-based retrieval over notebook chunks.

Complements the vector similarity search in ``opennote.retrieval.retriever``.
Uses rank-bm25's ``BM25Okapi`` for scoring and normalises scores to the
same [0, 1] range as vector similarities so the two can be combined via
reranking or linear interpolation.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from rank_bm25 import BM25Okapi

from opennote.notebooks import Notebook
from opennote.retrieval.citations import citation_for

logger = logging.getLogger(__name__)


def _tokenise(text: str) -> List[str]:
    """Very simple word-level tokenisation matching rank-bm25 conventions."""
    return text.lower().split()


class Bm25Retriever:
    """BM25 keyword retriever for a single notebook.

    The corpus is snapshotted at construction. Call :meth:`refresh` after
    ingesting new sources so the index reflects the current chunks.
    """

    def __init__(self, notebook: Notebook) -> None:
        self.notebook = notebook
        self._documents: List[str] = []
        self._metadatas: List[Dict[str, Any]] = []
        self._ids: List[str] = []
        self._corpus: List[List[str]] = []
        self.bm25: Optional[BM25Okapi] = None
        self._load_corpus()

    def _load_corpus(self) -> None:
        """Load chunk documents, metadata, and ids straight from ChromaDB.

        Uses the persistent client directly (not VectorStoreManager) so a pure
        keyword search never pays for loading an embedding model.
        """
        import chromadb

        client = chromadb.PersistentClient(path=str(self.notebook.store_dir))
        try:
            try:
                collection = client.get_collection("documents")
            except Exception:
                # No collection yet -> empty corpus; search returns [].
                logger.info("No document collection found for %s; BM25 corpus empty.", self.notebook.name)
                return
            got = collection.get(include=["documents", "metadatas"])
            documents = got.get("documents", []) or []
            metadatas = got.get("metadatas", []) or []
            ids = got.get("ids", []) or []
            self._documents = list(documents)
            self._metadatas = [dict(m or {}) for m in metadatas]
            self._ids = [str(i) for i in ids]
            # Tokenise the corpus once so BM25 scores are word-level, not char-level.
            self._corpus = [_tokenise(d) for d in self._documents]
            if self._corpus:
                self.bm25 = BM25Okapi(self._corpus)
            else:
                self.bm25 = None
            logger.info("BM25 corpus loaded: %d chunks for %s.", len(self._documents), self.notebook.name)
        finally:
            try:
                if hasattr(client, "close"):
                    client.close()
                elif hasattr(client, "_system") and hasattr(client._system, "stop"):
                    client._system.stop()
            except Exception:
                pass

    def refresh(self) -> None:
        """Rebuild the index from the current store (call after ingest)."""
        self._load_corpus()

    def search(
        self,
        query: str,
        top_k: int = 5,
        source: Optional[str] = None,
    ) -> List[SearchResult]:
        """BM25 search for ``query``, returning ``top_k`` results.

        ``source`` optionally filters to a single filename.
        """
        from opennote.retrieval.retriever import SearchResult

        if not self._documents or self.bm25 is None:
            return []
        tokenised_query = _tokenise(query)
        query_terms = set(tokenised_query)
        scores = self.bm25.get_scores(tokenised_query)
        # Optional per-source filter, mirroring Retriever.search's where_filter.
        if source is not None:
            kept = [
                i
                for i in range(len(self._documents))
                if (self._metadatas[i].get("filename") or "") == source
            ]
        else:
            kept = list(range(len(self._documents)))
        if not kept:
            return []
        # A document "matches" only if it shares at least one query token.
        # BM25 scores can legitimately be negative for very common terms
        # (negative IDF), so we must not filter on the sign of the score.
        kept = [
            i
            for i in kept
            if query_terms & set(self._corpus[i])
        ]
        kept_sorted = sorted(kept, key=lambda i: scores[i], reverse=True)[:top_k]
        if not kept_sorted:
            return []
        max_score = max((scores[i] for i in kept_sorted), default=0.0)
        divisor = max_score if max_score > 0 else 1.0
        results: List[SearchResult] = []
        for idx in kept_sorted:
            meta = dict(self._metadatas[idx])
            meta.setdefault("chunk_id", self._ids[idx])
            results.append(
                SearchResult(
                    content=self._documents[idx],
                    metadata=meta,
                    similarity=float(scores[idx]) / divisor,
                    citation=citation_for(meta),
                )
            )
        return results


def hybrid_search(
    vector_results: List["SearchResult"],
    bm25_results: List["SearchResult"],
    top_k: int = 5,
    alpha: float = 0.5,
) -> List["SearchResult"]:
    """Combine vector and BM25 scores and rerank.

    *Vector scores* come from ``retriever.search()`` (cosine similarity in
    [0, 1]).  *BM25 scores* are normalised to [0, 1] by dividing by the max
    score in the retrieval set.

    The combined score is ``alpha * vector + (1 - alpha) * bm25``, then results
    are re-sorted.  ``alpha=0.5`` gives equal weight; increase alpha to favour
    vector similarity, decrease to favour BM25 keyword matching.

    Results are merged per-chunk (keyed by chunk id), so multiple chunks of the
    same source can all survive into the final ranking.
    """
    if not vector_results and not bm25_results:
        return []

    def _key(res: "SearchResult") -> str:
        return res.id or str(hash((res.content, res.citation.label)))

    vec_map = {_key(r): r for r in vector_results}
    bm25_map = {_key(r): r for r in bm25_results}

    combined: List[tuple[float, "SearchResult"]] = []
    all_keys = set(vec_map.keys()) | set(bm25_map.keys())
    for key in all_keys:
        v = vec_map[key].similarity if key in vec_map else 0.0
        b = bm25_map[key].similarity if key in bm25_map else 0.0
        score = alpha * v + (1 - alpha) * b
        result = vec_map.get(key) or bm25_map.get(key)
        if result is not None:
            combined.append((score, result))

    combined.sort(key=lambda x: x[0], reverse=True)
    return [r for _, r in combined[:top_k]]