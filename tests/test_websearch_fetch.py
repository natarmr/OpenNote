"""Web fetch path: does the content that was fetched actually reach the result?

The Tavily enrichment fetch has a **silent fallback**: if the fetch or the
chunking fails, ``web_search`` emits the bare Tavily snippet instead and only
records a ``logger.warning``. Under the TUI that logger is routed to a
``NullHandler``, so a permanently broken enrichment path looks exactly like a
working one -- just with thinner answers.

These tests therefore assert on the *result*, not on "the fetcher was called".
That distinction is not theoretical: ``c.meta`` did not exist on ``DocumentChunk``
(``:161-169``), so enrichment raised ``AttributeError`` on every call and never
once produced enriched text. Nothing caught it.
"""
import sys

import pytest

import opennote.websearch as ws

PAGE_HTML = """
<html><body>
<h1>Loopback Fixture Page</h1>
<p>The tensile strength of the composite reached 340 MPa under standard test
conditions in this fixture page body, which is deliberately much longer than the
short snippet Tavily would return so the two are easy to tell apart.</p>
</body></html>
"""


@pytest.fixture
def fake_tavily(monkeypatch):
    """A Tavily response whose snippet is deliberately distinguishable from page text."""
    results = [
        {
            "url": "https://example.com/page",
            "title": "Example Page",
            "content": "SHORT-SNIPPET",
        }
    ]
    monkeypatch.setattr(ws, "_tavily_search", lambda q, **kw: results)
    # Real socket work is not the subject here: the guard's DNS and redirect
    # behaviour is covered in test_websearch.py.
    monkeypatch.setattr(ws, "_host_resolves_public", lambda host: True)
    monkeypatch.setattr(ws, "_next_redirect", lambda url: None)
    return results


def _install_fake_trafilatura(monkeypatch, html=PAGE_HTML, raise_exc=None):
    calls = []

    class FakeTrafilatura:
        @staticmethod
        def fetch_url(url):
            calls.append(url)
            if raise_exc is not None:
                raise raise_exc
            return html

    monkeypatch.setitem(sys.modules, "trafilatura", FakeTrafilatura)
    return calls


# --- the guard: content must win over snippet -------------------------------


def test_enrichment_content_reaches_the_result(fake_tavily, monkeypatch):
    """If this fails, enrichment is dead and answers are silently thinner."""
    _install_fake_trafilatura(monkeypatch)
    out = ws.web_search("q", top_k=1)
    assert len(out) == 1
    content = out[0].content
    assert "SHORT-SNIPPET" not in content, "fell back to the bare Tavily snippet"
    assert "composite reached 340 MPa" in content
    assert out[0].metadata["title"] == "Example Page"
    assert out[0].metadata["fetched_at"]
    assert out[0].metadata["id"]


def test_enrichment_populates_the_citation_from_the_page(fake_tavily, monkeypatch):
    """The enriched chunk's own heading becomes the locator, not the Tavily title."""
    _install_fake_trafilatura(monkeypatch)
    out = ws.web_search("q", top_k=1)
    citation = str(out[0].citation)
    assert "Loopback Fixture Page" in citation, citation
    # filename/title supply the source label; the page heading supplies the locator.
    assert out[0].metadata["title"] == "Example Page"


def test_enrichment_does_not_leak_the_snippet(fake_tavily, monkeypatch):
    """Regression: the enriched path must not also append Tavily's raw content."""
    _install_fake_trafilatura(monkeypatch)
    out = ws.web_search("q", top_k=1)
    assert "SHORT-SNIPPET" not in out[0].content


# --- the fallback is intended, and distinguishable -------------------------


def test_fallback_to_snippet_when_fetch_raises(fake_tavily, monkeypatch):
    _install_fake_trafilatura(monkeypatch, raise_exc=RuntimeError("network down"))
    out = ws.web_search("q", top_k=1)
    assert len(out) == 1
    assert out[0].content == "SHORT-SNIPPET"
    assert out[0].metadata["url"] == "https://example.com/page"


def test_fallback_when_fetch_returns_nothing(fake_tavily, monkeypatch):
    _install_fake_trafilatura(monkeypatch, html=None)
    out = ws.web_search("q", top_k=1)
    assert out[0].content == "SHORT-SNIPPET"


def test_fallback_when_page_has_no_extractable_sections(fake_tavily, monkeypatch):
    """An empty page yields no chunks, which is also a fallback, not a crash."""
    _install_fake_trafilatura(monkeypatch, html="<html><body></body></html>")
    out = ws.web_search("q", top_k=1)
    assert out[0].content == "SHORT-SNIPPET"


def test_a_failing_fetch_is_never_silent_to_the_developer(fake_tavily, monkeypatch, caplog):
    """The silent part was the TUI's NullHandler, not the log call itself."""
    _install_fake_trafilatura(monkeypatch, raise_exc=RuntimeError("network down"))
    with caplog.at_level("WARNING"):
        ws.web_search("q", top_k=1)
    assert any("enrichment failed" in r.getMessage() for r in caplog.records)


# --- read_page --------------------------------------------------------------


def test_read_page_returns_extracted_text(monkeypatch):
    _install_fake_trafilatura(monkeypatch)
    out = ws.read_page("https://example.com/page")
    assert out
    assert any("composite reached 340 MPa" in r.content for r in out)


def test_read_page_raises_when_fetch_fails(monkeypatch):
    _install_fake_trafilatura(monkeypatch, html=None)
    with pytest.raises(RuntimeError, match="Failed to fetch URL"):
        ws.read_page("https://example.com/page")


# --- real redirect behaviour over a real socket ----------------------------
#
# test_websearch.py covers the guard's *logic* with a stubbed _next_redirect. This
# exercises the real httpx streaming path against a real 302 Location header, with
# only the DNS decision stubbed -- otherwise the test would validate the stub
# rather than the parser.


def test_real_redirect_is_followed_to_a_public_host(loopback_http, monkeypatch):
    target = loopback_http.route("/final", body=b"<html><body><p>arrived</p></body></html>")
    start = loopback_http.route("/start", status=302, headers={"Location": target})
    _install_fake_trafilatura(monkeypatch)

    # The fixture server is on 127.0.0.1, which the guard rejects by design, so
    # the textual/DNS verdict is stubbed to isolate redirect *parsing*.
    monkeypatch.setattr(ws, "_is_safe_url", lambda url: True)
    monkeypatch.setattr(ws, "_host_resolves_public", lambda host: True)

    assert ws._fetch_guarded(start) == target
    # Both hops are probed: _fetch_guarded reads the status line and headers of
    # every hop so it can validate the next one, which is why a public chain
    # costs one extra headers-only request. The body is never read here.
    assert loopback_http.requests == ["/start", "/final"]


def test_real_redirect_into_a_private_range_is_refused(loopback_http, monkeypatch):
    private = loopback_http.route("/admin")
    start = loopback_http.route("/start", status=302, headers={"Location": private})
    _install_fake_trafilatura(monkeypatch)

    real_is_safe = ws._is_safe_url

    def is_safe(url):
        # Everything is permitted except the loopback target itself.
        return url != private and real_is_safe(url)

    monkeypatch.setattr(ws, "_is_safe_url", is_safe)
    with pytest.raises(RuntimeError, match="Refusing to fetch"):
        ws._fetch_guarded(start)
    assert "/admin" not in loopback_http.requests, "the private hop must never be requested"


def test_real_non_redirect_response_terminates_the_chain(loopback_http, monkeypatch):
    start = loopback_http.route("/here", body=b"<html><body><p>stay</p></body></html>")
    monkeypatch.setattr(ws, "_is_safe_url", lambda url: True)
    monkeypatch.setattr(ws, "_host_resolves_public", lambda host: True)
    assert ws._fetch_guarded(start) == start
    assert loopback_http.requests == ["/here"]
