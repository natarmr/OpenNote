"""Core chunking utilities and shared data structures.

Ported from the original ``ingest.py``.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Tuple


@dataclass
class DocumentChunk:
    """A chunk extracted from a document with rich metadata."""

    content: str
    metadata: Dict[str, Any]
    chunk_id: str


@dataclass
class ChunkSpec:
    """Configuration for text chunking."""

    size: int = 800
    overlap: int = 120


def compute_file_hash(file_path: Path) -> str:
    """Compute SHA256 hash of a file for change detection."""
    hasher = hashlib.sha256()
    with open(file_path, "rb") as f:
        while chunk := f.read(65536):
            hasher.update(chunk)
    return hasher.hexdigest()


def compute_chunk_id(
    source: str, page_start: int, page_end: int, chunk_idx: int, content: str
) -> str:
    """Generate a deterministic unique ID for a chunk to prevent duplicate inserts."""
    hasher = hashlib.sha256()
    hasher.update(
        f"{source}_{page_start}_{page_end}_{chunk_idx}_{content[:120]}".encode("utf-8")
    )
    return hasher.hexdigest()[:16]


def sliding_window_chunk_with_offsets(
    text: str,
    chunk_size: int = 800,
    chunk_overlap: int = 120,
) -> List[Tuple[str, int, int]]:
    """
    Splits text into sliding window chunks while respecting paragraph and
    sentence boundaries. Returns ``(chunk_text, start_char, end_char)`` tuples.
    """
    if not text.strip():
        return []

    if len(text) <= chunk_size:
        return [(text.strip(), 0, len(text))]

    chunks: List[Tuple[str, int, int]] = []
    start = 0
    while start < len(text):
        end = min(start + chunk_size, len(text))

        # Try to break at paragraph boundary
        if end < len(text):
            newline_pos = text.rfind("\n\n", start, end)
            if newline_pos != -1 and newline_pos > start + (chunk_size // 3):
                end = newline_pos + 2
            else:
                # Try to break at sentence boundary
                period_pos = text.rfind(". ", start, end)
                if period_pos != -1 and period_pos > start + (chunk_size // 3):
                    end = period_pos + 2
                else:
                    # Try to break at single newline or space
                    space_pos = text.rfind(" ", start, end)
                    if space_pos != -1 and space_pos > start + (chunk_size // 3):
                        end = space_pos + 1

        chunk_str = text[start:end].strip()
        if chunk_str:
            chunks.append((chunk_str, start, end))

        if end >= len(text):
            break
        start = max(end - chunk_overlap, start + 1)

    return chunks


def split_markdown_by_headings(md_text: str) -> List[str]:
    """Split markdown text on any heading level (H1-H6), preserving titles."""
    header_pattern = re.compile(r"(?m)(?=^#{1,6}\s+)")
    sections = header_pattern.split(md_text)
    return [s.strip() for s in sections if s.strip()]


def build_citation_pages(page_start: int, page_end: int) -> str:
    """Format a page citation range: '4' or '4-5'."""
    if page_start == page_end:
        return f"{page_start}"
    return f"{page_start}-{page_end}"