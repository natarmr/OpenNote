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


# --- Wave 2: host canonicalization (the guard used to fail OPEN here) --------

# Every one of these was ACCEPTED by the pre-Wave-2 guard: the root label defeats
# both the exact-membership and the suffix tuple, and ipaddress declines the
# non-canonical numeric forms, which the platform resolver still maps to loopback.
CANONICALIZATION_BYPASSES = [
    "http://localhost./",
    "http://127.0.0.1./",
    "http://metadata.google.internal./",
    "http://metadata.aws.internal./",
    "http://127.1/",
    "http://0177.0.0.1/",
    "http://127.0.0.1..:8080/",
    "http://LOCALHOST./admin",
]


@pytest.mark.parametrize("url", CANONICALIZATION_BYPASSES)
def test_is_safe_url_rejects_canonicalization_bypasses(url):
    assert not _is_safe_url(url), url


@pytest.mark.parametrize("url", CANONICALIZATION_BYPASSES)
def test_read_page_rejects_canonicalization_bypasses(url):
    with pytest.raises(RuntimeError, match="Refusing to fetch"):
        read_page(url)


def test_non_canonical_numeric_host_is_the_fail_closed_branch(monkeypatch):
    """Pin the premise: ipaddress declines these, so the guard must not pass them."""
    import ipaddress

    for host in ("127.1", "0177.0.0.1", "127.0.0.1."):
        with pytest.raises(ValueError):
            ipaddress.ip_address(host)
    from opennote.websearch import _is_private_ip

    assert _is_private_ip("127.1") is True
    assert _is_private_ip("0177.0.0.1") is True
    # A real hostname is not numeric, so it is settled by the DNS check instead.
    assert _is_private_ip("example.com") is False


def test_host_resolving_private_is_refused(monkeypatch):
    import socket

    import opennote.websearch as ws

    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("169.254.169.254", 0))],
    )
    assert ws._host_resolves_public("evil.example.com") is False


def test_unresolvable_host_is_left_to_the_fetcher(monkeypatch):
    import socket

    import opennote.websearch as ws

    def boom(*a, **k):
        raise socket.gaierror("nope")

    monkeypatch.setattr(socket, "getaddrinfo", boom)
    assert ws._host_resolves_public("example.com") is True


# --- Wave 2: the guard is bound to the fetch primitive ----------------------


def test_fetch_guarded_rejects_private_url():
    from opennote.websearch import _fetch_guarded

    with pytest.raises(RuntimeError, match="Refusing to fetch"):
        _fetch_guarded("http://127.0.0.1/")


def test_fetch_guarded_refuses_redirect_into_private_range(monkeypatch):
    """trafilatura follows redirects inside one urllib3 call, so we walk the chain."""
    import opennote.websearch as ws

    hops = {"https://public.example/": "http://127.0.0.1:8080/admin"}
    monkeypatch.setattr(ws, "_next_redirect", lambda url: hops.get(url))
    monkeypatch.setattr(ws, "_host_resolves_public", lambda host: True)
    with pytest.raises(RuntimeError, match="Refusing to fetch"):
        ws._fetch_guarded("https://public.example/")


def test_fetch_guarded_follows_a_public_chain(monkeypatch):
    import opennote.websearch as ws

    hops = {
        "https://a.example/": "/b",
        "https://a.example/b": "https://c.example/final",
        "https://c.example/final": None,
    }
    monkeypatch.setattr(ws, "_next_redirect", lambda url: hops.get(url))
    monkeypatch.setattr(ws, "_host_resolves_public", lambda host: True)
    assert ws._fetch_guarded("https://a.example/") == "https://c.example/final"


def test_fetch_guarded_refuses_non_http_redirect(monkeypatch):
    import opennote.websearch as ws

    monkeypatch.setattr(ws, "_next_redirect", lambda url: "file:///etc/passwd")
    monkeypatch.setattr(ws, "_host_resolves_public", lambda host: True)
    with pytest.raises(RuntimeError, match="non-http"):
        ws._fetch_guarded("https://a.example/")


def test_fetch_guarded_caps_redirect_hops(monkeypatch):
    import opennote.websearch as ws

    counter = {"n": 0}

    def forever(url):
        counter["n"] += 1
        return f"https://a.example/{counter['n']}"

    monkeypatch.setattr(ws, "_next_redirect", forever)
    monkeypatch.setattr(ws, "_host_resolves_public", lambda host: True)
    with pytest.raises(RuntimeError, match="Too many redirects"):
        ws._fetch_guarded("https://a.example/")


def test_enrichment_fetch_goes_through_the_guard(monkeypatch):
    """Lead 4.2: a Tavily `url` field must not reach the fetcher unvalidated."""
    import opennote.websearch as ws

    fetched = []

    class FakeTrafilatura:
        @staticmethod
        def fetch_url(url):
            fetched.append(url)
            return None  # force the bare-Tavily fallback so no parsing is needed

    monkeypatch.setattr(ws, "_tavily_search", lambda q, **k: [{"url": "http://127.0.0.1/", "content": "c"}])
    monkeypatch.setitem(__import__("sys").modules, "trafilatura", FakeTrafilatura)
    ws.web_search("q")
    assert fetched == [], "the private URL must be refused before the fetcher is called"


def test_enrichment_fetch_of_public_url_is_still_attempted(monkeypatch):
    import opennote.websearch as ws

    fetched = []

    class FakeTrafilatura:
        @staticmethod
        def fetch_url(url):
            fetched.append(url)
            return None

    monkeypatch.setattr(
        ws, "_tavily_search", lambda q, **k: [{"url": "https://example.com/", "content": "c"}]
    )
    monkeypatch.setattr(ws, "_next_redirect", lambda url: None)
    monkeypatch.setattr(ws, "_host_resolves_public", lambda host: True)
    monkeypatch.setitem(__import__("sys").modules, "trafilatura", FakeTrafilatura)
    ws.web_search("q")
    assert fetched == ["https://example.com/"]


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