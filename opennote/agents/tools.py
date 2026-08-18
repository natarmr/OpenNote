"""Tool definitions and dispatch for the agentic retrieval loop.

Each tool returns a list of ``SearchResult`` objects — the same type used
by ``opennote.ask`` — so that the model's inline ``[n]`` markers are
automatically validated against the actual chunks.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from opennote.retrieval.retriever import Retriever, SearchResult


# ---------------------------------------------------------------------------
# JSON‑schema definitions (OpenAI "functions" / Anthropic "input_schema")
# ---------------------------------------------------------------------------

TOOL_SCHEMAS: Dict[str, dict] = {
    "search": {
        "description": "Retrieve top‑k chunks for a free‑text query, optionally restricted to a single source filename.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "The search query."},
                "top_k": {
                    "type": "integer",
                    "description": "Number of chunks to return (default: 5).",
                    "default": 5,
                },
                "source": {"type": "string", "description": "Restrict results to this filename."},
            },
            "required": ["query"],
        },
    },
    "list_sources": {
        "description": "List all source filenames currently indexed in the notebook.",
        "parameters": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
}


# ---------------------------------------------------------------------------
# Dispatch implementation
# ---------------------------------------------------------------------------

def _search(
    retriever: Retriever,
    query: str,
    top_k: Any = 5,
    source: Optional[str] = None,
) -> List[SearchResult]:
    """Retrieve top‑k chunks for *query* on *retriever*, optionally filtered by *source*."""
    try:
        top_k = int(top_k) if top_k is not None else 5
    except (TypeError, ValueError):
        raise ValueError(f"top_k must be an integer, got {top_k!r}.")
    if top_k < 1:
        raise ValueError(f"top_k must be >= 1, got {top_k}.")
    if top_k > 25:
        raise ValueError(f"top_k must be <= 25, got {top_k}.")
    if not query or not str(query).strip():
        raise ValueError("search requires a non-empty 'query' string.")
    if source is not None:
        available = retriever.sources()
        if available and source not in available:
            raise ValueError(
                f"Source '{source}' not found. Available sources: {', '.join(sorted(available))}"
            )
    return retriever.search(query, top_k=top_k, source=source)


def _list_sources(retriever: Retriever) -> List[str]:
    """Return the filenames of all sources currently indexed in *retriever*."""
    return retriever.sources()


# Mapping from tool name → (Retriever, kwargs) → List[SearchResult]
_TOOL_DISPATCH: Dict[str, Any] = {
    "search": _search,
    "list_sources": _list_sources,
}


def execute_tool(
    tool_name: str, retriever: Retriever, kwargs: Optional[Dict[str, Any]]
) -> List[Any]:
    """Execute *tool_name* on *retriever* with *kwargs* (validated against the JSON‑schema).

    Raises ``ValueError`` if the tool name is unknown or required arguments are
    missing or malformed. Unknown extra keyword arguments are dropped rather
    than crashing.
    """
    schema = TOOL_SCHEMAS.get(tool_name)
    if schema is None:
        raise ValueError(f"Unknown tool: {tool_name}")

    kwargs = dict(kwargs or {})
    allowed = set(schema.get("parameters", {}).get("properties", {}))
    extra = set(kwargs) - allowed
    if extra:
        for key in extra:
            kwargs.pop(key)

    # Minimal validation – required fields from the schema
    required = schema.get("parameters", {}).get("required", [])
    for arg in required:
        if arg not in kwargs:
            raise ValueError(f"Missing required argument '{arg}' for tool {tool_name}")

    func = _TOOL_DISPATCH.get(tool_name)
    if func is None:
        raise ValueError(f"Tool {tool_name} not implemented")
    return func(retriever, **kwargs)


# ---------------------------------------------------------------------------
# Rendering helpers – turn SearchResult objects into model‑friendly text
# ---------------------------------------------------------------------------

def render_tool_results(results: List[SearchResult], max_lines: int = 6, offset: int = 0) -> str:
    """Render *results* as a numbered block the model can reference with ``[n]`` markers.

    *offset* shifts the numbering (e.g. to keep indices globally unique across
    multiple ``search`` calls in one turn, matching the flat ``retrieved``
    list used for citation validation).
    """
    lines: List[str] = []
    for i, r in enumerate(results, start=1):
        idx = offset + i
        lines.append(f"[{idx}] {r.citation}")
        content_lines = r.content.strip().splitlines()
        display = content_lines[:max_lines]
        if len(content_lines) > max_lines:
            display.append(f"... (+{len(content_lines) - max_lines} more lines)")
        lines.extend(display)
        lines.append("")
    return "\n".join(lines)