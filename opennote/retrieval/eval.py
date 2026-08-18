"""Retrieval evaluation: measure recall@k against a golden set.

A golden set is a list of (query, expected_source[, expected_pages]) pairs.
For each query we retrieve and check whether the expected source appears in the
top-k results. This gives a regression gate for retrieval quality: parser,
chunker, or embedding changes must not silently degrade grounded retrieval.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

from opennote.retrieval.retriever import Retriever


@dataclass
class GoldenQuery:
    query: str
    expected_source: str
    expected_pages: Optional[Tuple[int, int]] = None

    @classmethod
    def from_row(cls, query: str, source: str, pages: Optional[str] = None) -> "GoldenQuery":
        span = None
        if pages:
            parts = pages.split("-")
            try:
                span = (int(parts[0]), int(parts[1]) if len(parts) > 1 else int(parts[0]))
            except ValueError:
                span = None
        return cls(query=query, expected_source=source, expected_pages=span)


@dataclass
class QueryResult:
    golden: GoldenQuery
    hit_source: bool
    top_sources: List[str]


@dataclass
class EvalSummary:
    total: int
    top_k: int
    recall_at_k: float
    per_query: List[QueryResult]

    def report(self) -> str:
        lines = [
            f"Evaluation: {self.total} queries",
            f"Recall@{self.top_k}: {self.recall_at_k:.2f}",
        ]
        return "\n".join(lines)


def evaluate(
    retriever: Retriever,
    golden: List[GoldenQuery],
    top_k: Optional[int] = None,
) -> EvalSummary:
    """Run retrieval over the golden set and compute recall@k."""
    k = top_k or retriever.top_k
    per_query: List[QueryResult] = []
    hits = 0

    for g in golden:
        results = retriever.search(g.query, top_k=k)
        top_sources = [r.metadata.get("filename", "") for r in results]
        hit = any(Path(s).name == g.expected_source for s in top_sources)
        if hit:
            hits += 1
        per_query.append(QueryResult(golden=g, hit_source=hit, top_sources=top_sources))

    return EvalSummary(
        total=len(golden),
        top_k=k,
        recall_at_k=(hits / len(golden)) if golden else 0.0,
        per_query=per_query,
    )


def load_golden(path) -> List[GoldenQuery]:
    """Load a golden set from a TSV/CSV: query, source, optional pages."""
    import csv

    golden = []
    with open(path, "r", encoding="utf-8", newline="") as f:
        for row in csv.reader(f, delimiter="\t"):
            if not row or not row[0].strip():
                continue
            query = row[0].strip()
            source = row[1].strip() if len(row) > 1 else ""
            pages = row[2].strip() if len(row) > 2 else None
            golden.append(GoldenQuery.from_row(query, source, pages))
    return golden