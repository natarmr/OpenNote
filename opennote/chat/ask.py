"""Grounded ask: retrieve -> ground -> complete -> validate citations."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

from opennote.chat.citations import used_sources
from opennote.chat.client import LLMClient, default_provider, get_client
from opennote.context_meter import ContextUsage
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
    usage: Optional["ContextUsage"] = None  # per-turn context accounting (engg_choices.md:E12)


def _single_shot(
    question: str,
    results: List[SearchResult],
    notebook: Notebook,
    client: LLMClient,
    max_tokens: int,
    context_budget: int | None = 12000,
) -> str:
    """Single-shot grounded completion with notebook context + allowlist."""
    from opennote.chat.context_budget import (
        build_fitted_tagged_context,
        fit_tagged_context,
        fitted_results,
    )
    from opennote.chat.prompt import build_tagged_context, build_tagged_user_message, render_system_post, render_system_pre

    if context_budget is not None:
        fitted = fit_tagged_context(results, budget_chars=context_budget)
        results = fitted_results(fitted)
        tagged = build_fitted_tagged_context(fitted)
    else:
        tagged = build_tagged_context(results)
    valid_tokens = [f"[{i+1}]" for i in range(len(results))]
    system_pre = render_system_pre(
        notebook_name=notebook.name,
        notebook_project=getattr(notebook, "project", "") or None,
        source_count=len(notebook.sources) if hasattr(notebook, "sources") else None,
        valid_tokens=valid_tokens or None,
    )
    system_post = render_system_post()
    system = f"{system_pre}\n\nSources:\n{tagged}\n\n{system_post}" if tagged else f"{system_pre}\n\n{system_post}"
    user_message = build_tagged_user_message(question, tagged)
    messages = [{"role": "user", "content": user_message}]
    raw = client.complete(
        system,
        messages,
        max_tokens=max_tokens,
    )
    from opennote.chat.clean import clean_thinking_content

    return clean_thinking_content(raw).strip(), system, messages


def _drain_usage(client: LLMClient, acc: "TokenUsage") -> "TokenUsage":
    """Add client.last_usage into acc (opencode-style summed totals)."""
    try:
        from opennote.chat.client import TokenUsage as _TU

        u = getattr(client, "last_usage", None)
        if isinstance(u, _TU) and (u.prompt_tokens or u.completion_tokens):
            return acc + u
    except Exception:
        pass
    return acc


def _multihop(
    question: str,
    notebook: Notebook,
    client: LLMClient,
    retriever: Retriever,
    max_tokens: int,
    context_budget: int | None = 12000,
) -> tuple[str, List[SearchResult]] | None:
    """Plan → per-query workers → synthesizer. Returns (answer, all_results) or None on fallback."""
    from opennote.chat.clean import clean_thinking_content
    from opennote.chat.client import TokenUsage as _TU
    from opennote.chat.context_budget import (
        build_fitted_tagged_context,
        fit_tagged_context,
        fitted_results,
    )
    from opennote.chat.planner import plan_queries
    from opennote.chat.prompt import build_tagged_context
    from opennote.chat.render import render

    acc = _TU()
    plan = plan_queries(question, client)
    acc = _drain_usage(client, acc)
    if plan is None:
        return None

    # Collect results per sub-query
    all_results: List[SearchResult] = []
    seen_ids: set[str] = set()
    worker_answers: List[str] = []
    strategy_text = plan.reasoning

    for pq in plan.searches:
        try:
            sub_results = retriever.search(pq.term)
        except Exception:
            sub_results = []

        # Deduplicate by chunk id / citation label
        deduped: List[SearchResult] = []
        for r in sub_results:
            cid = r.id or str(r.citation)
            if cid not in seen_ids:
                seen_ids.add(cid)
                deduped.append(r)
                all_results.append(r)

        if not deduped:
            worker_answers.append(f"[No results for: {pq.term}]")
            continue

        # Per-query worker — context escaped inside build_tagged_context
        if context_budget is not None:
            _f = fit_tagged_context(deduped, budget_chars=context_budget)
            tagged = build_fitted_tagged_context(_f)
            deduped = fitted_results(_f)
        else:
            tagged = build_tagged_context(deduped)
        valid_tokens = [f"[{i+1}]" for i in range(len(deduped))]
        # Map worker's local [1]..[N] to global indices for synthesizer traceability
        # For now workers cite local tokens; synthesizer re-cites after merge.
        worker_system = render(
            "worker.jinja",
            question=question,
            term=pq.term,
            instructions=pq.instructions,
            results=tagged,
            valid_tokens=valid_tokens,
        )
        try:
            w_raw = client.complete(
                worker_system,
                [{"role": "user", "content": f"Question: {question}\n\nInstructions: {pq.instructions}"}],
                max_tokens=max_tokens,
            )
            w_raw = clean_thinking_content(w_raw)
            acc = _drain_usage(client, acc)
        except Exception:
            w_raw = ""
        worker_answers.append(w_raw.strip() or "[No answer]")

    if not all_results:
        return None

    # Synthesizer — sees all worker answers; must re-cite with global tokens
    global_tokens = [f"[{i+1}]" for i in range(len(all_results))]
    if context_budget is not None:
        _fall = fit_tagged_context(all_results, budget_chars=context_budget)
        all_results = fitted_results(_fall)
        global_tokens = [f"[{i+1}]" for i in range(len(all_results))]
        tagged_all = build_fitted_tagged_context(_fall)
    else:
        tagged_all = build_tagged_context(all_results)
    answers_block = "\n\n".join(f"--- Answer {i+1} ---\n{a}" for i, a in enumerate(worker_answers))
    synth_prompt = render(
        "synthesizer.jinja",
        question=question,
        strategy=strategy_text,
        answers=answers_block + f"\n\nAll sources:\n{tagged_all}",
        valid_tokens=global_tokens,
    )
    synth_messages = [{"role": "user", "content": f"Question: {question}\n\nProvide the final answer with citations {', '.join(global_tokens)}."}]
    try:
        final = client.complete(
            synth_prompt,
            synth_messages,
            max_tokens=max_tokens,
        )
        acc = _drain_usage(client, acc)
    except Exception:
        return None
    final = clean_thinking_content(final)

    # Annotate partial coverage so silent worker gaps are visible, not hidden.
    partial = [
        i + 1
        for i, a in enumerate(worker_answers)
        if a == "[No answer]" or a.startswith("[No results")
    ]
    final_text = final.strip()
    if final_text and partial:
        final_text += (
            f"\n\n*Note: {len(partial)} of {len(worker_answers)} sub-question(s) "
            f"returned nothing; synthesized from partial coverage.*"
        )

    # stash summed provider usage for the caller (may be all-zero → estimate)
    try:
        client.last_usage = acc if (acc.prompt_tokens or acc.completion_tokens) else None  # type: ignore[attr-defined]
    except Exception:
        pass
    return final_text, all_results, synth_prompt, synth_messages


def _track_usage(system, messages, answer, client, chunks, notebook) -> "ContextUsage | None":
    """Meter a turn: provider usage when reported, else chars/4 estimate.

    Logs the turn and accumulates session spend/tokens. Warns (not silent)
    when metering itself fails so a missing panel is diagnosable.
    """
    import logging

    _log = logging.getLogger("opennote.context_meter")
    try:
        from opennote.context_meter import log_turn, record_spent, summarize

        model = getattr(client, "model", "") or ""
        provider_usage = getattr(client, "last_usage", None)
        usage = summarize(system, messages, answer, model=model, chunks=chunks, provider_usage=provider_usage)
        nb_dir = getattr(notebook, "directory", None)
        usage.session_spent = record_spent(usage.cost, nb_dir)
        try:
            from opennote.context_meter import load_spent as _ls

            usage.session_tokens = usage.total  # per-turn fill; spend accumulates separately
        except Exception:
            pass
        log_turn(usage, where="ask")
        return usage
    except Exception as exc:
        _log.warning("context metering failed: %s", exc)
        return None
        return None


def ask(
    notebook: Notebook,
    question: str,
    provider_id: Optional[str] = None,
    top_k: Optional[int] = None,
    max_tokens: int = 1024,
    client: Optional[LLMClient] = None,
    retriever: Optional[Retriever] = None,
    multihop: bool = False,
    use_bm25: Optional[bool] = None,
    bm25_alpha: float = 0.5,
    context_budget: int | None = 12000,
) -> AskResult:
    """Answer ``question`` grounded in ``notebook`` with validated citations.

    ``client``/``retriever`` are injectable for tests; when omitted they are
    built from the configured auth state. ``top_k=None`` picks adaptively
    (``engg_choices.md:E2``). ``use_bm25`` defaults to hybrid ON
    (``engg_choices.md:E1``); pass ``False`` to disable.
    When ``multihop=True``, runs planner → per-query workers → synthesizer
    (up to 3 sub-queries); falls back to single-shot on any planner failure.
    """
    if client is None:
        client = get_client(provider_id or default_provider())
    if retriever is None:
        r_kwargs: dict = {}
        if top_k is not None:
            r_kwargs["top_k"] = top_k
        if use_bm25 is not None:
            r_kwargs["use_bm25"] = use_bm25
        if bm25_alpha != 0.5:
            r_kwargs["bm25_alpha"] = bm25_alpha
        retriever = Retriever(notebook, **r_kwargs)

    if multihop:
        mh = _multihop(question, notebook, client, retriever, max_tokens, context_budget)
        if mh is not None:
            final_text, all_results, synth_prompt, synth_messages = mh
            answer = final_text.strip() or "sources don't contain this"
            from opennote.validation.citation import validate_freeform_answer

            chunk_map = {str(i + 1): r for i, r in enumerate(all_results)}
            if answer.lower() != "sources don't contain this" and all_results:
                if not validate_freeform_answer(answer, chunk_map):
                    answer = "sources don't contain this"
            footer, sources_used = used_sources(answer, all_results)
            if footer:
                answer = f"{answer}\n\n{footer}"
            usage = _track_usage(synth_prompt, synth_messages, answer, client, len(all_results), notebook)
            return AskResult(
                question=question,
                answer=answer,
                sources=sources_used,
                results=all_results,
                provider_id=client.provider_id,
                model=client.model,
                usage=usage,
            )
        # Fall through to single-shot on planner/synthesizer failure
        # (but still use single-shot's retriever.search(question) below).
        # Mark it: the caller asked for multihop, so say so on the answer.
        multihop_fallback = True
    else:
        multihop_fallback = False

    results = retriever.search(question)
    if not results:
        return AskResult(
            question=question,
            answer="I could not find any relevant sources in this notebook.",
            provider_id=client.provider_id,
            model=client.model,
        )

    answer, system, messages = _single_shot(question, results, notebook, client, max_tokens, context_budget)

    # Gate free-form through validator (defense 3)
    from opennote.validation.citation import validate_freeform_answer

    chunk_map = {str(i + 1): r for i, r in enumerate(results)}
    if answer.lower() != "sources don't contain this" and results:
        if not validate_freeform_answer(answer, chunk_map):
            answer = "sources don't contain this"
    footer, sources_used = used_sources(answer, results)
    if footer:
        answer = f"{answer}\n\n{footer}"
    if multihop_fallback:
        answer = f"{answer}\n\n*Note: multi-hop planner unavailable; answered single-shot.*"
    usage = _track_usage(system, messages, answer, client, len(results), notebook)
    return AskResult(
        question=question,
        answer=answer,
        sources=sources_used,
        results=results,
        provider_id=client.provider_id,
        model=client.model,
        usage=usage,
    )
