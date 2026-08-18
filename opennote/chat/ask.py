"""Grounded ask: retrieve -> ground -> complete -> validate citations."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

from opennote.chat.citations import used_sources
from opennote.chat.client import ChatError, LLMClient, default_provider, get_client
from opennote.chat.prompt import SYSTEM_TEMPLATE, build_context, build_user_message
from opennote.notebooks import Notebook
from opennote.retrieval.citations import Citation
from opennote.retrieval.retriever import Retriever, SearchResult


@dataclass
class AskResult:
    question: str
    answer: str
    sources: List[Citation] = field(default_factory=list)
    results: List[SearchResult] = field(default_factory=list)
    provider_id: str = ""
    model: str = ""


def ask(
    notebook: Notebook,
    question: str,
    provider_id: Optional[str] = None,
    top_k: int = 5,
    max_tokens: int = 1024,
    client: Optional[LLMClient] = None,
    retriever: Optional[Retriever] = None,
) -> AskResult:
    """Answer ``question`` grounded in ``notebook`` with validated citations.

    ``client``/``retriever`` are injectable for tests; when omitted they are
    built from the configured auth state.
    """
    if client is None:
        client = get_client(provider_id or default_provider())
    retriever = retriever or Retriever(notebook, top_k=top_k)

    results = retriever.search(question)
    if not results:
        return AskResult(
            question=question,
            answer="I could not find any relevant sources in this notebook.",
            provider_id=client.provider_id,
            model=client.model,
        )

    context = build_context(results)
    user_message = build_user_message(question, context)
    raw = client.complete(
        SYSTEM_TEMPLATE,
        [{"role": "user", "content": user_message}],
        max_tokens=max_tokens,
    )

    answer = raw.strip()
    footer, sources_used = used_sources(answer, results)
    if footer:
        answer = f"{answer}\n\n{footer}"
    return AskResult(
        question=question,
        answer=answer,
        sources=sources_used,
        results=results,
        provider_id=client.provider_id,
        model=client.model,
    )