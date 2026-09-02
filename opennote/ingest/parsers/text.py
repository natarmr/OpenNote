"""Plain-text and Markdown parser with line-range citations."""
from __future__ import annotations

import bisect
import logging
import re
from pathlib import Path
from typing import List

from opennote.ingest.chunking import (
    ChunkSpec,
    DocumentChunk,
    compute_chunk_id,
    sliding_window_chunk_with_offsets,
)
from opennote.ingest.parsers.base import SourceParser

logger = logging.getLogger("opennote.ingest.text")


def _line_starts(text: str) -> List[int]:
    starts = [0]
    for m in re.finditer("\n", text):
        starts.append(m.end())
    return starts


def _char_to_line(line_starts: List[int], pos: int) -> int:
    """Return the 1-based line number for a character offset."""
    return bisect.bisect_right(line_starts, pos) - 1 + 1


class TextParser(SourceParser):
    """Chunk .txt / .md sources, citing by line range (e.g. L12-L19)."""

    def parse(self, file_path: Path, spec: ChunkSpec) -> List[DocumentChunk]:
        # Size guard C1: skip huge files before full read
        try:
            if file_path.stat().st_size > 50 * 1024 * 1024:
                logger.warning(f"Skipping '{file_path.name}': file too large (>50MB).")
                return []
        except OSError:
            pass
        text = file_path.read_text(encoding="utf-8-sig", errors="replace")
        if not text.strip():
            logger.warning(f"No text content in '{file_path.name}'.")
            return []
        # C2: detect binary / high replacement-char ratio
        if "\x00" in text or text.count("\ufffd") > len(text) * 0.1:
            logger.warning(f"Skipping '{file_path.name}': appears binary or heavily corrupted.")
            return []

        source = str(file_path.resolve())
        filename = file_path.name
        starts = _line_starts(text)
        chunks: List[DocumentChunk] = []

        for idx, (chunk_text, start, end) in enumerate(
            sliding_window_chunk_with_offsets(text, spec.size, spec.overlap)
        ):
            line_start = _char_to_line(starts, start)
            line_end = _char_to_line(starts, max(end - 1, start))
            chunk_id = compute_chunk_id(source, line_start, line_end, idx, chunk_text)
            chunks.append(
                DocumentChunk(
                    content=chunk_text,
                    metadata={
                        "source": source,
                        "filename": filename,
                        "line_start": line_start,
                        "line_end": line_end,
                        "element_type": "text",
                        "char_count": len(chunk_text),
                    },
                    chunk_id=chunk_id,
                )
            )

        logger.info(f"Extracted {len(chunks)} chunk(s) from '{filename}'.")
        return chunks