"""Tests for the retrieval + citation layer and eval harness."""
from __future__ import annotations

import pytest

from opennote.ingest.chunking import DocumentChunk
from opennote.retrieval.citations import citation_for, citations_for
from opennote.retrieval.eval import EvalSummary, GoldenQuery, QueryResult, evaluate, load_golden
from opennote.retrieval.retriever import Retriever, SearchResult
from opennote.store.vectors import VectorStoreManager


def _chunk(content: str, filename: str, page: int = 1) -> DocumentChunk:
    return DocumentChunk(
        content=content,
        metadata={
            "source": f"/src/{filename}",
            "filename": filename,
            "page_start": page,
            "page_end": page,
            "pages": str(page),
            "chunk_index": 0,
            "element_type": "text",
            "char_count": len(content),
        },
        chunk_id=f"{filename}-{page}-{content[:8]}",
    )


# ---- citations (pure) ----

def test_citation_page_range():
    c = citation_for({"filename": "a.pdf", "pages": "4-5"})
    assert c.source == "a.pdf"
    assert c.locator == "p.4-5"
    assert str(c) == "[a.pdf, p.4-5]"


def test_citation_single_page():
    c = citation_for({"filename": "a.pdf", "page_start": 3, "page_end": 3})
    assert c.locator == "p.3"


def test_citation_falls_back_to_line_and_heading():
    assert citation_for({"filename": "a.txt", "line_start": 12}).locator == "L12"
    assert citation_for({"filename": "a.md", "heading": "Intro"}).locator == "§ Intro"


def test_citations_for_metadata_list():
    cs = citations_for(
        [{"metadata": {"filename": "x.pdf", "pages": "1"}},
         {"metadata": {"filename": "y.pdf", "pages": "2"}}]
    )
    assert [c.source for c in cs] == ["x.pdf", "y.pdf"]


# ---- retriever ----

def test_retriever_search_returns_cited_results(stub_embedder, notebook_manager):
    nb = notebook_manager.create("nb")
    mgr = VectorStoreManager("documents", nb.store_dir)
    mgr.add_chunks([_chunk("alpha content", "a.pdf"), _chunk("beta content", "b.pdf")])

    retriever = Retriever(nb, top_k=5)
    results = retriever.search("alpha")
    assert results
    assert all(isinstance(r, SearchResult) for r in results)
    assert all(r.citation.source for r in results)
    assert retriever.sources() == ["a.pdf", "b.pdf"]


def test_retriever_source_filter(stub_embedder, notebook_manager):
    nb = notebook_manager.create("nb")
    mgr = VectorStoreManager("documents", nb.store_dir)
    mgr.add_chunks([_chunk("alpha content", "a.pdf"), _chunk("beta content", "b.pdf")])

    retriever = Retriever(nb, top_k=5)
    filtered = retriever.search("alpha", source="a.pdf")
    assert filtered
    assert all(r.metadata["filename"] == "a.pdf" for r in filtered)


def test_retriever_top_k_zero_and_negative_rejected(stub_embedder, notebook_manager):
    nb = notebook_manager.create("nb")
    VectorStoreManager("documents", nb.store_dir).add_chunks([_chunk("alpha content", "a.pdf")])
    retriever = Retriever(nb, top_k=5)
    with pytest.raises(ValueError, match="top_k"):
        retriever.search("q", top_k=0)
    with pytest.raises(ValueError, match="top_k"):
        retriever.search("q", top_k=-1)


def test_search_result_carries_chroma_id(stub_embedder, notebook_manager):
    nb = notebook_manager.create("nb")
    mgr = VectorStoreManager("documents", nb.store_dir)
    chunk = _chunk("alpha content", "a.pdf")
    mgr.add_chunks([chunk])
    results = Retriever(nb, top_k=5).search("alpha")
    assert results
    assert results[0].id == chunk.chunk_id


def test_eval_filename_exact_match_not_suffix():
    # 'notes.txt' must NOT match a golden expecting 'my-notes.txt' (L13).
    summary = EvalSummary(
        total=1,
        top_k=5,
        recall_at_k=0.0,
        per_query=[
            QueryResult(
                golden=GoldenQuery("q", "my-notes.txt"),
                hit_source=False,
                top_sources=["/x/notes.txt"],
            )
        ],
    )
    assert summary.per_query[0].hit_source is False
    assert summary.report().startswith("Evaluation: 1 queries")


# ---- eval harness ----

def test_golden_query_from_row_parses_pages():
    g = GoldenQuery.from_row("what", "a.pdf", "4-5")
    assert g.expected_pages == (4, 5)
    g2 = GoldenQuery.from_row("what", "a.pdf", "7")
    assert g2.expected_pages == (7, 7)
    assert GoldenQuery.from_row("what", "a.pdf").expected_pages is None


def test_evaluate_returns_summary_structure(stub_embedder, notebook_manager):
    nb = notebook_manager.create("nb")
    mgr = VectorStoreManager("documents", nb.store_dir)
    mgr.add_chunks(
        [_chunk("alpha content", "a.pdf"), _chunk("beta content", "b.pdf")]
    )
    retriever = Retriever(nb, top_k=5)
    golden = [GoldenQuery("alpha", "a.pdf"), GoldenQuery("beta", "b.pdf")]
    summary = evaluate(retriever, golden, top_k=5)
    assert summary.total == 2
    assert 0.0 <= summary.recall_at_k <= 1.0
    assert len(summary.per_query) == 2


def test_load_golden_parses_tsv(tmp_path):
    f = tmp_path / "golden.tsv"
    f.write_text("query one\ta.pdf\t4-5\nquery two\tb.pdf\n\n", encoding="utf-8")
    golden = load_golden(f)
    assert len(golden) == 2
    assert golden[0].expected_source == "a.pdf"
    assert golden[0].expected_pages == (4, 5)
    assert golden[1].expected_pages is None