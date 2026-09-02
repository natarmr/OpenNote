"""Citation formatting for retrieved chunks.

Citations point back to a source location: ``[filename, p.4-5]`` today. The
locator is source-type-aware and extensible (URL/heading/timestamp for the
Phase 3 source types).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List

PAGE_LOCATOR_KEYS = ("pages", "page_start", "page_end", "page", "timestamp")


@dataclass
class Citation:
    """A single source reference attached to an answer claim."""

    source: str
    locator: str
    label: str

    def __str__(self) -> str:
        return f"[{self.source}, {self.locator}]"

    def to_dict(self) -> Dict[str, str]:
        return {"source": self.source, "locator": self.locator, "label": self.label}


def _pick_locator(meta: Dict[str, Any]) -> str:
    """Return the best human-readable locator from a chunk's metadata."""
    # Priority: a dedicated page-citation string, then page range, then single page.
    if meta.get("pages"):
        return f"p.{meta['pages']}"
    if meta.get("page_start") and meta.get("page_end"):
        if meta["page_start"] == meta["page_end"]:
            return f"p.{meta['page_start']}"
        return f"p.{meta['page_start']}-{meta['page_end']}"
    if meta.get("page_start"):
        return f"p.{meta['page_start']}"
    if meta.get("timestamp"):
        return meta["timestamp"]
    # Heading/line locators (txt, docx, html, AV) extend here in later phases.
    if meta.get("heading"):
        return f"§ {meta['heading']}"
    if meta.get("line_start"):
        return f"L{meta['line_start']}"
    # Web results: URL hostname + optional page title (L48 — previously dead code).
    if meta.get("url"):
        from urllib.parse import urlparse

        host = urlparse(meta["url"]).netloc or meta["url"]
        title = meta.get("title") or ""
        if title and title != host:
            return f"{host}, \"{title}\""
        return host
    return "loc. n/a"


def _source_label(meta: Dict[str, Any]) -> str:
    return (
        meta.get("filename")
        or meta.get("source")
        or meta.get("url")
        or "unknown"
    )


def citation_for(meta: Dict[str, Any]) -> Citation:
    """Build a Citation from a chunk's metadata."""
    source = _source_label(meta)
    locator = _pick_locator(meta)
    return Citation(
        source=source,
        locator=locator,
        label=f"{source}, {locator}",
    )


def citations_for(results: List[Dict[str, Any]]) -> List[Citation]:
    """Build citations for a list of raw search result dicts."""
    return [citation_for(r.get("metadata", {})) for r in results]