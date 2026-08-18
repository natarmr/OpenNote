import pytest

from opennote.agents.tools import TOOL_SCHEMAS, execute_tool, render_tool_results
from opennote.retrieval.citations import citation_for
from opennote.retrieval.retriever import SearchResult


class FakeRetriever:
    def __init__(self, results=None, sources=None):
        self._results = results or []
        self._sources = sources or []
        self.calls = []

    def search(self, query, top_k=None, source=None):
        self.calls.append({"query": query, "top_k": top_k, "source": source})
        return list(self._results)

    def sources(self):
        return list(self._sources)


def _result(filename="a.pdf", content="alpha"):
    meta = {"filename": filename, "chunk_id": "c", "pages": "2"}
    return SearchResult(content=content, metadata=meta, similarity=0.5, citation=citation_for(meta))


def test_schemas_have_both_tools():
    assert set(TOOL_SCHEMAS) == {"search", "list_sources"}
    assert "query" in TOOL_SCHEMAS["search"]["parameters"]["required"]
    assert TOOL_SCHEMAS["search"]["parameters"]["properties"]["query"]["type"] == "string"


def test_execute_search_passes_retriever_args():
    retriever = FakeRetriever(results=[_result()])
    out = execute_tool("search", retriever, {"query": "hello", "top_k": 3})
    assert retriever.calls[0]["query"] == "hello"
    assert retriever.calls[0]["top_k"] == 3
    assert retriever.calls[0]["source"] is None
    assert len(out) == 1


def test_execute_search_source_filter():
    retriever = FakeRetriever(results=[_result()])
    execute_tool("search", retriever, {"query": "q", "source": "b.pdf"})
    assert retriever.calls[0]["source"] == "b.pdf"


def test_execute_search_rejects_non_int_top_k():
    retriever = FakeRetriever(results=[_result()])
    with pytest.raises(ValueError, match="top_k"):
        execute_tool("search", retriever, {"query": "q", "top_k": "five"})


def test_execute_search_rejects_zero_negative_and_huge_top_k():
    retriever = FakeRetriever(results=[_result()])
    with pytest.raises(ValueError, match="top_k"):
        execute_tool("search", retriever, {"query": "q", "top_k": 0})
    with pytest.raises(ValueError, match="top_k"):
        execute_tool("search", retriever, {"query": "q", "top_k": -3})
    with pytest.raises(ValueError, match="top_k"):
        execute_tool("search", retriever, {"query": "q", "top_k": 999})


def test_execute_search_rejects_empty_query():
    retriever = FakeRetriever(results=[_result()])
    with pytest.raises(ValueError, match="query"):
        execute_tool("search", retriever, {"query": "   "})


def test_execute_search_unknown_source_raises():
    retriever = FakeRetriever(results=[_result()], sources=["a.pdf"])
    with pytest.raises(ValueError, match="a.pdf"):
        execute_tool("search", retriever, {"query": "q", "source": "missing.pdf"})


def test_execute_search_unknown_kwargs_dropped():
    retriever = FakeRetriever(results=[_result()])
    out = execute_tool("search", retriever, {"query": "q", "nonsense": 1, "top_k": 2})
    assert len(out) == 1
    assert "nonsense" not in retriever.calls[0]


def test_execute_search_kwargs_none_missing_required():
    retriever = FakeRetriever(results=[_result()])
    with pytest.raises(ValueError, match="query"):
        execute_tool("search", retriever, None)


def test_execute_list_sources():
    retriever = FakeRetriever(sources=["a.pdf", "b.pdf"])
    assert execute_tool("list_sources", retriever, {}) == ["a.pdf", "b.pdf"]


def test_execute_missing_required_arg_raises():
    retriever = FakeRetriever(results=[_result()])
    with pytest.raises(ValueError, match="'query'"):
        execute_tool("search", retriever, {})


def test_execute_unknown_tool_raises():
    with pytest.raises(ValueError, match="Unknown tool"):
        execute_tool("nope", FakeRetriever(), {})


def test_render_tool_results_numbers_and_cites():
    text = render_tool_results([_result("a.pdf", "alpha"), _result("b.pdf", "beta")])
    assert "[1] [a.pdf, p.2]" in text
    assert "[2] [b.pdf, p.2]" in text
    assert "alpha" in text
    assert "beta" in text


def test_render_tool_results_truncates_long_chunks():
    long = _result("a.pdf", "\n".join(f"line{i}" for i in range(10)))
    text = render_tool_results([long], max_lines=3)
    assert "+7 more lines" in text