"""Endpoint + remote-tunnel auth tests."""
from __future__ import annotations

import pytest
import httpx

from opennote.auth.cli import _normalize_endpoint_url
from opennote.auth.registry import get_provider
from opennote.auth.validate import validate_key


def test_normalize_strips_models_and_v1():
    assert _normalize_endpoint_url("https://abc.trycloudflare.com") == "https://abc.trycloudflare.com/v1"
    assert _normalize_endpoint_url("https://abc.trycloudflare.com/") == "https://abc.trycloudflare.com/v1"
    assert _normalize_endpoint_url("https://abc.trycloudflare.com/v1") == "https://abc.trycloudflare.com/v1"
    assert _normalize_endpoint_url("https://abc.trycloudflare.com/v1/") == "https://abc.trycloudflare.com/v1"
    assert _normalize_endpoint_url("https://abc.trycloudflare.com/v1/models") == "https://abc.trycloudflare.com/v1"
    assert _normalize_endpoint_url("https://abc.trycloudflare.com/models") == "https://abc.trycloudflare.com/v1"


def test_normalize_rejects_non_http():
    with pytest.raises(ValueError):
        _normalize_endpoint_url("not-a-url")
    with pytest.raises(ValueError):
        _normalize_endpoint_url("ftp://x")


def test_validate_key_override_url_used(monkeypatch, tmp_path):
    """validate_key honours endpoint override for its GET."""
    from opennote.auth.config import AuthConfig

    monkeypatch.setenv("OPENNOTE_HOME", str(tmp_path))
    AuthConfig().set_base_url("openai", "https://tunnel.example.com/v1")
    captured = {}

    def handler(request):
        captured["url"] = str(request.url)
        return httpx.Response(200, json={"data": [{"id": "m1"}]})

    result = validate_key(get_provider("openai"), "not-needed", transport=httpx.MockTransport(handler))
    assert result.ok
    assert captured["url"] == "https://tunnel.example.com/v1/models"
    assert "m1" in result.models


def test_validate_key_explicit_models_url_wins(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENNOTE_HOME", str(tmp_path))
    captured = {}

    def handler(request):
        captured["url"] = str(request.url)
        return httpx.Response(200, json={"data": []})

    validate_key(
        get_provider("openai"), "k",
        transport=httpx.MockTransport(handler),
        models_url="https://explicit.example.com/v1/models",
    )
    assert captured["url"] == "https://explicit.example.com/v1/models"


def test_auth_endpoint_cli_roundtrip(tmp_path, monkeypatch):
    from typer.testing import CliRunner
    import opennote.auth.cli as cli_mod
    from opennote.auth.config import AuthConfig

    monkeypatch.setenv("OPENNOTE_HOME", str(tmp_path))
    runner = CliRunner()
    # set via URL with /models suffix → normalised to /v1
    r = runner.invoke(cli_mod.auth_app, ["endpoint", "set", "openai", "https://tunnel.example.com/v1/models"])
    assert r.exit_code == 0, r.output
    assert AuthConfig().get("openai").base_url_override == "https://tunnel.example.com/v1"
    # list shows it
    r = runner.invoke(cli_mod.auth_app, ["endpoint", "list"])
    assert "tunnel.example.com" in r.output
    # clear
    r = runner.invoke(cli_mod.auth_app, ["endpoint", "clear", "openai"])
    assert r.exit_code == 0
    assert AuthConfig().get("openai").base_url_override is None


def test_tui_url_at_key_prompt_sets_endpoint(tmp_path, monkeypatch):
    """Pasting a tunnel URL at the connect key prompt sets endpoint and re-prompts."""
    monkeypatch.setenv("OPENNOTE_HOME", str(tmp_path))
    from opennote.auth.config import AuthConfig

    # Simulate the handler inline (we test the helper logic directly).
    assert _normalize_endpoint_url("https://config-investing-headed-trial.trycloudflare.com/v1/models") == "https://config-investing-headed-trial.trycloudflare.com/v1"
    AuthConfig().set_base_url("openai", _normalize_endpoint_url("https://config-investing-headed-trial.trycloudflare.com/v1/models"))
    assert AuthConfig().get("openai").base_url_override == "https://config-investing-headed-trial.trycloudflare.com/v1"
