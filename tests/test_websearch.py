"""Regression tests for Phase G websearch fixes (L42-L48)."""

import pytest

from opennote.websearch import (
    _DEFAULT_TOPIC,
    _is_safe_url,
    _page_title,
    _tavily_search,
    read_page,
    web_search,
)
from opennote.retrieval.citations import _pick_locator, citation_for


# --- L42: Tavily auth + request shape -------------------------------------


def test_tavily_search_sends_bearer_auth_and_valid_topic(monkeypatch):
    sent = {}

    class FakeResp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"type": "search", "results": [{"url": "https://x.dev", "title": "T", "content": "c"}]}

    def fake_post(url, json=None, headers=None, timeout=None):
        sent["url"] = url
        sent["json"] = json
        sent["headers"] = headers
        return FakeResp()

    import httpx

    monkeypatch.setattr(httpx, "post", fake_post)
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-test-key")
    results = _tavily_search("hello")
    assert sent["headers"]["Authorization"] == "Bearer tvly-test-key"
    assert sent["json"]["query"] == "hello"
    assert sent["json"]["topic"] == _DEFAULT_TOPIC
    assert sent["json"]["topic"] != "default"
    assert "api_key" not in sent["json"]
    assert len(results) == 1


def test_tavily_search_requires_key(monkeypatch):
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="TAVILY_API_KEY"):
        _tavily_search("hello")


def test_tavily_search_filters_non_dict_results(monkeypatch):
    class FakeResp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"results": [{"url": "u", "title": "t"}, "junk", None]}

    import httpx

    monkeypatch.setattr(httpx, "post", lambda *a, **k: FakeResp())
    monkeypatch.setenv("TAVILY_API_KEY", "k")
    assert len(_tavily_search("q")) == 1


# --- L43: SSRF guard -------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost:8000/",
        "http://127.0.0.1/",
        "http://0.0.0.0/",
        "http://[::1]/",
        "http://10.0.0.5/",
        "http://192.168.1.10/",
        "http://169.254.169.254/latest/meta-data/",
        "http://metadata.google.internal/",
        "file:///etc/passwd",
        "ftp://example.com/x",
        "http://",
        "http://notahost",  # hostname with no dot could be intranet — blocked via _is_private_ip? no, this is just malformed-ish
        "javascript:alert(1)",
    ],
)
def test_read_page_rejects_private_urls(url):
    with pytest.raises(RuntimeError, match="Refusing to fetch"):
        read_page(url)


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/",
        "http://example.com/a/b?q=1",
        "https://sub.example.com/path",
    ],
)
def test_is_safe_url_allows_public(url):
    assert _is_safe_url(url)


# --- L48: web citation locator ---------------------------------------------


def test_pick_locator_uses_url_hostname_and_title():
    loc = _pick_locator({"url": "https://example.com/page", "title": "Example Page"})
    assert loc == 'example.com, "Example Page"'


def test_pick_locator_falls_back_to_hostname_only():
    loc = _pick_locator({"url": "https://example.com/page"})
    assert loc == "example.com"


def test_pick_locator_prefers_page_over_url():
    loc = _pick_locator({"url": "https://example.com/page", "title": "T", "pages": "4-5"})
    assert loc == "p.4-5"


def test_citation_for_web_meta_uses_url_source():
    cit = citation_for({"url": "https://example.com/", "title": "T"})
    assert cit.source == "https://example.com/"
    assert "example.com" in cit.label


# --- L51: quick_search + web_search shape ----------------------------------


def test_web_search_missing_key_raises_cleanly(monkeypatch):
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="TAVILY_API_KEY"):
        web_search("anything")


def test_quick_search_aliases_web_search(monkeypatch):
    calls = []

    def fake_ws(q, top_k=5):
        calls.append((q, top_k))
        return []

    import opennote.websearch as ws

    monkeypatch.setattr(ws, "web_search", fake_ws)
    ws.quick_search("q", top_k=2)
    assert calls == [("q", 2)]


# --- L45: URL title derivation (never str.title() the URL) ------------------


def test_page_title_uses_host_and_path():
    assert _page_title("https://Example.com/Docs/Guide") == "Example.com — Docs/Guide"
    assert _page_title("https://example.com") == "example.com"
    assert _page_title("not a url") == "web"