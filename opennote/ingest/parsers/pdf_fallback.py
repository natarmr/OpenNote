"""Fallback PDF parser using pdfplumber, with pypdf as a last resort."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import List, Optional, Tuple

from opennote.ingest.chunking import (
    ChunkSpec,
    DocumentChunk,
    build_citation_pages,
    compute_chunk_id,
    sliding_window_chunk_with_offsets,
)
from opennote.ingest.parsers.base import SourceParser

logger = logging.getLogger("opennote.ingest.pdf_fallback")


class FallbackPDFParser(SourceParser):
    """
    Robust fallback parser using pdfplumber and pypdf.
    - Preserves tables as clean Markdown tables.
    - Preserves continuous text across page boundaries without slicing sentences.
    - Accurate multi-page citation tracking.
    """

    def __init__(self):
        try:
            import pdfplumber

            self.pdfplumber = pdfplumber
            self.available = True
        except ImportError:
            self.pdfplumber = None
            self.available = False

    def parse(self, file_path: Path, spec: ChunkSpec) -> List[DocumentChunk]:
        if not self.available:
            return self._parse_with_pypdf(file_path, spec)

        logger.info(
            f"Parsing '{file_path.name}' with pdfplumber "
            "(cross-page continuity & table extraction)..."
        )
        chunks: List[DocumentChunk] = []
        chunk_idx = 0

        page_text_spans: List[Tuple[str, int]] = []
        total_images_found = 0

        with self.pdfplumber.open(file_path) as pdf:
            for page_idx, page in enumerate(pdf.pages, start=1):
                # 1. Tables as dedicated table chunks
                tables = page.extract_tables()
                for t_idx, table in enumerate(tables):
                    if not table or not any(table):
                        continue
                    md_table = self._format_as_markdown_table(table)
                    if md_table.strip():
                        chunk_id = compute_chunk_id(
                            str(file_path.resolve()), page_idx, page_idx, chunk_idx, md_table
                        )
                        chunks.append(
                            DocumentChunk(
                                content=md_table.strip(),
                                metadata={
                                    "source": str(file_path.resolve()),
                                    "filename": file_path.name,
                                    "page_start": page_idx,
                                    "page_end": page_idx,
                                    "pages": f"{page_idx}",
                                    "chunk_index": chunk_idx,
                                    "element_type": "table",
                                    "table_index": t_idx + 1,
                                    "char_count": len(md_table),
                                },
                                chunk_id=chunk_id,
                            )
                        )
                        chunk_idx += 1

                # 2. Regular text with page markers for a continuous stream
                raw_text = page.extract_text() or ""
                cleaned_text = raw_text.strip()
                if cleaned_text:
                    page_text_spans.append((cleaned_text, page_idx))

                # 3. Track images / figures
                images = getattr(page, "images", [])
                if images:
                    total_images_found += len(images)

        # Build continuous document text with character-to-page offset mapping
        continuous_text = ""
        offset_to_page: List[Tuple[int, int, int]] = []

        for text_snippet, page_no in page_text_spans:
            if continuous_text:
                continuous_text += "\n\n"
            start_pos = len(continuous_text)
            continuous_text += text_snippet
            end_pos = len(continuous_text)
            offset_to_page.append((start_pos, end_pos, page_no))

        # Chunk continuous text across page boundaries
        if continuous_text:
            text_splits = sliding_window_chunk_with_offsets(
                continuous_text, spec.size, spec.overlap
            )
            for split_text, start_offset, end_offset in text_splits:
                chunk_pages = [
                    page_no
                    for p_start, p_end, page_no in offset_to_page
                    if not (end_offset <= p_start or start_offset >= p_end)
                ]
                page_start = min(chunk_pages) if chunk_pages else 1
                page_end = max(chunk_pages) if chunk_pages else 1
                page_citation = build_citation_pages(page_start, page_end)

                chunk_id = compute_chunk_id(
                    str(file_path.resolve()), page_start, page_end, chunk_idx, split_text
                )
                chunks.append(
                    DocumentChunk(
                        content=split_text,
                        metadata={
                            "source": str(file_path.resolve()),
                            "filename": file_path.name,
                            "page_start": page_start,
                            "page_end": page_end,
                            "pages": page_citation,
                            "chunk_index": chunk_idx,
                            "element_type": "text",
                            "char_count": len(split_text),
                        },
                        chunk_id=chunk_id,
                    )
                )
                chunk_idx += 1

        return chunks

    def _parse_with_pypdf(
        self, file_path: Path, spec: ChunkSpec
    ) -> List[DocumentChunk]:
        from pypdf import PdfReader

        logger.info(
            f"Parsing '{file_path.name}' with standard pypdf "
            "(continuous cross-page stream)..."
        )
        reader = PdfReader(str(file_path))
        chunks: List[DocumentChunk] = []
        chunk_idx = 0

        page_text_spans: List[Tuple[str, int]] = []
        for page_idx, page in enumerate(reader.pages, start=1):
            text = (page.extract_text() or "").strip()
            if text:
                page_text_spans.append((text, page_idx))

        continuous_text = ""
        offset_to_page: List[Tuple[int, int, int]] = []
        for text_snippet, page_no in page_text_spans:
            if continuous_text:
                continuous_text += "\n\n"
            start_pos = len(continuous_text)
            continuous_text += text_snippet
            end_pos = len(continuous_text)
            offset_to_page.append((start_pos, end_pos, page_no))

        if continuous_text:
            text_splits = sliding_window_chunk_with_offsets(
                continuous_text, spec.size, spec.overlap
            )
            for split_text, start_offset, end_offset in text_splits:
                chunk_pages = [
                    page_no
                    for p_start, p_end, page_no in offset_to_page
                    if not (end_offset <= p_start or start_offset >= p_end)
                ]
                page_start = min(chunk_pages) if chunk_pages else 1
                page_end = max(chunk_pages) if chunk_pages else 1
                page_citation = build_citation_pages(page_start, page_end)

                chunk_id = compute_chunk_id(
                    str(file_path.resolve()), page_start, page_end, chunk_idx, split_text
                )
                chunks.append(
                    DocumentChunk(
                        content=split_text,
                        metadata={
                            "source": str(file_path.resolve()),
                            "filename": file_path.name,
                            "page_start": page_start,
                            "page_end": page_end,
                            "pages": page_citation,
                            "chunk_index": chunk_idx,
                            "element_type": "text",
                            "char_count": len(split_text),
                        },
                        chunk_id=chunk_id,
                    )
                )
                chunk_idx += 1

        return chunks

    @staticmethod
    def _format_as_markdown_table(rows: List[List[Optional[str]]]) -> str:
        """Format a 2D raw array to a clean Markdown table."""
        if not rows:
            return ""
        cleaned_rows = [
            [(cell.replace("\n", " ").strip() if cell else "") for cell in row]
            for row in rows
        ]
        cleaned_rows = [row for row in cleaned_rows if any(row)]
        if not cleaned_rows:
            return ""

        headers = cleaned_rows[0]
        num_cols = max(len(r) for r in cleaned_rows)
        headers = headers + [""] * (num_cols - len(headers))

        md_lines = []
        md_lines.append("| " + " | ".join(headers) + " |")
        md_lines.append("| " + " | ".join(["---"] * num_cols) + " |")

        for row in cleaned_rows[1:]:
            padded_row = row + [""] * (num_cols - len(row))
            md_lines.append("| " + " | ".join(padded_row) + " |")

        return "\n".join(md_lines)
