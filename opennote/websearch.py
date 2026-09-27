"""Web search tool for OpenNote (Tavily API).

Provides ``web_search(query, top_k)`` and ``read_page(url)`` that return
SearchResult-shaped objects so existing citation validation works unchanged.

Tavily key from env var ``TAVILY_API_KEY``. Tool is **hidden** from the model
when the key is absent (capability probe in Phase A).
"""
from __future__ import annotations

import logging
import os
import re
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
    max_results: int = _MAX_RESULTS,
) -> List[Dict[str, Any]]:
    """Call Tavily search and return raw JSON results list.

    Each dict has at least: ``url``, ``title``, ``content``, ``score``.
    """
    key = _get_tavily_key()
    if not key:
        raise RuntimeError("TAVILY_API_KEY not set")

    # Cap max_results to Tavily limit (20) — prevents unbounded API cost
    max_results = max(1, min(int(max_results), 20))
    if topic not in ("general", "news", "finance"):
        topic = _DEFAULT_TOPIC

    payload: Dict[str, Any] = {
        "query": query,
        "topic": topic,
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
    if not query or not str(query).strip():
        raise ValueError("web_search requires a non-empty query")
    try:
        top_k = int(top_k)
    except (TypeError, ValueError):
        raise ValueError(f"top_k must be an integer, got {top_k!r}")
    if top_k < 1 or top_k > 25:
        raise ValueError(f"top_k must be 1..25, got {top_k}")
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

        # Cap enrichment fetches — count attempts, not successes (prevents 10 sequential fetches on failures)
        if enrich_fetches < _MAX_ENRICH_FETCHES:
            enrich_fetches += 1
            try:
                import trafilatura

                # The url is a third-party response field and the query is
                # model-chosen, so this hop is unvalidated input — it goes
                # through the same guard as read_page rather than straight to
                # the fetcher.
                downloaded = trafilatura.fetch_url(_fetch_guarded(url))
                if downloaded:
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

        # Fallback: create SearchResult from bare Tavily data (handle content:null)
        raw_content = r.get("content") or ""
        if not isinstance(raw_content, str):
            raw_content = str(raw_content)
        output.append(
            SearchResult(
                content=raw_content[:_MAX_CONTENT_CHARS],
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

# A host made only of digits and dots is an IP-literal attempt. When ipaddress
# cannot parse one it is a non-canonical numeric form ("127.1", "0177.0.0.1")
# that the platform resolver still maps to a private address, so it is refused
# rather than passed through.
_NUMERIC_HOST = re.compile(r"^[0-9.]+$")

#: Redirect hops we validate ourselves. trafilatura follows redirects inside a
#: single urllib3 call (MAX_REDIRECTS=2 in 2.2.0) and never re-checks the host,
#: so a pre-request string check cannot see where it actually lands.
_MAX_REDIRECTS = 5
_FETCH_TIMEOUT = 20.0


def _is_private_ip(host: str) -> bool:
    """Return True when *host* is a raw IPv4/IPv6 address on a private range."""
    host = host.strip("[]").lower()
    if host in _PRIVATE_HOSTNAMES:
        return True
    # Block hex-encoded IP tricks like 0x7f.0.0.1 (bypasses ip_address check)
    if "0x" in host:
        return True
    # Strip trailing port when the netloc carried one.
    if host.count(":") == 1 and host.rsplit(":", 1)[1].isdigit():
        host = host.rsplit(":", 1)[0]
    if host.count(":") > 1:  # IPv6 literal like ::1 — block loopback family
        return True
    try:
        import ipaddress

        addr = ipaddress.ip_address(host)
    except ValueError:
        # Fail closed on a numeric host we cannot parse (127.1, 0177.0.0.1);
        # a real hostname is not numeric and is settled by the DNS check.
        return bool(_NUMERIC_HOST.match(host))
    return addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_reserved


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
    # Drop the root label: "localhost." and "metadata.google.internal." are
    # absolute-name spellings of names the membership and suffix tests below
    # would otherwise miss, and they resolve normally.
    host = parts.hostname.lower().rstrip(".")
    if not host:
        return False
    if host in _PRIVATE_HOSTNAMES:
        return False
    if host.endswith(_PRIVATE_HOST_SUFFIXES):
        return False
    # Single-label hostnames (no dot) are intranet-style names — block them.
    if "." not in host:
        return False
    return not _is_private_ip(host)


def _host_resolves_public(host: str) -> bool:
    """False when *host* resolves to any private/loopback/link-local address.

    The textual filter above is deliberately best-effort: a public hostname can
    resolve into a private range. This closes that gap for names we cannot
    recognise as IP literals. An unresolvable host returns True so the fetcher,
    not the guard, reports the real network error.
    """
    import ipaddress
    import socket

    try:
        infos = socket.getaddrinfo(host, None)
    except (OSError, UnicodeError):
        return True
    if not infos:
        return True
    for info in infos:
        try:
            addr = ipaddress.ip_address(info[4][0])
        except ValueError:
            return False
        if addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_reserved:
            return False
    return True


def _probe_headers() -> Dict[str, str]:
    """Send the same headers trafilatura would, so a probe is not distinguishable."""
    try:
        from trafilatura.downloads import DEFAULT_HEADERS

        return dict(DEFAULT_HEADERS)
    except Exception:
        return {}


def _next_redirect(url: str) -> Optional[str]:
    """Return the ``Location`` of *url*'s redirect, or None if it is not one.

    Reads only the status line and headers, then closes the connection, so the
    body is never transferred here — ``trafilatura`` still does the real fetch.
    """
    import httpx

    try:
        with (
            httpx.Client(
                follow_redirects=False, timeout=_FETCH_TIMEOUT, headers=_probe_headers()
            ) as client,
            client.stream("GET", url) as response,
        ):
            if response.status_code not in (301, 302, 303, 307, 308):
                return None
            return response.headers.get("location") or None
    except Exception as exc:  # noqa: BLE001
        # The probe and the real fetch share a transport, so a failure here
        # almost always means the fetch will fail too. Pass the URL through and
        # let trafilatura surface the error; the host was already validated.
        logger.debug("redirect probe failed for %s: %s", url, exc)
        return None


def _fetch_guarded(url: str) -> str:
    """Validate *url* and every redirect hop; return the final URL to fetch.

    This is the single chokepoint both fetch paths go through, so a third
    ``trafilatura.fetch_url`` cannot appear without inheriting the guard.
    """
    from urllib.parse import urljoin

    current = url
    for _ in range(_MAX_REDIRECTS + 1):
        if not _is_safe_url(current):
            raise RuntimeError(f"Refusing to fetch non-public URL: {current}")
        host = urlparse(current).hostname or ""
        if not _host_resolves_public(host.rstrip(".")):
            raise RuntimeError(f"Refusing to fetch URL resolving to a private address: {current}")
        target = _next_redirect(current)
        if not target:
            return current
        nxt = urljoin(current, target)
        if not nxt.lower().startswith(("http://", "https://")):
            raise RuntimeError(f"Refusing to follow non-http(s) redirect: {target}")
        current = nxt
    raise RuntimeError(f"Too many redirects while fetching: {url}")


# ---------------------------------------------------------------------------
# Read‑page tool: full‑page grounding (trafilatura)
# ---------------------------------------------------------------------------


def read_page(url: str) -> List[SearchResult]:
    """Fetch *url* and return citable chunks from its HTML structure.

    Uses trafilatura to download the page, then the same section/chunk logic
    as ``web_search`` so the results are SearchResult‑shaped and validate
    against the existing `[n]` marker system.
    """
    import trafilatura

    # The guard lives in the chokepoint, not at this call site, so a future
    # third fetch path cannot skip it (the L43 asymmetry).
    downloaded = trafilatura.fetch_url(_fetch_guarded(url))
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