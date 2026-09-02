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

    # Tagged sources with late constraint (defense 1)
    from opennote.chat.prompt import build_tagged_context, build_tagged_user_message, SYSTEM_PRE_TAGGED, SYSTEM_POST_TAGGED
    from opennote.validation.citation import validate_freeform_answer

    tagged = build_tagged_context(results)
    system = f"{SYSTEM_PRE_TAGGED}\n\nSources:\n{tagged}\n\n{SYSTEM_POST_TAGGED}" if tagged else f"{SYSTEM_PRE_TAGGED}\n\n{SYSTEM_POST_TAGGED}"
    user_message = build_tagged_user_message(question, tagged)
    raw = client.complete(
        system,
        [{"role": "user", "content": user_message}],
        max_tokens=max_tokens,
    )

    answer = raw.strip()
    # Gate free-form through validator (defense 3)
    chunk_map = {str(i+1): r for i, r in enumerate(results)}
    if answer.lower() != "sources don't contain this" and results:
        if not validate_freeform_answer(answer, chunk_map):
            answer = "sources don't contain this"
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