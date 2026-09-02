"""Retriever: ranked retrieval over a notebook with normalized results."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from opennote.notebooks import COLLECTION_NAME, Notebook
from opennote.retrieval.bm25 import Bm25Retriever, hybrid_search
from opennote.retrieval.citations import Citation, citation_for
from opennote.store.vectors import VectorStoreManager

logger = logging.getLogger(__name__)


@dataclass
class SearchResult:
    """A normalized retrieval hit with a ready-made citation."""

    content: str
    metadata: Dict[str, Any]
    similarity: float
    citation: Citation

    @property
    def id(self) -> str:
        return self.metadata.get("chunk_id", "") or str(self.metadata.get("id", ""))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "content": self.content,
            "metadata": self.metadata,
            "similarity": self.similarity,
            "citation": self.citation.to_dict(),
        }


class Retriever:
    """Searches a notebook's vector store, returning SearchResult objects.

    Constructing a Retriever loads the notebook's embedding model (read-only —
    it never creates or mutates the collection).
    """

    def __init__(
        self,
        notebook: Notebook,
        top_k: int = 5,
        device: Optional[str] = None,
        use_bm25: bool = False,
        bm25_alpha: float = 0.5,
    ) -> None:
        self.notebook = notebook
        self.top_k = top_k
        self._mgr = VectorStoreManager(
            collection_name=COLLECTION_NAME,
            store_dir=notebook.store_dir,
            model_name=notebook.embed_model,
            device=device,
            read_only=True,
        )
        self.use_bm25 = use_bm25
        self.bm25_alpha = bm25_alpha
        # Always initialised (None when disabled) so search() never hits an
        # AttributeError if someone flips use_bm25 on after construction.
        self._bm25: Optional[Bm25Retriever] = None
        if use_bm25:
            self._bm25 = Bm25Retriever(notebook)

    def _search_vector(
        self,
        query: str,
        top_k: int,
        source: Optional[str],
    ) -> List[SearchResult]:
        """Vector-only search (no BM25). Used by the hybrid path to avoid
        re-entering ``search()`` and recursing forever."""
        where_filter = {"filename": source} if source else None
        raw = self._mgr.search(
            query,
            top_k=top_k,
            where_filter=where_filter,
        )
        return [
            SearchResult(
                content=r["content"],
                metadata={**r["metadata"], "id": r["id"]},
                similarity=r["similarity"],
                citation=citation_for(r["metadata"]),
            )
            for r in raw
        ]

    def search(
        self,
        query: str,
        top_k: Optional[int] = None,
        source: Optional[str] = None,
    ) -> List[SearchResult]:
        """Retrieve top-k chunks for ``query``.

        ``source`` optionally filters to a single filename.
        If ``use_bm25`` is enabled, results are hybridised with BM25 and reranked.
        """
        k = self.top_k if top_k is None else top_k
        if not isinstance(k, int) or k < 1:
            raise ValueError(f"top_k must be a positive integer, got {top_k!r}.")
        if self.use_bm25 and self._bm25 is not None:
            vec_results = self._search_vector(query, k * 2, source)
            bm25_results = self._bm25.search(query, top_k=k * 2, source=source)
            return hybrid_search(
                vec_results,
                bm25_results,
                top_k=k,
                alpha=self.bm25_alpha,
            )
        return self._search_vector(query, k, source)

    def sources(self) -> List[str]:
        """List source filenames currently indexed in the notebook."""
        got = self._mgr.collection.get(include=["metadatas"])
        filenames = {
            m.get("filename")
            for m in got["metadatas"]
            if m and m.get("filename")
        }
        return sorted(filenames)


def render_results(results: List[SearchResult], max_lines: int = 10) -> str:
    """Render SearchResults as a human-readable block with citations."""
    out = []
    out.append("=" * 80)
    out.append(f"  TOP {len(results)} RETRIEVED CHUNKS")
    out.append("=" * 80)
    for idx, res in enumerate(results, start=1):
        meta = res.metadata
        elem = meta.get("element_type", "text").upper()
        out.append(
            f"\n[{idx}] Score: {res.similarity:.4f} | {res.citation} | Type: {elem}"
        )
        lines = res.content.strip().split("\n")
        display = lines[:max_lines]
        if len(lines) > max_lines:
            display.append(f"... (+ {len(lines) - max_lines} more lines)")
        for line in display:
            out.append(f"    {line}")
    out.append("=" * 80)
    return "\n".join(out)