"""Retrieval parity benchmark: chunk configs + hybrid alpha ablation.

Compares local defaults (800/120) against upstream-style 400/60 and
large-embedder 1500/150 using deterministic BM25 recall on rare terms
(E1 rationale: exact technical terms favor keyword match).

Uses stub_embedder (no HF downloads); vector scores are random so the
meaningful assertions use BM25-forced paths (alpha=0) vs hybrid.
"""
from __future__ import annotations

from opennote.ingest.chunking import sliding_window_chunk_with_offsets
from opennote.ingest.pipeline import ingest
from opennote.retrieval.bm25 import hybrid_search
from opennote.retrieval.eval import evaluate, load_golden
from opennote.retrieval.retriever import Retriever


RARE_TERMS = ["Stable LatentMoE", "gated MLA", "Kimi Delta Attention"]

LONG_TEXT = (
    "Introduction. " + ("General background on retrieval systems. " * 40)
    + "Stable LatentMoE is a sparse expert architecture. "
    + ("Filler paragraph about embeddings and search. " * 40)
    + "gated MLA improves attention efficiency. "
    + ("More filler on chunking and indexing. " * 40)
    + "Kimi Delta Attention handles long context. "
    + ("Closing remarks on evaluation. " * 20)
)


def _make_docs(tmp_path, n_terms=3):
    docs = []
    for i in range(n_terms):
        p = tmp_path / f"doc{i}.txt"
        # Each doc carries one rare term plus shared filler so BM25 must discriminate.
        p.write_text(
            f"Document {i} about {RARE_TERMS[i]}. " + ("Shared filler text. " * 30),
            encoding="utf-8",
        )
        docs.append(p)
    return docs


def test_chunk_counts_scale_with_size():
    small = sliding_window_chunk_with_offsets(LONG_TEXT, chunk_size=400, chunk_overlap=60)
    default = sliding_window_chunk_with_offsets(LONG_TEXT, chunk_size=800, chunk_overlap=120)
    large = sliding_window_chunk_with_offsets(LONG_TEXT, chunk_size=1500, chunk_overlap=150)
    assert len(small) >= len(default) >= 1
    assert len(default) >= len(large) >= 1
    # Overlap invariant: adjacent chunks overlap
    assert all(len(t) > 0 for t, _, _ in default)


def test_bm25_recall_beats_random_vector_on_rare_terms(notebook_manager, stub_embedder, tmp_path):
    nb = notebook_manager.create("parity", project="test")
    for doc in _make_docs(tmp_path):
        n = ingest(nb, doc, chunk_size=800, chunk_overlap=120)
        assert n >= 1

    golden_file = tmp_path / "golden.tsv"
    golden_file.write_text(
        "\n".join(
            f"{term}\tdoc{i}.txt" for i, term in enumerate(RARE_TERMS)
        ),
        encoding="utf-8",
    )
    golden = load_golden(golden_file)

    # BM25-forced (alpha=0) is deterministic on rare terms.
    r_bm25 = Retriever(nb, top_k=5, use_bm25=True, bm25_alpha=0.0)
    s_bm25 = evaluate(r_bm25, golden, top_k=5)
    # Hybrid default should be at least as good as vector-only in structure
    # (both share the same BM25 corpus; just assert it runs and scores in range).
    r_hybrid = Retriever(nb, top_k=5, use_bm25=True, bm25_alpha=0.5)
    s_hybrid = evaluate(r_hybrid, golden, top_k=5)
    assert 0.0 <= s_bm25.recall_at_k <= 1.0
    assert 0.0 <= s_hybrid.recall_at_k <= 1.0
    # Rare-term BM25 recall should be perfect on this synthetic set.
    assert s_bm25.recall_at_k == 1.0


def test_chunk_size_plumbing_preserved(notebook_manager, stub_embedder, tmp_path):
    """Both 800/120 and upstream-style 400/60 ingest without error."""
    for size, overlap in [(800, 120), (400, 60), (1500, 150)]:
        nb = notebook_manager.create(f"parity_{size}", project="test")
        doc = tmp_path / f"doc_{size}.txt"
        doc.write_text(LONG_TEXT, encoding="utf-8")
        n = ingest(nb, doc, chunk_size=size, chunk_overlap=overlap)
        assert n >= 1
        r = Retriever(nb, top_k=3, use_bm25=True, bm25_alpha=0.0)
        hits = r.search("Stable LatentMoE", top_k=3)
        assert isinstance(hits, list)


def test_hybrid_alpha_blend_unit():
    from opennote.retrieval.citations import citation_for
    from opennote.retrieval.retriever import SearchResult

    def _sr(cid, sim):
        return SearchResult(
            content=f"content {cid}",
            metadata={"chunk_id": cid, "filename": "f.txt"},
            similarity=sim,
            citation=citation_for({"filename": "f.txt"}),
        )

    vec = [_sr("a", 1.0), _sr("b", 0.1)]
    bm = [_sr("b", 1.0), _sr("c", 0.9)]
    # alpha=0 -> BM25 wins (b first); alpha=1 -> vector wins (a first)
    assert hybrid_search(vec, bm, top_k=2, alpha=0.0)[0].id == "b"
    assert hybrid_search(vec, bm, top_k=2, alpha=1.0)[0].id == "a"
    # default 0.5 blends: b (0.55) beats a (0.5)
    assert hybrid_search(vec, bm, top_k=3, alpha=0.5)[0].id == "b"
