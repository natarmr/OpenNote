"""Regression tests for the ingest pipeline (caching, force, reindex, manifest)."""
from __future__ import annotations

import pytest

from opennote.ingest.pipeline import ingest
from opennote.store.vectors import VectorStoreManager
from fixtures import make_long_pdf, make_text_pdf


def test_ingest_indexes_then_skips_unchanged(stub_embedder, notebook_manager, tmp_path):
    pdf = make_long_pdf(tmp_path / "long.pdf")
    nb = notebook_manager.create("nb")

    first = ingest(nb, pdf, parser="fallback")
    assert first > 0

    second = ingest(nb, pdf, parser="fallback")
    assert second == 0, "unchanged content should be skipped via cache"


def test_force_reindexes_unchanged(stub_embedder, notebook_manager, tmp_path):
    pdf = make_long_pdf(tmp_path / "long.pdf")
    nb = notebook_manager.create("nb")
    ingest(nb, pdf, parser="fallback")

    forced = ingest(nb, pdf, parser="fallback", force=True)
    assert forced > 0


def test_reindex_replaces_old_chunks(stub_embedder, notebook_manager, tmp_path):
    pdf = make_text_pdf(tmp_path / "doc.pdf", "Version one unique marker content.")
    nb = notebook_manager.create("nb")
    ingest(nb, pdf, parser="fallback")

    old_ids = set(VectorStoreManager("documents", nb.store_dir).collection.get()["ids"])

    make_text_pdf(pdf, "Version two entirely different unique marker text.")
    ingest(nb, pdf, parser="fallback")

    new_ids = set(VectorStoreManager("documents", nb.store_dir).collection.get()["ids"])
    assert old_ids, "expected old chunks to exist"
    assert old_ids.isdisjoint(new_ids), "old chunks should be deleted on reindex"


def test_source_not_marked_indexed_when_embedding_fails(
    stub_embedder, notebook_manager, tmp_path, monkeypatch
):
    pdf = make_long_pdf(tmp_path / "long.pdf")
    nb = notebook_manager.create("nb")

    def boom(self, chunks, batch_size=64):
        raise RuntimeError("embedding failed")

    monkeypatch.setattr(VectorStoreManager, "add_chunks", boom)
    with pytest.raises(ValueError, match="No chunks indexed"):
        ingest(nb, pdf, parser="fallback")

    mgr = VectorStoreManager("documents", nb.store_dir)
    assert mgr.manifest.data == {}, "source must not be marked indexed on failure"


def test_recorded_source_in_notebook(stub_embedder, notebook_manager, tmp_path):
    pdf = make_long_pdf(tmp_path / "long.pdf")
    nb = notebook_manager.create("nb")
    ingest(nb, pdf, parser="fallback")
    assert len(nb.sources) == 1
    assert nb.sources[0].endswith("long.pdf")


def test_ingest_txt_multiformat(stub_embedder, notebook_manager, tmp_path):
    p = tmp_path / "notes.txt"
    p.write_text("OpenNote is a grounded question answering tool.\n", encoding="utf-8")
    nb = notebook_manager.create("nb")
    count = ingest(nb, p)
    assert count > 0
    mgr = VectorStoreManager("documents", nb.store_dir)
    assert mgr.manifest.data, "txt source should be marked indexed"


def test_ingest_directory_multiformat(stub_embedder, notebook_manager, tmp_path):
    (tmp_path / "a.txt").write_text("Alpha content file.\n", encoding="utf-8")
    (tmp_path / "b.txt").write_text("Beta content file.\n", encoding="utf-8")
    nb = notebook_manager.create("nb")
    count = ingest(nb, tmp_path)
    assert count > 0
    assert len(nb.sources) == 2


def test_empty_file_reingest_removes_stale_chunks(stub_embedder, notebook_manager, tmp_path):
    f = tmp_path / "doc.txt"
    f.write_text("Version one unique marker content.", encoding="utf-8")
    nb = notebook_manager.create("nb")
    ingest(nb, f)
    old_ids = set(VectorStoreManager("documents", nb.store_dir).collection.get()["ids"])
    assert old_ids

    f.write_text("", encoding="utf-8")  # file becomes empty
    ingest(nb, f)
    new_ids = set(VectorStoreManager("documents", nb.store_dir).collection.get()["ids"])
    assert old_ids.isdisjoint(new_ids), "stale chunks must be dropped on empty re-ingest"
    assert nb.sources == [], "empty source must be removed from notebook metadata"


def test_empty_file_marked_indexed_once(stub_embedder, notebook_manager, tmp_path, monkeypatch):
    calls = []

    class CountingParser:
        def parse(self, path, spec):
            calls.append(path)
            return []

    import opennote.ingest.pipeline as pipeline

    monkeypatch.setattr(pipeline, "get_parser_for_file", lambda p, s="auto", o=False: CountingParser())
    f = tmp_path / "empty.txt"
    f.write_text("", encoding="utf-8")
    nb = notebook_manager.create("nb")
    ingest(nb, f)
    ingest(nb, f)  # second run: hash marked, must not re-parse
    assert len(calls) == 1


def test_invalid_chunk_params_rejected(stub_embedder, notebook_manager, tmp_path):
    f = tmp_path / "x.txt"
    f.write_text("content", encoding="utf-8")
    nb = notebook_manager.create("nb")
    with pytest.raises(ValueError, match="chunk_size"):
        ingest(nb, f, chunk_size=0)
    with pytest.raises(ValueError, match="overlap"):
        ingest(nb, f, chunk_size=10, chunk_overlap=10)
    with pytest.raises(ValueError, match="batch_size"):
        ingest(nb, f, batch_size=0)


def test_mixed_case_extensions_scanned(stub_embedder, notebook_manager, tmp_path):
    (tmp_path / "Notes.PDF").write_bytes(b"%PDF-1.4 fake")
    (tmp_path / "read.TXT").write_text("Mixed case.", encoding="utf-8")
    from opennote.ingest.pipeline import find_source_files

    files = find_source_files(tmp_path)
    assert {f.name for f in files} == {"Notes.PDF", "read.TXT"}


def test_all_files_fail_raises_not_silent_zero(stub_embedder, notebook_manager, tmp_path, monkeypatch):
    import opennote.ingest.pipeline as pipeline

    class BoomParser:
        def parse(self, path, spec):
            raise RuntimeError("parser down")

    monkeypatch.setattr(pipeline, "get_parser_for_file", lambda *a, **kw: BoomParser())
    f = tmp_path / "bad.txt"
    f.write_text("content", encoding="utf-8")
    nb = notebook_manager.create("nb")
    with pytest.raises(ValueError, match="No chunks indexed"):
        ingest(nb, f)


def test_ingest_url_failure_raises(stub_embedder, notebook_manager, tmp_path, monkeypatch):
    import opennote.ingest.pipeline as pipeline

    def boom_url(url, spec):
        raise RuntimeError("net down")

    monkeypatch.setattr(pipeline, "parse_url", boom_url)
    nb = notebook_manager.create("nb")
    with pytest.raises(ValueError, match="Failed to ingest URL"):
        ingest(nb, "https://example.com/page")


def test_remove_source_propagates_backend_failure(stub_embedder, notebook_manager, tmp_path, monkeypatch):
    import opennote.ingest.pipeline as pipeline
    from opennote.ingest.pipeline import remove_source

    nb = notebook_manager.create("nb")
    nb.sources.append("/tmp/a.txt")
    nb.save()

    class BoomVM:
        def __init__(self, *a, **kw):
            raise RuntimeError("store down")

    monkeypatch.setattr(pipeline, "VectorStoreManager", BoomVM)
    with pytest.raises(RuntimeError, match="store down"):
        remove_source(nb, "/tmp/a.txt")
    assert "/tmp/a.txt" in nb.sources, "sources list must be untouched on backend failure"


def test_remove_source_clears_vectors_and_list(stub_embedder, notebook_manager, tmp_path):
    from opennote.ingest.pipeline import ingest, remove_source

    f = tmp_path / "doc.txt"
    f.write_text("removable content marker", encoding="utf-8")
    nb = notebook_manager.create("nb")
    assert ingest(nb, f) > 0
    assert len(nb.sources) == 1
    remove_source(nb, nb.sources[0])
    assert nb.sources == []
    mgr = VectorStoreManager("documents", nb.store_dir)
    assert mgr.collection.get()["ids"] == []