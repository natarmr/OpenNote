"""Microsoft Word (.docx) parser with heading/paragraph locators."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import List, Optional, Tuple

from opennote.ingest.chunking import (
    ChunkSpec,
    DocumentChunk,
    compute_chunk_id,
    sliding_window_chunk_with_offsets,
)
from opennote.ingest.parsers.base import SourceParser

logger = logging.getLogger("opennote.ingest.docx")

_HEADING_HINTS = ("heading", "title")


class DocxParser(SourceParser):
    """Chunk .docx sources, citing by section heading (``§ heading``) or by the
    range of paragraphs the chunk spans."""

    def parse(self, file_path: Path, spec: ChunkSpec) -> List[DocumentChunk]:
        try:
            from docx import Document
        except ImportError:
            raise RuntimeError(
                "python-docx is required to parse .docx files (pip install python-docx)."
            )

        document = Document(str(file_path))
        source = str(file_path.resolve())
        filename = file_path.name

        # Build ordered sections: (heading, [(para_index, text), ...])
        sections: List[Tuple[Optional[str], List[Tuple[int, str]]]] = []
        current_heading: Optional[str] = None
        current_body: List[Tuple[int, str]] = []

        def flush():
            nonlocal current_heading, current_body
            if current_body:
                sections.append((current_heading, current_body))
            current_heading = None
            current_body = []

        for para_idx, para in enumerate(document.paragraphs):
            text = para.text.strip()
            if not text:
                continue
            style = (para.style.name or "").lower()
            if any(h in style for h in _HEADING_HINTS):
                flush()
                current_heading = text
            else:
                current_body.append((para_idx, text))
        flush()

        chunks: List[DocumentChunk] = []
        chunk_idx = 0

        for heading, body in sections:
            body_text = "\n\n".join(t for _, t in body)
            para_start = body[0][0]
            para_end = body[-1][0]

            if len(body_text) <= spec.size:
                blocks = [(body_text, para_start, para_end)]
            else:
                blocks = [
                    (t, para_start, para_end)
                    for t, _, _ in sliding_window_chunk_with_offsets(
                        body_text, spec.size, spec.overlap
                    )
                ]

            for text, ps, pe in blocks:
                meta = {
                    "source": source,
                    "filename": filename,
                    "element_type": "text",
                    "char_count": len(text),
                }
                if heading:
                    meta["heading"] = heading
                else:
                    meta["line_start"] = ps + 1
                    meta["line_end"] = pe + 1

                chunk_id = compute_chunk_id(source, ps, pe, chunk_idx, text)
                chunks.append(
                    DocumentChunk(content=text, metadata=meta, chunk_id=chunk_id)
                )
                chunk_idx += 1

        logger.info(f"Extracted {len(chunks)} chunk(s) from '{filename}'.")
        return chunks