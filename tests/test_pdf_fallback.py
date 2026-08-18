"""Regression tests for the fallback PDF parser (page-span citations, tables)."""
from __future__ import annotations

from opennote.ingest.chunking import ChunkSpec
from opennote.ingest.parsers.pdf_fallback import FallbackPDFParser
from fixtures import make_long_pdf, make_table_pdf


def test_long_pdf_crosses_pages_with_multi_page_citations(tmp_path):
    pdf = make_long_pdf(tmp_path / "long.pdf", sentences=500)
    parser = FallbackPDFParser()
    chunks = parser.parse(pdf, ChunkSpec(size=800, overlap=120))

    assert chunks
    page_starts = {c.metadata["page_start"] for c in chunks}
    assert max(page_starts) > 1, "expected content across multiple pages"

    multi_page = [
        c
        for c in chunks
        if c.metadata["page_start"] < c.metadata["page_end"]
    ]
    assert multi_page, "expected at least one chunk spanning multiple pages"

    for c in chunks:
        assert 1 <= c.metadata["page_start"] <= c.metadata["page_end"]


def test_table_is_extracted_as_table_chunk(tmp_path):
    pdf = make_table_pdf(tmp_path / "table.pdf")
    parser = FallbackPDFParser()
    chunks = parser.parse(pdf, ChunkSpec())

    tables = [c for c in chunks if c.metadata["element_type"] == "table"]
    assert tables, "expected a table chunk"
    for t in tables:
        assert "|" in t.content
        assert t.metadata["page_start"] == 1


def test_pypdf_fallback_when_pdfplumber_unavailable(tmp_path):
    pdf = make_long_pdf(tmp_path / "pypdf.pdf", sentences=100)
    parser = FallbackPDFParser()
    parser.available = False
    chunks = parser.parse(pdf, ChunkSpec(size=400, overlap=60))
    assert chunks
    for c in chunks:
        assert 1 <= c.metadata["page_start"] <= c.metadata["page_end"]