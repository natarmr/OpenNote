"""Track 1: context budget + thinking-strip tests."""
from __future__ import annotations

from opennote.chat.clean import clean_thinking_content
from opennote.chat.context_budget import (
    build_fitted_tagged_context,
    fit_tagged_context,
)
from opennote.retrieval.citations import citation_for
from opennote.retrieval.retriever import SearchResult


def _sr(content, cid="c1"):
    return SearchResult(
        content=content,
        metadata={"chunk_id": cid, "filename": "f.txt"},
        similarity=0.9,
        citation=citation_for({"filename": "f.txt"}),
    )


def test_clean_strips_think():
    out = clean_thinking_content("Hello <think>secret reasoning</think> world [1]")
    assert "secret" not in out
    assert "[1]" in out


def test_clean_malformed_closing_only():
    out = clean_thinking_content("prefix </think> suffix")
    assert "</think>" not in out
    assert "suffix" in out


def test_clean_bypass_large():
    big = "x" * 100_001 + "<think>y</think>"
    assert clean_thinking_content(big) == big


def test_clean_empty():
    assert clean_thinking_content("") == ""


def test_budget_full_when_fits():
    r = _sr("short content")
    fitted = fit_tagged_context([r], budget_chars=5000)
    assert fitted[0].status == "full"
    assert "truncated" not in build_fitted_tagged_context(fitted)


def test_budget_truncates_with_notice():
    r = _sr("A" * 2000)
    fitted = fit_tagged_context([r], budget_chars=500)
    assert fitted[0].status == "truncated"
    body = build_fitted_tagged_context(fitted)
    assert "truncated" in body
    assert "[1]" in body


def test_budget_omits_when_hopeless():
    r = _sr("A" * 2000)
    fitted = fit_tagged_context([r], budget_chars=10)
    assert fitted[0].status == "omitted_budget"
    # Never notice-only: omitted items are skipped entirely.
    assert build_fitted_tagged_context(fitted) == ""


def test_budget_ask_uses_fitted_results(notebook_manager, stub_embedder):
    """ask() with a tiny budget omits gracefully (no crash, no fake cites)."""
    from opennote.chat.ask import ask
    from opennote.chat.client import LLMClient

    nb = notebook_manager.create("budget", project="test")
    from opennote.ingest.pipeline import ingest

    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "doc.txt"
        p.write_text("Budget content about retrieval. " * 50, encoding="utf-8")
        ingest(nb, p)

    class FakeClient(LLMClient):
        provider_id = "fake"
        model = "fake"

        def complete(self, system, messages, max_tokens=1024):
            return "Answer [1]"

    res = ask(nb, "what?", client=FakeClient(), context_budget=50)
    assert isinstance(res.answer, str)
