"""Docling-based layout-aware PDF parser."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from opennote.ingest.chunking import (
    ChunkSpec,
    DocumentChunk,
    build_citation_pages,
    compute_chunk_id,
    sliding_window_chunk_with_offsets,
    split_markdown_by_headings,
)
from opennote.ingest.parsers.base import SourceParser

logger = logging.getLogger("opennote.ingest.pdf_docling")


class DoclingParser(SourceParser):
    """Parses PDFs using Docling, retaining layout, tables, and hierarchy."""

    def __init__(self, do_ocr: bool = False):
        self.do_ocr = do_ocr
        try:
            from docling.document_converter import (
                DocumentConverter,
                PdfFormatOption,
            )
            from docling.datamodel.pipeline_options import PdfPipelineOptions
            from docling.datamodel.base_models import InputFormat

            pipeline_options = PdfPipelineOptions()
            pipeline_options.do_ocr = self.do_ocr
            pipeline_options.do_table_structure = True

            self.converter = DocumentConverter(
                format_options={
                    InputFormat.PDF: PdfFormatOption(
                        pipeline_options=pipeline_options
                    )
                }
            )
            self.available = True
        except ImportError:
            self.available = False
            logger.warning(
                "Docling is not installed (pip install docling). "
                "Falling back to pdfplumber parser."
            )

    def parse(self, file_path: Path, spec: ChunkSpec) -> List[DocumentChunk]:
        if not self.available:
            raise RuntimeError("Docling is not available in current environment.")

        logger.info(
            f"Parsing '{file_path.name}' with Docling layout parser (OCR={self.do_ocr})..."
        )
        result = self.converter.convert(file_path)
        doc = result.document

        chunks: List[DocumentChunk] = []
        chunk_idx = 0

        # Try Docling's native HybridChunker
        try:
            from docling.chunking import HybridChunker

            # Convert character budget to a token budget (approx. 4 chars/token).
            chunker = HybridChunker(max_tokens=max(1, spec.size // 4))
            doc_chunks = list(chunker.chunk(doc))

            for chunk in doc_chunks:
                text_content = chunk.text.strip()
                if not text_content:
                    continue

                # Extract accurate multi-page citation range
                pages = []
                for item in getattr(chunk, "meta", {}).get("doc_items", []):
                    if hasattr(item, "prov") and item.prov:
                        for p in item.prov:
                            if hasattr(p, "page_no"):
                                pages.append(p.page_no)

                page_start = min(pages) if pages else 1
                page_end = max(pages) if pages else 1
                page_citation = build_citation_pages(page_start, page_end)

                # Detect if table
                is_table = "| ---" in text_content or "|:---" in text_content
                elem_type = "table" if is_table else "text"

                chunk_id = compute_chunk_id(
                    str(file_path.resolve()), page_start, page_end, chunk_idx, text_content
                )
                meta = {
                    "source": str(file_path.resolve()),
                    "filename": file_path.name,
                    "page_start": int(page_start),
                    "page_end": int(page_end),
                    "pages": page_citation,
                    "chunk_index": chunk_idx,
                    "element_type": elem_type,
                    "char_count": len(text_content),
                }
                chunks.append(
                    DocumentChunk(
                        content=text_content, metadata=meta, chunk_id=chunk_id
                    )
                )
                chunk_idx += 1

            if chunks:
                return chunks
        except Exception as e:  # noqa: BLE001
            logger.debug(
                f"Docling hybrid chunker fell back to markdown heading chunking: {e}"
            )

        # Fallback: export to structured markdown and split by heading levels.
        md_content = doc.export_to_markdown()
        sections = split_markdown_by_headings(md_content)

        # Best-effort page attribution: map each heading to its page span by
        # walking docling's document items. Degrades gracefully to page 1 if the
        # item API is unavailable (version drift) or raises.
        try:
            heading_pages = heading_page_map(item for item, _ in doc.iterate_items())
        except Exception:  # noqa: BLE001
            heading_pages = {}

        for sec in sections:
            is_table = "| ---" in sec or "|:---" in sec
            elem_type = "table" if is_table else "text"
            page_start, page_end = _section_pages(sec, heading_pages)
            page_citation = build_citation_pages(page_start, page_end)

            if len(sec) <= spec.size or is_table:
                chunk_id = compute_chunk_id(
                    str(file_path.resolve()), page_start, page_end, chunk_idx, sec
                )
                chunks.append(
                    DocumentChunk(
                        content=sec,
                        metadata={
                            "source": str(file_path.resolve()),
                            "filename": file_path.name,
                            "page_start": page_start,
                            "page_end": page_end,
                            "pages": page_citation,
                            "chunk_index": chunk_idx,
                            "element_type": elem_type,
                            "char_count": len(sec),
                        },
                        chunk_id=chunk_id,
                    )
                )
                chunk_idx += 1
            else:
                sub_splits = sliding_window_chunk_with_offsets(
                    sec, spec.size, spec.overlap
                )
                for sub_text, _, _ in sub_splits:
                    chunk_id = compute_chunk_id(
                        str(file_path.resolve()), page_start, page_end, chunk_idx, sub_text
                    )
                    chunks.append(
                        DocumentChunk(
                            content=sub_text,
                            metadata={
                                "source": str(file_path.resolve()),
                                "filename": file_path.name,
                                "page_start": page_start,
                                "page_end": page_end,
                                "pages": page_citation,
                                "chunk_index": chunk_idx,
                                "element_type": "text",
                                "char_count": len(sub_text),
                            },
                            chunk_id=chunk_id,
                        )
                    )
                    chunk_idx += 1

        return chunks


def heading_page_map(items) -> Dict[str, Tuple[int, int]]:
    """Map heading text -> (page_start, page_end) for a stream of document items.

    ``items`` is an iterable of objects exposing ``.text``, ``.label`` and
    ``.prov`` (a list of objects with ``.page_no``). A heading item starts a new
    section; subsequent body items accumulate page numbers into the current
    heading's span until the next heading.

    This is a pure helper so it can be unit-tested without invoking Docling.
    """
    headings: Dict[str, Tuple[int, int]] = {}
    current_heading: Optional[str] = None
    current_pages: set = set()

    def flush():
        if current_heading is not None and current_pages:
            headings[current_heading] = (min(current_pages), max(current_pages))

    for item in items:
        label_s = str(getattr(item, "label", ""))
        is_heading = (
            "heading" in label_s or label_s == "title" or "subtitle" in label_s
        )
        pages = {
            p.page_no
            for p in getattr(item, "prov", [])
            if hasattr(p, "page_no")
        }
        if is_heading:
            flush()
            current_heading = (getattr(item, "text", "") or "").strip()
            current_pages = set(pages)
        else:
            current_pages |= pages
    flush()
    return headings


def _section_heading(section: str) -> Optional[str]:
    """Return the normalized heading text of a markdown section, if any."""
    for line in section.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            return stripped.lstrip("#").strip()
    return None


def _section_pages(section: str, heading_pages) -> Tuple[int, int]:
    """Resolve a section's (page_start, page_end), defaulting to (1, 1)."""
    heading = _section_heading(section)
    if heading and heading in heading_pages:
        return heading_pages[heading]
    return 1, 1
