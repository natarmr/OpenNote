from opennote.ingest.chunking import (
    build_citation_pages,
    compute_file_hash,
    sliding_window_chunk_with_offsets,
    split_markdown_by_headings,
)


def test_single_short_text_returns_one_chunk():
    chunks = sliding_window_chunk_with_offsets("Hello world", chunk_size=800)
    assert chunks == [("Hello world", 0, 11)]


def test_empty_text_returns_nothing():
    assert sliding_window_chunk_with_offsets("   ") == []


def test_long_text_splits_into_multiple_chunks():
    text = "word " * 1000
    chunks = sliding_window_chunk_with_offsets(text, chunk_size=200, chunk_overlap=20)
    assert len(chunks) > 1
    joined = "".join(t for t, _, _ in chunks)
    assert "word" in joined


def test_chunks_respect_boundaries_and_offsets():
    text = "First sentence. Second sentence. Third sentence. Fourth one here."
    chunks = sliding_window_chunk_with_offsets(text, chunk_size=40, chunk_overlap=8)
    assert chunks
    for _, start, end in chunks:
        assert 0 <= start < end <= len(text)


def test_split_markdown_by_headings():
    md = "# Title\n\nIntro\n\n## Section\n\nBody\n\n### Sub\n\nMore"
    sections = split_markdown_by_headings(md)
    assert sections[0].startswith("# Title")
    assert any(s.startswith("## Section") for s in sections)
    assert any(s.startswith("### Sub") for s in sections)


def test_build_citation_pages():
    assert build_citation_pages(4, 4) == "4"
    assert build_citation_pages(4, 5) == "4-5"


def test_compute_file_hash_is_stable(tmp_path):
    f = tmp_path / "a.txt"
    f.write_text("hello", encoding="utf-8")
    assert compute_file_hash(f) == compute_file_hash(f)
