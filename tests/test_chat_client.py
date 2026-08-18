import json
import types

import pytest

import opennote.chat.client as client_mod
from opennote.chat.client import (
    AnthropicClient,
    ChatError,
    OpenAICompatClient,
    default_provider,
    get_client,
)
from opennote.auth.registry import get_provider


class FakeCompletions:
    def __init__(self, content="fake answer"):
        self._content = content
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return types.SimpleNamespace(
            choices=[types.SimpleNamespace(message=types.SimpleNamespace(content=self._content))]
        )


class FakeChat:
    def __init__(self, content="fake answer"):
        self.completions = FakeCompletions(content)


class FakeOpenAIClient:
    def __init__(self, *args, **kwargs):
        self.chat = FakeChat()


class FakeMessages:
    def __init__(self, blocks=None):
        self.blocks = blocks or [types.SimpleNamespace(type="text", text="hi from claude")]
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return types.SimpleNamespace(content=self.blocks)


class FakeAnthropicClient:
    def __init__(self, blocks=None):
        self.messages = FakeMessages(blocks)


def test_openai_compat_sends_system_and_user():
    provider = get_provider("groq")
    client = OpenAICompatClient(provider, "gsk-x", "openai/gpt-oss-120b", _client=FakeOpenAIClient())
    out = client.complete("sys", [{"role": "user", "content": "hi"}])
    assert out == "fake answer"
    call = client._client.chat.completions.calls[0]
    assert call["model"] == "openai/gpt-oss-120b"
    assert call["messages"][0] == {"role": "system", "content": "sys"}
    assert call["messages"][1] == {"role": "user", "content": "hi"}


def test_groq_uses_max_tokens_kwarg():
    provider = get_provider("groq")
    client = OpenAICompatClient(provider, "gsk-x", "openai/gpt-oss-120b", _client=FakeOpenAIClient())
    client.complete("s", [{"role": "user", "content": "q"}], max_tokens=512)
    call = client._client.chat.completions.calls[0]
    assert call["max_tokens"] == 512


def test_openai_uses_max_completion_tokens():
    provider = get_provider("openai")
    client = OpenAICompatClient(provider, "sk-x", "gpt-5.4", _client=FakeOpenAIClient())
    client.complete("s", [{"role": "user", "content": "q"}])
    call = client._client.chat.completions.calls[0]
    assert call["max_completion_tokens"] == 1024
    assert "max_tokens" not in call


def test_base_url_defaults_to_registry():
    provider = get_provider("groq")
    client = OpenAICompatClient(provider, "gsk-x", "m", _client=FakeOpenAIClient())
    assert client.base_url == "https://api.groq.com/openai/v1"


def test_base_url_override():
    provider = get_provider("groq")
    client = OpenAICompatClient(provider, "gsk-x", "m", base_url="http://localhost:9999/v1", _client=FakeOpenAIClient())
    assert client.base_url == "http://localhost:9999/v1"


def test_anthropic_system_separate_and_roles_filtered():
    provider = get_provider("anthropic")
    client = AnthropicClient(provider, "sk-ant-x", "claude-sonnet-5", _client=FakeAnthropicClient())
    out = client.complete(
        "sys", [{"role": "system", "content": "ignored"}, {"role": "user", "content": "hi"}]
    )
    assert out == "hi from claude"
    call = client._client.messages.calls[0]
    assert call["system"] == "sys"
    assert call["max_tokens"] == 1024
    assert call["messages"] == [{"role": "user", "content": "hi"}]


def test_anthropic_joins_text_blocks():
    provider = get_provider("anthropic")
    blocks = [
        types.SimpleNamespace(type="text", text="part1 "),
        types.SimpleNamespace(type="tool_use", text="ignored"),
        types.SimpleNamespace(type="text", text="part2"),
    ]
    client = AnthropicClient(provider, "k", "m", _client=FakeAnthropicClient(blocks))
    assert client.complete("s", []) == "part1 part2"


@pytest.fixture
def configured_auth(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir(parents=True)
    (home / "auth.json").write_text(
        json.dumps({"groq": {"model": "openai/gpt-oss-120b"}}), encoding="utf-8"
    )
    monkeypatch.setenv("OPENNOTE_HOME", str(home))
    monkeypatch.setattr(client_mod, "resolve_key", lambda pid: "fake-key" if pid == "groq" else None)
    return home


def test_get_client_builds_from_config(configured_auth):
    client = get_client("groq")
    assert client.provider_id == "groq"
    assert client.model == "openai/gpt-oss-120b"
    assert client.base_url == "https://api.groq.com/openai/v1"


def test_get_client_missing_key_raises(configured_auth):
    with pytest.raises(ChatError, match="No API key for OpenAI"):
        get_client("openai")


def test_get_client_missing_model_raises(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir(parents=True)
    (home / "auth.json").write_text("{}", encoding="utf-8")
    monkeypatch.setenv("OPENNOTE_HOME", str(home))
    monkeypatch.setattr(client_mod, "resolve_key", lambda pid: "fake-key")
    with pytest.raises(ChatError, match="No model selected"):
        get_client("groq")


def test_get_client_unknown_provider_raises(configured_auth):
    with pytest.raises(ValueError, match="Unknown provider"):
        get_client("nope")


def test_default_provider_alphabetical_first(configured_auth, monkeypatch):
    monkeypatch.setattr(client_mod, "resolve_key", lambda pid: "fake-key")
    assert default_provider() == "groq"


def test_default_provider_none_raises(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir(parents=True)
    (home / "auth.json").write_text("{}", encoding="utf-8")
    monkeypatch.setenv("OPENNOTE_HOME", str(home))
    with pytest.raises(ChatError, match="No provider is configured"):
        default_provider()