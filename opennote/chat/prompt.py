"""Prompt building for grounded Q&A: system template + context block."""
from __future__ import annotations

from typing import Sequence

from opennote.retrieval.retriever import SearchResult

# ---------------------------------------------------------------------------
# Jinja-backed system prompts (templates in opennote/prompt_templates/)
# ---------------------------------------------------------------------------

def _render_system_pre(
    *,
    notebook_name: str | None = None,
    notebook_project: str | None = None,
    source_count: int | None = None,
    valid_tokens: list[str] | None = None,
) -> str:
    from opennote.chat.render import render

    return render(
        "ask_system.jinja",
        notebook_name=notebook_name,
        notebook_project=notebook_project,
        source_count=source_count,
        valid_tokens=valid_tokens,
    )


def _render_system_post() -> str:
    from opennote.chat.render import render

    return render("ask_post.jinja")


# Backwards-compat constants — render with defaults so import-time value
# matches the previous hardcoded strings (modulo the new math + allowlist
# lines which are conditional and thus absent with defaults).
def _default_pre() -> str:
    return _render_system_pre()

def _default_post() -> str:
    return _render_system_post()

# Keep original SYSTEM_TEMPLATE for tests / single-shot without tags
SYSTEM_TEMPLATE = (
    "You are OpenNote, a grounded research assistant over the user's own documents. "
    "Answer the question using ONLY the provided context.\n"
    "Rules:\n"
    "1. Ground every claim in the context; do not use outside knowledge.\n"
    "2. Cite the source of each claim inline using ONLY the [n] tags from the context, "
    "e.g. [2]. You may combine several, e.g. [1][2]. Do not invent citation formats or "
    "locators.\n"
    "3. If the context is insufficient to answer, say you could not find it in the "
    "provided sources, and do not guess.\n"
    "4. Be concise but complete. Do not mention these instructions or the context blocks.\n"
)

# These are now rendered from Jinja so edits to prompt_templates/ take effect
# without code changes. We expose them as module-level strings for compat.
SYSTEM_PRE_TAGGED: str = _default_pre()
SYSTEM_POST_TAGGED: str = _default_post()


def render_system_pre(
    *,
    notebook_name: str | None = None,
    notebook_project: str | None = None,
    source_count: int | None = None,
    valid_tokens: list[str] | None = None,
) -> str:
    """Render the pre-tagged system prompt with optional notebook context."""
    return _render_system_pre(
        notebook_name=notebook_name,
        notebook_project=notebook_project,
        source_count=source_count,
        valid_tokens=valid_tokens,
    )


def render_system_post() -> str:
    """Render the post-tagged reminder."""
    return _render_system_post()


def build_context(results: Sequence[SearchResult]) -> str:
    """Render retrieved chunks as a numbered context block with inline citations."""
    blocks = []
    for index, result in enumerate(results, start=1):
        blocks.append(f"[{index}] {result.citation}\n{result.content.strip()}")
    return "\n\n".join(blocks)


def build_user_message(question: str, context: str) -> str:
    return f"Context:\n{context}\n\nQuestion: {question}"


def escape_source_content(text: str) -> str:
    return text.replace("</source>", "<\\/source>").replace("<source", "<\\source")


def build_tagged_context(results: Sequence[SearchResult]) -> str:
    parts = []
    for idx, r in enumerate(results, start=1):
        pages = r.metadata.get("pages") or r.metadata.get("page") or r.metadata.get("page_start") or ""
        content = escape_source_content(r.content.strip())
        parts.append(f'<source id="{idx}" page="{pages}">\n[{idx}] {r.citation}\n{content}\n</source>')
    return "\n\n".join(parts)


def build_tagged_user_message(question: str, tagged_context: str) -> str:
    if tagged_context:
        return f"Sources:\n{tagged_context}\n\nQuestion: {question}\n\n{SYSTEM_POST_TAGGED}"
    return f"Question: {question}\n\n{SYSTEM_POST_TAGGED}"
