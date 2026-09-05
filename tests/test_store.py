"""Regression tests for the vector store (db_directory handling, mismatch guard)."""
from __future__ import annotations

import pytest

from opennote.ingest.chunking import DocumentChunk
from opennote.store.vectors import VectorStoreManager


def _chunk(content: str, source: str = "s", page: int = 1) -> DocumentChunk:
    return DocumentChunk(
        content=content,
        metadata={
            "source": source,
            "filename": "f.pdf",
            "page_start": page,
            "page_end": page,
            "pages": str(page),
            "chunk_index": 0,
            "element_type": "text",
            "char_count": len(content),
        },
        chunk_id=f"{source}-{page}-{content[:8]}",
    )


def test_fresh_dir_creates_store(stub_embedder, tmp_path):
    store = tmp_path / "nb" / "chroma"
    mgr = VectorStoreManager("documents", store)
    assert store.exists()
    assert (store / "chroma.sqlite3").exists()


def test_reopen_existing_collection(stub_embedder, tmp_path):
    store = tmp_path / "nb" / "chroma"
    VectorStoreManager("documents", store).add_chunks([_chunk("hello world")])
    # Reopening the same store_dir must not crash (db_directory regression).
    mgr = VectorStoreManager("documents", store)
    results = mgr.search("hello")
    assert len(results) == 1
    assert results[0]["content"] == "hello world"


def test_model_mismatch_guard_raises(stub_embedder, tmp_path):
    store = tmp_path / "nb" / "chroma"
    VectorStoreManager("documents", store, model_name="model-a")
    with pytest.raises(ValueError, match="Mismatch Guard"):
        VectorStoreManager("documents", store, model_name="model-b")


def test_model_mismatch_force_resets(stub_embedder, tmp_path):
    store = tmp_path / "nb" / "chroma"
    VectorStoreManager("documents", store, model_name="model-a").add_chunks(
        [_chunk("old content")]
    )
    mgr = VectorStoreManager(
        "documents", store, model_name="model-b", force_reindex=True
    )
    assert mgr.collection.count() == 0


def test_read_only_requires_existing_collection(stub_embedder, tmp_path):
    store = tmp_path / "nb" / "chroma"
    with pytest.raises(ValueError, match="does not exist"):
        VectorStoreManager("documents", store, read_only=True)


def test_read_only_search_skips_guard(stub_embedder, tmp_path):
    store = tmp_path / "nb" / "chroma"
    VectorStoreManager("documents", store, model_name="model-a").add_chunks(
        [_chunk("searchable text")]
    )
    mgr = VectorStoreManager("documents", store, model_name="model-a", read_only=True)
    assert mgr.search("searchable")


def test_model_load_prefers_local_files_only(tmp_path, monkeypatch):
    calls = []

    class Recorder:
        def __init__(self, model_name, device=None, **kwargs):
            calls.append(kwargs)

        def encode(self, *a, **k):
            return [[0.0] * 4]

    import sentence_transformers
    from opennote.store.vectors import _MODEL_CACHE

    _MODEL_CACHE.clear()
    monkeypatch.setattr(sentence_transformers, "SentenceTransformer", Recorder)
    VectorStoreManager("documents", tmp_path / "c")
    assert calls and calls[0].get("local_files_only") is True