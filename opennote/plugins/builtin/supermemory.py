"""Built-in Supermemory plugin — memory search + turn-complete memory write.

Gated on SUPERMEMORY_API_KEY (like TAVILY for web_search). When the key is
absent the plugin is not loaded; when present it registers a ``memory_search``
tool and an ``on_turn_complete`` hook that stores Q&A turns as memories.

Uses httpx directly (no new deps) against the Supermemory v3 API.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional

logger = logging.getLogger("opennote.plugins.supermemory")

_SUPERMEMORY_API_BASE = os.environ.get("SUPERMEMORY_API_BASE", "https://api.supermemory.ai")
# Optional container tag to scope memories per project/notebook
_DEFAULT_CONTAINER = "opennote"


def _headers() -> Dict[str, str]:
    key = os.environ.get("SUPERMEMORY_API_KEY", "")
    return {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}


def _memory_search_impl(query: str, top_k: Any = 5, container_tag: str | None = None) -> List[Dict[str, Any]]:
    """Call Supermemory search and return normalized hits.

    Returns a list of dicts {content, metadata}. The plugin tool wrapper
    will convert these to SearchResult-like results for the LLM.
    """
    import httpx

    try:
        top_k = int(top_k) if top_k is not None else 5
    except (TypeError, ValueError):
        raise ValueError(f"top_k must be an integer, got {top_k!r}")
    if top_k < 1:
        raise ValueError(f"top_k must be >= 1, got {top_k}")
    if top_k > 25:
        raise ValueError(f"top_k must be <= 25, got {top_k}")
    if not query or not str(query).strip():
        raise ValueError("memory_search requires a non-empty 'query' string.")

    key = os.environ.get("SUPERMEMORY_API_KEY")
    if not key:
        raise RuntimeError("SUPERMEMORY_API_KEY is not set")

    # Supermemory v3 search: POST /v3/search  {q, containerTag, limit}
    # See https://supermemory.ai/docs — uses containerTags for scoping
    payload: Dict[str, Any] = {
        "q": query.strip(),
        "limit": top_k,
    }
    if container_tag:
        payload["containerTag"] = container_tag
    else:
        # Use default unless caller overrides; keeps memories scoped per app
        payload["containerTag"] = os.environ.get("SUPERMEMORY_CONTAINER_TAG", _DEFAULT_CONTAINER)

    url = f"{_SUPERMEMORY_API_BASE.rstrip('/')}/v3/search"
    try:
        with httpx.Client(timeout=15) as client:
            resp = client.post(url, json=payload, headers=_headers())
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:
        logger.warning("Supermemory search failed: %s", exc)
        raise RuntimeError(f"Supermemory search failed: {exc}") from exc

    # Normalize response shapes — API returns {results: [{content, metadata, ...}]} or {memories: [...]}
    results = data.get("results") or data.get("memories") or data.get("data") or []
    if isinstance(data, list):
        results = data
    out: List[Dict[str, Any]] = []
    for r in results[:top_k]:
        if isinstance(r, dict):
            content = r.get("content") or r.get("memory") or r.get("text") or ""
            meta = r.get("metadata") or {}
            if not isinstance(meta, dict):
                meta = {"raw_metadata": str(meta)}
            meta.setdefault("source", "supermemory")
            out.append({"content": str(content), "metadata": meta})
        elif isinstance(r, str):
            out.append({"content": r, "metadata": {"source": "supermemory"}})
    return out


def _memory_search_tool(ctx: Any, query: str, top_k: Any = 5) -> List[Any]:
    """Tool execute wrapper — returns SearchResult objects so citation flow works."""
    from opennote.retrieval.citations import citation_for
    from opennote.retrieval.retriever import SearchResult

    # Notebook-scoped container tag if available
    container_tag = None
    try:
        nb = getattr(ctx, "notebook", None)
        if nb and getattr(nb, "name", None):
            container_tag = f"opennote-{nb.name}"
    except Exception:
        pass

    hits = _memory_search_impl(query, top_k=top_k, container_tag=container_tag)
    results: List[SearchResult] = []
    for h in hits:
        meta = dict(h.get("metadata", {}))
        meta.setdefault("filename", "supermemory")
        results.append(
            SearchResult(
                content=h.get("content", ""),
                metadata=meta,
                similarity=0.7,
                citation=citation_for(meta),
            )
        )
    return results


def _on_turn_complete(result: Any) -> None:
    """Store the Q&A turn as a memory (fire-and-forget, best-effort)."""
    import httpx

    key = os.environ.get("SUPERMEMORY_API_KEY")
    if not key:
        return
    try:
        question = getattr(result, "question", "") or ""
        answer = getattr(result, "answer", "") or ""
        if not question or not answer:
            return
        # Don't store abstention answers
        if "sources don't contain this" in answer:
            return
        container_tag = os.environ.get("SUPERMEMORY_CONTAINER_TAG", _DEFAULT_CONTAINER)
        # Try to scope by notebook name
        try:
            # result may have notebook-ish context in extra? Use containerTag variant
            pass
        except Exception:
            pass

        payload = {
            "content": f"Q: {question}\nA: {answer}",
            "containerTag": container_tag,
            "metadata": {"source": "opennote", "type": "qa_turn"},
        }
        url = f"{_SUPERMEMORY_API_BASE.rstrip('/')}/v3/add"
        # Also try /v3/memories — handle both shapes with fallback
        try:
            with httpx.Client(timeout=10) as client:
                resp = client.post(url, json=payload, headers=_headers())
                if resp.status_code == 404:
                    # Fallback endpoint
                    alt_url = f"{_SUPERMEMORY_API_BASE.rstrip('/')}/v3/memories"
                    resp = client.post(alt_url, json=payload, headers=_headers())
                resp.raise_for_status()
        except Exception as exc:
            logger.debug("Supermemory add failed (non-fatal): %s", exc)
    except Exception as exc:
        logger.debug("Supermemory on_turn_complete failed: %s", exc)


def register(ctx) -> Dict[str, Any]:
    """Plugin entry point — called by PluginLoader."""
    # Double-gate: loader already checks key, but be defensive
    if not os.environ.get("SUPERMEMORY_API_KEY"):
        return {"tools": {}}

    return {
        "tools": {
            "memory_search": {
                "description": "Search long-term memories (Supermemory) for a query. Use for recalling prior conversations, preferences, and stored knowledge across sessions.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Search query for memories."},
                        "top_k": {"type": "integer", "description": "Number of memories to return (default: 5).", "default": 5},
                    },
                    "required": ["query"],
                },
                # capture ctx via closure; the loader will call execute(tool_ctx, **kwargs)
                # but our wrapper expects (tool_ctx, query, top_k) so adapt
                "execute": lambda tool_ctx, query, top_k=5, **_: _memory_search_tool(tool_ctx, query, top_k),
            }
        },
        "on_turn_complete": _on_turn_complete,
    }
