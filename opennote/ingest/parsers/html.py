"""HTML and URL parser with section-heading citations.

Parses the document structure directly (headings + paragraphs) so chunks carry
an ``§ heading`` locator pointing back to the source section. trafilatura is
used only to fetch remote URLs.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import List, Optional, Tuple
from urllib.parse import urlparse

from opennote.ingest.chunking import (
    ChunkSpec,
    DocumentChunk,
    compute_chunk_id,
    sliding_window_chunk_with_offsets,
)
from opennote.ingest.parsers.base import SourceParser

logger = logging.getLogger("opennote.ingest.html")

_HEADINGS = ("h1", "h2", "h3", "h4", "h5", "h6")


def _extract_sections(html: str) -> List[Tuple[Optional[str], List[str]]]:
    """Return [(heading_or_None, [paragraph_text, ...]), ...] from HTML."""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    body = soup.body or soup
    sections: List[Tuple[Optional[str], List[str]]] = []
    current_heading: Optional[str] = None
    current_paras: List[str] = []

    def flush():
        nonlocal current_heading, current_paras
        if current_paras:
            sections.append((current_heading, current_paras))
        current_heading = None
        current_paras = []

    for el in body.find_all(list(_HEADINGS) + ["p", "li"]):
        text = el.get_text(" ", strip=True)
        if not text:
            continue
        if el.name in _HEADINGS:
            flush()
            current_heading = text
        else:
            current_paras.append(text)
    flush()
    return sections


def _chunk_sections(
    sections: List[Tuple[Optional[str], List[str]]],
    source: str,
    filename: str,
    spec: ChunkSpec,
) -> List[DocumentChunk]:
    chunks: List[DocumentChunk] = []
    chunk_idx = 0

    for heading, paras in sections:
        text = "\n\n".join(paras)
        if not text.strip():
            continue

        if len(text) <= spec.size:
            blocks = [text]
        else:
            blocks = [
                t for t, _, _ in sliding_window_chunk_with_offsets(
                    text, spec.size, spec.overlap
                )
            ]

        for block in blocks:
            meta = {
                "source": source,
                "filename": filename,
                "element_type": "text",
                "char_count": len(block),
            }
            if heading:
                meta["heading"] = heading
            else:
                meta["line_start"] = chunk_idx + 1
                meta["line_end"] = chunk_idx + 1
            chunk_id = compute_chunk_id(source, chunk_idx, chunk_idx, chunk_idx, block)
            chunks.append(DocumentChunk(content=block, metadata=meta, chunk_id=chunk_id))
            chunk_idx += 1

    return chunks


class HtmlParser(SourceParser):
    """Chunk a local .html / .htm file, citing by source + section heading."""

    def parse(self, file_path: Path, spec: ChunkSpec) -> List[DocumentChunk]:
        html = file_path.read_text(encoding="utf-8-sig", errors="replace")
        sections = _extract_sections(html)
        chunks = _chunk_sections(sections, str(file_path.resolve()), file_path.name, spec)
        logger.info(f"Extracted {len(chunks)} chunk(s) from '{file_path.name}'.")
        return chunks


def parse_url(url: str, spec: ChunkSpec) -> List[DocumentChunk]:
    """Fetch a URL and return cited chunks from its structure."""
    import trafilatura

    html = trafilatura.fetch_url(url)
    if not html:
        raise RuntimeError(f"Failed to fetch URL: {url}")

    host = urlparse(url).netloc or url
    path = urlparse(url).path or ""
    filename = host + path if path and path != "/" else host
    sections = _extract_sections(html)
    chunks = _chunk_sections(sections, url, filename, spec)
    logger.info(f"Extracted {len(chunks)} chunk(s) from '{url}'.")
    return chunks