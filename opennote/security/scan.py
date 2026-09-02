"""Chunk pre-scan — telemetry, not a gate (defense 6)."""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import List

logger = logging.getLogger("opennote.security.scan")

INJECTION_MARKERS = [
    r"ignore.*instructions",
    r"you are now",
    r"system prompt",
    r"new instructions?:",
    r"disregard (the )?(above|prior)",
    r"override.*instructions",
    r"as an ai",
    r"reveal.*prompt",
]


_COMPILED = [re.compile(p, re.IGNORECASE) for p in INJECTION_MARKERS]


def scan_chunk(text: str, source: str = "", chunk_id: str = "") -> List[str]:
    """Return list of matched marker patterns for telemetry."""
    hits = []
    for pat, comp in zip(INJECTION_MARKERS, _COMPILED):
        if comp.search(text):
            hits.append(pat)
    return hits


def log_scan_hits(notebook, source: str, chunk_id: str, text: str, hits: List[str]) -> None:
    if not hits:
        return
    log_path = notebook.directory / "security.log"
    entry = {"source": source, "chunk_id": chunk_id, "hits": hits, "excerpt": text[:200]}
    try:
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception:
        pass
    logger.warning("Injection-like text in %s chunk %s: %s", source, chunk_id, hits)


def scan_and_log(notebook, chunks) -> None:
    """Scan list of DocumentChunk or dict-like and log hits."""
    for ch in chunks:
        text = getattr(ch, "content", None) or ch.get("content", "") if isinstance(ch, dict) else str(ch)
        source = getattr(ch, "metadata", {}).get("filename", "") if hasattr(ch, "metadata") else ""
        cid = getattr(ch, "metadata", {}).get("chunk_id", "") if hasattr(ch, "metadata") else ""
        hits = scan_chunk(text, source, cid)
        if hits:
            log_scan_hits(notebook, source, cid, text, hits)
