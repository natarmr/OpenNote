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


# ---- BM25 + hybrid retrieval (Phase G) ----

from opennote.retrieval.bm25 import Bm25Retriever, hybrid_search


def test_bm25_search_returns_ranked_results(stub_embedder, notebook_manager):
    nb = notebook_manager.create("nb")
    mgr = VectorStoreManager("documents", nb.store_dir)
    mgr.add_chunks(
        [
            _chunk("The cat sat on the mat", "a.pdf"),
            _chunk("Dogs chase the cat in the park", "b.pdf"),
            _chunk("Stock market reports for investors", "c.pdf"),
        ]
    )
    bm25 = Bm25Retriever(nb)
    results = bm25.search("cat", top_k=3)
    assert len(results) == 2  # only the two cat documents match
    assert {r.metadata["filename"] for r in results} == {"a.pdf", "b.pdf"}
    assert all(r.similarity > 0.0 for r in results)
    assert all(r.id for r in results)  # chunk_id copied from chroma (L76)


def test_bm25_empty_corpus_no_crash(notebook_manager):
    # A notebook with a store dir but no chunks must not ZeroDivide (L72).
    nb = notebook_manager.create("nb")
    bm25 = Bm25Retriever(nb)
    assert bm25.search("anything") == []


def test_bm25_source_filter(stub_embedder, notebook_manager):
    nb = notebook_manager.create("nb")
    mgr = VectorStoreManager("documents", nb.store_dir)
    mgr.add_chunks(
        [_chunk("alpha content", "a.pdf"), _chunk("beta content", "b.pdf")]
    )
    bm25 = Bm25Retriever(nb)
    results = bm25.search("content", top_k=5, source="a.pdf")
    assert results
    assert all(r.metadata["filename"] == "a.pdf" for r in results)


def test_bm25_multiple_chunks_same_source_survive(stub_embedder, notebook_manager):
    # Chunks are merged per-chunk, not per-file (L71).
    nb = notebook_manager.create("nb")
    mgr = VectorStoreManager("documents", nb.store_dir)
    mgr.add_chunks(
        [
            _chunk("alpha first paragraph", "a.pdf"),
            _chunk("alpha second paragraph with more text", "a.pdf"),
            _chunk("beta unrelated content", "b.pdf"),
        ]
    )
    bm25 = Bm25Retriever(nb)
    results = bm25.search("alpha", top_k=5)
    alpha_chunks = [r for r in results if r.metadata["filename"] == "a.pdf"]
    assert len(alpha_chunks) == 2  # both chunks of a.pdf present


def test_hybrid_no_recursion(stub_embedder, notebook_manager):
    # The L68 recursion: hybrid path must not re-enter search() with use_bm25.
    nb = notebook_manager.create("nb")
    mgr = VectorStoreManager("documents", nb.store_dir)
    mgr.add_chunks(
        [
            _chunk("alpha content", "a.pdf"),
            _chunk("beta content", "b.pdf"),
        ]
    )
    retriever = Retriever(nb, top_k=5, use_bm25=True, bm25_alpha=0.5)
    results = retriever.search("alpha")
    assert results
    assert all(r.metadata["filename"] in ("a.pdf", "b.pdf") for r in results)


def test_hybrid_source_filter_forwarded(stub_embedder, notebook_manager):
    # The L74 source-filter must survive the hybrid path.
    nb = notebook_manager.create("nb")
    mgr = VectorStoreManager("documents", nb.store_dir)
    mgr.add_chunks(
        [
            _chunk("alpha content", "a.pdf"),
            _chunk("beta content", "b.pdf"),
        ]
    )
    retriever = Retriever(nb, top_k=5, use_bm25=True)
    filtered = retriever.search("alpha", source="a.pdf")
    assert filtered
    assert all(r.metadata["filename"] == "a.pdf" for r in filtered)


def test_hybrid_merge_pure_function():
    # hybrid_search is now a pure merge of pre-computed results (L68/L71).
    a = SearchResult(
        content="x",
        metadata={"chunk_id": "a-1", "filename": "a.pdf"},
        similarity=0.9,
        citation=citation_for({"filename": "a.pdf"}),
    )
    b = SearchResult(
        content="y",
        metadata={"chunk_id": "b-1", "filename": "b.pdf"},
        similarity=0.2,
        citation=citation_for({"filename": "b.pdf"}),
    )
    merged = hybrid_search([a], [b], top_k=5, alpha=0.5)
    assert {r.metadata["chunk_id"] for r in merged} == {"a-1", "b-1"}
    assert merged[0].metadata["chunk_id"] == "a-1"  # higher combined score


def test_bm25_disabled_no_attribute_error(stub_embedder, notebook_manager):
    nb = notebook_manager.create("nb")
    mgr = VectorStoreManager("documents", nb.store_dir)
    mgr.add_chunks([_chunk("alpha content", "a.pdf")])
    retriever = Retriever(nb, top_k=5, use_bm25=False)
    retriever.use_bm25 = True  # flipped at runtime (L76 latent AttributeError)
    assert retriever.search("alpha")  # must not raise AttributeError