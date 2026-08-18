from opennote.chat.citations import used_sources
from opennote.retrieval.citations import citation_for
from opennote.retrieval.retriever import SearchResult


def _result(filename, pages):
    meta = {"filename": filename, "chunk_id": "c", "pages": pages}
    return SearchResult(content="x", metadata=meta, similarity=0.5, citation=citation_for(meta))


def test_used_sources_maps_markers_in_order():
    results = [_result("a.pdf", "1"), _result("b.pdf", "2")]
    answer = "Claim [1] and more [2] and again [1]."
    footer, used = used_sources(answer, results)
    assert str(used[0]) == "[a.pdf, p.1]"
    assert str(used[1]) == "[b.pdf, p.2]"
    assert footer == "Sources:\n  [1] [a.pdf, p.1]\n  [2] [b.pdf, p.2]"


def test_used_sources_dedupes_markers():
    results = [_result("a.pdf", "1")]
    footer, used = used_sources("[1] ... [1] again", results)
    assert len(used) == 1


def test_used_sources_ignores_out_of_range_markers():
    results = [_result("a.pdf", "1")]
    footer, used = used_sources("Claim [1] but not [9] or [0] or [42]", results)
    assert len(used) == 1
    assert "9" not in footer


def test_used_sources_empty_when_no_markers():
    results = [_result("a.pdf", "1")]
    footer, used = used_sources("No citations here", results)
    assert footer == ""
    assert used == []


def test_used_sources_requires_real_chunk():
    results = [_result("a.pdf", "1")]
    footer, used = used_sources("Hallucinated [7]", results)
    assert footer == ""
    assert used == []


def test_used_sources_matches_bracket_variants():
    results = [_result("a.pdf", "1"), _result("b.pdf", "2"), _result("c.pdf", "3")]
    answer = "A【1†L1-L3】 B(2) C[^3] D[2]"
    footer, used = used_sources(answer, results)
    assert [str(c) for c in used] == ["[a.pdf, p.1]", "[b.pdf, p.2]", "[c.pdf, p.3]"]
    assert "[a.pdf, p.1]" in footer


def test_used_sources_ignores_decimal_numbers():
    results = [_result("a.pdf", "1"), _result("b.pdf", "2")]
    footer, used = used_sources("It is (2.8) trillion.", results)
    assert used == []


def test_parenthesized_prose_not_citation():
    results = [_result("a.pdf", "1")]
    footer, used = used_sources("Revenue grew by (2) percentage points last quarter.", results)
    assert used == []
    assert footer == ""


def test_parenthesized_marker_requires_immediate_close():
    results = [_result("a.pdf", "1")]
    footer, used = used_sources("See (1) for the detail.", results)
    assert [str(c) for c in used] == ["[a.pdf, p.1]"]


def test_same_citation_deduped_across_indices():
    # [1] and [2] point at two chunks of the same page → one footer line (L23).
    results = [_result("a.pdf", "1"), _result("a.pdf", "1")]
    footer, used = used_sources("One [1] and two [2] both.", results)
    assert len(used) == 1
    assert footer.count("[a.pdf, p.1]") == 1