"""Tests for the context meter (engg_choices.md:E12)."""
from opennote.context_meter import (
    ContextUsage,
    cost_for,
    context_limit_for,
    estimate_tokens,
    summarize,
)


def test_estimate_tokens_chars_over_four():
    assert estimate_tokens("abcd" * 100) == 100
    assert estimate_tokens("") == 0


def test_context_limit_known_and_default():
    assert context_limit_for("openai/gpt-oss-120b") == 131072
    assert context_limit_for("something-unknown-xyz") == 128000


def test_cost_unknown_model_is_zero_not_wrong():
    assert cost_for("mystery-model", 10000, 1000) == 0.0
    assert cost_for("gpt-4o-mini", 1000, 0) > 0


def test_render_matches_panel_format():
    # exact (provider-reported) matches the opencode screenshot verbatim
    u2 = ContextUsage(input_tokens=191045, output_tokens=0, limit=262144, session_spent=0.0, exact=True)
    text = u2.render()
    assert text.startswith("Context\n")
    assert "191,045 tokens" in text
    assert "73% used" in text  # 191045/262144 = 72.9%
    assert "$0.00 spent" in text
    assert u2.total == 191045
    # estimated renders with ~ prefix (honest about being approximate)
    u3 = ContextUsage(input_tokens=191045, output_tokens=0, limit=262144, session_spent=0.0, exact=False)
    assert "~191,045 tokens" in u3.render()
    # compact one-liner for the persistent prompt-bar readout
    assert "ctx 191,045/262,144 73% $0.00" in u2.render_compact()
    assert u2.render_compact().startswith("ctx ")


def test_summarize_attaches_chunks_and_model():
    u = summarize("system text here", [{"role": "user", "content": "hello question"}], "answer [1]", model="gpt-oss-120b", chunks=3)
    assert u.chunks == 3
    assert u.model == "gpt-oss-120b"
    assert u.input_tokens > 0
    assert u.output_tokens > 0
    assert u.exact is False  # no provider usage → estimate


def test_summarize_prefers_provider_usage():
    from opennote.chat.client import TokenUsage

    u = summarize("x" * 4000, [{"role": "user", "content": "y" * 400}], "answer", model="gpt-oss-120b",
                  provider_usage=TokenUsage(prompt_tokens=191000, completion_tokens=45))
    assert u.exact is True
    assert u.input_tokens == 191000
    assert u.output_tokens == 45
    assert "191,045 tokens" in u.render()  # no ~ prefix


def test_token_usage_adds():
    from opennote.chat.client import TokenUsage

    a = TokenUsage(prompt_tokens=100, completion_tokens=20)
    b = TokenUsage(prompt_tokens=50, completion_tokens=5)
    assert (a + b).total == 175


def test_openai_usage_extracted_and_zero_rejected():
    from opennote.chat.client import _openai_usage
    from types import SimpleNamespace

    assert _openai_usage(SimpleNamespace(usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5))).total == 15
    assert _openai_usage(SimpleNamespace(usage=None)) is None
    assert _openai_usage(SimpleNamespace(usage=SimpleNamespace(prompt_tokens=0, completion_tokens=0))) is None
    assert _openai_usage(object()) is None


def test_ask_attaches_usage(tmp_path, monkeypatch):
    """ask() returns usage; spend accumulates in notebook dir."""
    import opennote.context_meter as meter
    from opennote.chat.ask import ask
    from opennote.retrieval.citations import citation_for
    from opennote.retrieval.retriever import SearchResult

    spent_file = tmp_path / "usage.json"
    monkeypatch.setattr(meter, "_usage_file", lambda _nb=None: spent_file)

    meta = {"filename": "a.pdf", "chunk_id": "c", "pages": "1"}
    results = [SearchResult(content="alpha", metadata=meta, similarity=0.5, citation=citation_for(meta))]

    class FakeRetriever:
        def search(self, query, **kwargs):
            return list(results)

    class FakeClient:
        provider_id = "groq"
        model = "openai/gpt-oss-120b"

        def complete(self, system, messages, max_tokens=1024):
            return "alpha [1]"

    class StubNotebook:
        name = "stub"
        project = ""
        sources: list = []
        directory = tmp_path

    out = ask(StubNotebook(), "q?", client=FakeClient(), retriever=FakeRetriever())
    assert out.usage is not None
    assert out.usage.total > 0
    assert out.usage.chunks == 1
    assert spent_file.exists()


def test_ask_uses_provider_usage_when_reported(tmp_path, monkeypatch):
    """Client-reported usage wins over the estimate (exact=True)."""
    import opennote.context_meter as meter
    from opennote.chat.ask import ask
    from opennote.chat.client import TokenUsage
    from opennote.retrieval.citations import citation_for
    from opennote.retrieval.retriever import SearchResult

    spent_file = tmp_path / "usage.json"
    monkeypatch.setattr(meter, "_usage_file", lambda _nb=None: spent_file)

    meta = {"filename": "a.pdf", "chunk_id": "c", "pages": "1"}
    results = [SearchResult(content="alpha", metadata=meta, similarity=0.5, citation=citation_for(meta))]

    class FakeRetriever:
        def search(self, query, **kwargs):
            return list(results)

    class ReportingClient:
        provider_id = "groq"
        model = "openai/gpt-oss-120b"
        last_usage = None

        def complete(self, system, messages, max_tokens=1024):
            self.last_usage = TokenUsage(prompt_tokens=1000, completion_tokens=50)
            return "alpha [1]"

    class StubNotebook:
        name = "stub"
        project = ""
        sources: list = []
        directory = tmp_path

    out = ask(StubNotebook(), "q?", client=ReportingClient(), retriever=FakeRetriever())
    assert out.usage is not None
    assert out.usage.exact is True
    assert out.usage.total == 1050
    assert "1,050 tokens" in out.usage.render()
