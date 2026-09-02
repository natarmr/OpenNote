"""Web search tool for OpenNote (Tavily API).

Provides ``web_search(query, top_k)`` and ``read_page(url)`` that return
SearchResult-shaped objects so existing citation validation works unchanged.

Tavily key from env var ``TAVILY_API_KEY``. Tool is **hidden** from the model
when the key is absent (capability probe in Phase A).
"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import httpx

from opennote.ingest.parsers.html import _extract_sections, _chunk_sections
from opennote.ingest.chunking import ChunkSpec
from opennote.retrieval.retriever import SearchResult
from opennote.retrieval.citations import citation_for

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Tavily client (httpx only — no new pip dep)
# ---------------------------------------------------------------------------

TAVILY_API_URL = "https://api.tavily.com/search"

_DEFAULT_TOPIC = "general"
_DEFAULT_TONE = "balanced"
_MAX_RESULTS = 5
_MAX_CONTENT_CHARS = 2000
# Typical Tavily key format is "tvly-..." sent via Bearer auth (L42).
_AUTH_TIMEOUT = 20.0
# Cap the number of sequential enrichment fetches per web_search call (L47).
_MAX_ENRICH_FETCHES = 3


def _get_tavily_key() -> Optional[str]:
    return os.environ.get("TAVILY_API_KEY")


def _page_title(url: str, fallback: str = "web") -> str:
    """Best-effort human title for a URL (L45: never `str.title()` the URL)."""
    try:
        parts = urlparse(url)
        netloc = parts.netloc
        path = parts.path.strip("/")
    except ValueError:
        return fallback
    if not parts.scheme or not netloc:
        return fallback
    if path:
        return f"{netloc} — {path}"
    return netloc


def _tavily_search(
    query: str,
    *,
    topic: str = _DEFAULT_TOPIC,
    tone: str = _DEFAULT_TONE,
    max_results: int = _MAX_RESULTS,
) -> List[Dict[str, Any]]:
    """Call Tavily search and return raw JSON results list.

    Each dict has at least: ``url``, ``title``, ``content``, ``score``.
    """
    key = _get_tavily_key()
    if not key:
        raise RuntimeError("TAVILY_API_KEY not set")

    payload: Dict[str, Any] = {
        "query": query,
        "topic": topic,
        "tone": tone,
        "max_results": max_results,
    }
    headers = {"Authorization": f"Bearer {key}"}

    try:
        resp = httpx.post(TAVILY_API_URL, json=payload, headers=headers, timeout=_AUTH_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        # Tavily returns {"type":"search","results":[...]}
        if isinstance(data, dict) and "results" in data:
            results = data["results"]
        elif isinstance(data, list):
            results = data
        else:
            results = []
        return [r for r in results if isinstance(r, dict)]
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"Tavily search failed: {exc}")


# ---------------------------------------------------------------------------
# Search tool: returns SearchResult objects (citation validation unchanged)
# ---------------------------------------------------------------------------


def web_search(query: str, top_k: int = 5) -> List[SearchResult]:
    """Retrieve top‑k chunks for *query* via Tavily web search.

    The returned objects carry metadata ``url``, ``title``, ``fetched_at`` so that
    ``retrieval/citations.py`` can format them as ``[hostname, "Page title"]``.
    """
    from datetime import datetime, timezone

    results = _tavily_search(query, max_results=top_k)

    output: List[SearchResult] = []
    enrich_fetches = 0
    for r in results:
        url = r.get("url", "")
        title = r.get("title", "") or _page_title(url)
        fetched_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        # Build metadata the same way URL ingestion does (html.py _chunk_sections)
        meta: Dict[str, Any] = {
            "source": url,
            "filename": url,
            "element_type": "text",
            "char_count": 0,
            "url": url,
            "title": title,
            "fetched_at": fetched_at,
        }

        # If the URL is short enough and looks like it has HTML, try to extract
        # sections (title + first few paragraphs) via trafilatura for richer
        # heading/citation metadata. Cap the number of sequential fetches so a
        # web_search tool call stays cancellable (L47).
        if enrich_fetches < _MAX_ENRICH_FETCHES:
            try:
                import trafilatura

                downloaded = trafilatura.fetch_url(url)
                if downloaded:
                    enrich_fetches += 1
                    sections = _extract_sections(downloaded)
                    chunks = _chunk_sections(sections, url, title or url, ChunkSpec())
                    if chunks:
                        # Use the first chunk's metadata, enriched with Tavily title
                        c = chunks[0]
                        c.meta["title"] = title
                        c.meta["fetched_at"] = fetched_at
                        # Convert DocumentChunk → SearchResult
                        output.append(
                            SearchResult(
                                content=c.content,
                                metadata={**c.meta, "id": c.chunk_id},
                                similarity=1.0,
                                citation=citation_for(c.meta),
                            )
                        )
                        continue
            except Exception as exc:  # noqa: BLE001
                logger.warning("trafilatura enrichment failed for %s: %s", url, exc)

        # Fallback: create SearchResult from bare Tavily data
        output.append(
            SearchResult(
                content=r.get("content", "")[:_MAX_CONTENT_CHARS],
                metadata=meta,
                similarity=r.get("score", 0.0),
                citation=citation_for(meta),
            )
        )

    return output


# ---------------------------------------------------------------------------
# SSRF guard (L43): never fetch private / loopback / link-local addresses
# ---------------------------------------------------------------------------

_PRIVATE_SCHEMES = {"http", "https"}
_PRIVATE_HOST_SUFFIXES = (
    ".local",
    ".localhost",
    ".internal",
    ".lan",
    ".home.arpa",
    ".corp",
)
# Hostnames that resolve to the machine itself or private ranges.
_PRIVATE_HOSTNAMES = {
    "localhost",
    "127.0.0.1",
    "::1",
    "0.0.0.0",
    "metadata.google.internal",
    "metadata.aws.internal",
}


def _is_private_ip(host: str) -> bool:
    """Return True when *host* is a raw IPv4/IPv6 address on a private range."""
    host = host.strip("[]").lower()
    if host in _PRIVATE_HOSTNAMES:
        return True
    # Strip trailing port when the netloc carried one.
    if host.count(":") == 1 and host.rsplit(":", 1)[1].isdigit():
        host = host.rsplit(":", 1)[0]
    if host.count(":") > 1:  # IPv6 literal like ::1 — block loopback family
        return True
    try:
        import ipaddress

        addr = ipaddress.ip_address(host)
        return addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_reserved
    except ValueError:
        return False


def _is_safe_url(url: str) -> bool:
    """Return True when *url* is http(s) and its host is not private/loopback."""
    try:
        parts = urlparse(url)
    except ValueError:
        return False
    if parts.scheme.lower() not in _PRIVATE_SCHEMES:
        return False
    if not parts.hostname:
        return False
    host = parts.hostname.lower()
    if host in _PRIVATE_HOSTNAMES:
        return False
    if host.endswith(_PRIVATE_HOST_SUFFIXES):
        return False
    # Single-label hostnames (no dot) are intranet-style names — block them.
    if "." not in host:
        return False
    return not _is_private_ip(host)


# ---------------------------------------------------------------------------
# Read‑page tool: full‑page grounding (trafilatura)
# ---------------------------------------------------------------------------


def read_page(url: str) -> List[SearchResult]:
    """Fetch *url* and return citable chunks from its HTML structure.

    Uses trafilatura to download the page, then the same section/chunk logic
    as ``web_search`` so the results are SearchResult‑shaped and validate
    against the existing `[n]` marker system.
    """
    if not _is_safe_url(url):
        raise RuntimeError(f"Refusing to fetch non-public URL: {url}")
    import trafilatura

    downloaded = trafilatura.fetch_url(url)
    if not downloaded:
        raise RuntimeError(f"Failed to fetch URL: {url}")

    sections = _extract_sections(downloaded)
    chunks = _chunk_sections(sections, url, _page_title(url), ChunkSpec())

    output: List[SearchResult] = []
    for c in chunks:
        meta: Dict[str, Any] = {
            "source": url,
            "filename": url,
            "element_type": "text",
            "char_count": 0,
            "url": url,
            "title": _page_title(url),
        }
        output.append(
            SearchResult(
                content=c.content,
                metadata={**meta, "id": c.chunk_id},
                similarity=1.0,
                citation=citation_for(meta),
            )
        )

    return output


# ---------------------------------------------------------------------------
# Convenience: quick inline search (CLI / one‑off)
# ---------------------------------------------------------------------------

def quick_search(query: str, top_k: int = 3) -> List[SearchResult]:
    """One‑off search – handy for CLI or ad‑hoc use.

    Kept separate from the tool‑dispatch system so it doesn't pollute the
    model‑seen tool set unless explicitly enabled.
    """
    return web_search(query, top_k=top_k)