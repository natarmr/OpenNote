"""Prompt building for grounded Q&A: system template + context block."""
from __future__ import annotations

from typing import Sequence

from opennote.retrieval.retriever import SearchResult

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


def build_context(results: Sequence[SearchResult]) -> str:
    """Render retrieved chunks as a numbered context block with inline citations."""
    blocks = []
    for index, result in enumerate(results, start=1):
        blocks.append(f"[{index}] {result.citation}\n{result.content.strip()}")
    return "\n\n".join(blocks)


def build_user_message(question: str, context: str) -> str:
    return f"Context:\n{context}\n\nQuestion: {question}"