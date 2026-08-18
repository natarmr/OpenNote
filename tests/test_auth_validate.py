import httpx
import pytest

from opennote.auth.registry import get_provider
from opennote.auth.validate import validate_key


def _transport(handler):
    return httpx.MockTransport(handler)


def test_valid_key_returns_models():
    def handler(request):
        return httpx.Response(200, json={"data": [{"id": "gpt-4o"}, {"id": "whisper-1"}]})

    result = validate_key(get_provider("openai"), "sk-test", transport=_transport(handler))
    assert result.ok
    assert result.error is None
    assert result.models == ["gpt-4o", "whisper-1"]


def test_invalid_key_401():
    def handler(request):
        return httpx.Response(401, json={"error": "bad key"})

    result = validate_key(get_provider("openai"), "sk-bad", transport=_transport(handler))
    assert not result.ok
    assert result.error == "invalid-key"


def test_invalid_key_403():
    def handler(request):
        return httpx.Response(403, json={})

    result = validate_key(get_provider("groq"), "gsk-bad", transport=_transport(handler))
    assert result.error == "invalid-key"


def test_http_error_classified():
    def handler(request):
        return httpx.Response(500, json={})

    result = validate_key(get_provider("openai"), "sk-x", transport=_transport(handler))
    assert not result.ok
    assert result.error == "http-500"


def test_network_error_classified():
    def handler(request):
        raise httpx.ConnectError("connection refused")

    result = validate_key(get_provider("openai"), "sk-x", transport=_transport(handler))
    assert not result.ok
    assert result.error == "network"


def test_openai_bearer_header_sent():
    captured = {}

    def handler(request):
        captured["auth"] = request.headers.get("Authorization")
        return httpx.Response(200, json={"data": []})

    validate_key(get_provider("openai"), "sk-test", transport=_transport(handler))
    assert captured["auth"] == "Bearer sk-test"


def test_anthropic_headers_sent():
    captured = {}

    def handler(request):
        captured["key"] = request.headers.get("x-api-key")
        captured["version"] = request.headers.get("anthropic-version")
        return httpx.Response(200, json={"data": [{"id": "claude-sonnet-5"}]})

    result = validate_key(get_provider("anthropic"), "sk-ant-x", transport=_transport(handler))
    assert result.ok
    assert captured["key"] == "sk-ant-x"
    assert captured["version"] == "2023-06-01"


def test_hits_models_url():
    captured = {}

    def handler(request):
        captured["url"] = str(request.url)
        return httpx.Response(200, json={"data": []})

    validate_key(get_provider("cerebras"), "csk-x", transport=_transport(handler))
    assert captured["url"] == "https://api.cerebras.ai/v1/models"


def test_key_is_stripped():
    captured = {}

    def handler(request):
        captured["auth"] = request.headers.get("Authorization")
        return httpx.Response(200, json={"data": []})

    validate_key(get_provider("openai"), "  sk-test  ", transport=_transport(handler))
    assert captured["auth"] == "Bearer sk-test"


def test_html_error_page_200_malformed():
    def handler(request):
        return httpx.Response(200, text="<html><body>We are sorry...</body></html>")

    result = validate_key(get_provider("openai"), "sk-x", transport=_transport(handler))
    assert not result.ok
    assert result.error == "http-200"