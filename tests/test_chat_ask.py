from opennote.chat.ask import ask
from opennote.retrieval.citations import citation_for
from opennote.retrieval.retriever import SearchResult


def _result(filename, content, pages="4-5"):
    meta = {"filename": filename, "chunk_id": "c", "pages": pages}
    return SearchResult(content=content, metadata=meta, similarity=0.5, citation=citation_for(meta))


class FakeRetriever:
    def __init__(self, results):
        self._results = results
        self.queries = []

    def search(self, query, **kwargs):
        self.queries.append(query)
        return list(self._results)


class FakeClient:
    def __init__(self, content, provider_id="groq", model="openai/gpt-oss-120b"):
        self._content = content
        self.provider_id = provider_id
        self.model = model
        self.calls = []

    def complete(self, system, messages, max_tokens=1024):
        self.calls.append((system, messages, max_tokens))
        return self._content


class StubNotebook:
    name = "stub"


def test_ask_appends_validated_sources_footer():
    results = [_result("a.pdf", "alpha"), _result("b.pdf", "beta")]
    client = FakeClient("The answer [2] then [1].")
    out = ask(
        StubNotebook(),
        "question?",
        client=client,
        retriever=FakeRetriever(results),
    )
    assert out.answer.startswith("The answer [2] then [1].")
    assert "Sources:" in out.answer
    assert "[b.pdf, p.4-5]" in out.answer
    assert [str(c) for c in out.sources] == ["[b.pdf, p.4-5]", "[a.pdf, p.4-5]"]
    assert out.provider_id == "groq"
    assert out.model == "openai/gpt-oss-120b"


def test_ask_grounds_on_retrieved_context():
    results = [_result("a.pdf", "alpha")]
    client = FakeClient("ok [1]")
    out = ask(StubNotebook(), "question?", client=client, retriever=FakeRetriever(results))
    system, messages, max_tokens = client.calls[0]
    assert "question?" in messages[0]["content"]
    assert '<source id="1"' in system
    assert "alpha" in system
    assert "ONLY" in system


def test_ask_no_results_returns_canned_answer():
    client = FakeClient("never called")
    out = ask(StubNotebook(), "q", client=client, retriever=FakeRetriever([]))
    assert "could not find any relevant sources" in out.answer
    assert client.calls == []


def test_ask_preserves_answer_without_markers():
    results = [_result("a.pdf", "alpha")]
    # Plain answer without citation and no overlap is gated to abstention
    client = FakeClient("Plain answer, no citations.")
    out = ask(StubNotebook(), "q", client=client, retriever=FakeRetriever(results))
    assert out.answer == "sources don't contain this"
    assert out.sources == []