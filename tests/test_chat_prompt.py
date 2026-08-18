from opennote.chat.prompt import SYSTEM_TEMPLATE, build_context, build_user_message
from opennote.retrieval.citations import Citation, citation_for
from opennote.retrieval.retriever import SearchResult


def _result(filename, content, pages="4-5"):
    meta = {"filename": filename, "chunk_id": "c1", "pages": pages}
    return SearchResult(
        content=content, metadata=meta, similarity=0.5, citation=citation_for(meta)
    )


def test_build_context_numbers_and_cites():
    results = [_result("a.pdf", "alpha"), _result("b.txt", "beta")]
    context = build_context(results)
    assert "[1] [a.pdf, p.4-5]\nalpha" in context
    assert "[2] [b.txt, p.4-5]\nbeta" in context


def test_build_user_message_includes_question():
    msg = build_user_message("the question", "CONTEXT")
    assert "the question" in msg
    assert "CONTEXT" in msg


def test_system_template_requires_grounding_and_citations():
    assert "ONLY the provided context" in SYSTEM_TEMPLATE
    assert "[n]" in SYSTEM_TEMPLATE
    assert "could not find" in SYSTEM_TEMPLATE