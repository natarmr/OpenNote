import json
import types

import pytest

from opennote.auth.registry import get_provider
from opennote.chat.client import AnthropicClient, ChatError, ChatResponse, OpenAICompatClient, ToolCall


class FakeOpenAIClient:
    def __init__(self):
        self.calls = []

    def chat_completions_create(self, **kwargs):
        self.calls.append(kwargs)
        message = types.SimpleNamespace(
            content="final text",
            tool_calls=[
                types.SimpleNamespace(
                    id="tc1",
                    function=types.SimpleNamespace(name="search", arguments='{"query": "kimi", "top_k": 3}'),
                )
            ],
        )
        return types.SimpleNamespace(choices=[types.SimpleNamespace(message=message)])


class FakeOpenAI:
    def __init__(self, *args, **kwargs):
        self.chat = types.SimpleNamespace(completions=None)
        fake = FakeOpenAIClient()
        self.chat.completions = types.SimpleNamespace(create=fake.chat_completions_create)
        self._fake = fake


class FakeAnthropicClient:
    def __init__(self):
        self.calls = []

    def messages_create(self, **kwargs):
        self.calls.append(kwargs)
        content = [
            types.SimpleNamespace(type="text", text="claude says "),
            types.SimpleNamespace(type="tool_use", id="tu1", name="search", input={"query": "x"}),
        ]
        return types.SimpleNamespace(content=content)


class FakeAnthropic:
    def __init__(self, *args, **kwargs):
        self._fake = FakeAnthropicClient()
        self.messages = types.SimpleNamespace(create=self._fake.messages_create)


@pytest.fixture
def oai_client():
    return OpenAICompatClient(get_provider("groq"), "gsk-x", "m", _client=FakeOpenAI())


@pytest.fixture
def ant_client():
    return AnthropicClient(get_provider("anthropic"), "sk-ant-x", "m", _client=FakeAnthropic())


def test_openai_chat_returns_content_and_tool_calls(oai_client):
    resp = oai_client.chat([{"role": "user", "content": "q"}], tools={"search": {"description": "d", "parameters": {}}})
    assert isinstance(resp, ChatResponse)
    assert resp.content == "final text"
    assert resp.tool_calls == [ToolCall(id="tc1", name="search", arguments={"query": "kimi", "top_k": 3})]


def test_openai_chat_builds_sdk_messages_and_tools(oai_client):
    messages = [
        {"role": "user", "content": "q"},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "t1", "name": "search", "arguments": {"query": "x"}}]},
        {"role": "tool", "tool_call_id": "t1", "content": "results"},
    ]
    oai_client.chat(messages, tools={"search": {"description": "d", "parameters": {"type": "object"}}})
    kwargs = oai_client._client._fake.calls[0]
    assert kwargs["messages"][0] == {"role": "user", "content": "q"}
    assert kwargs["messages"][1]["role"] == "assistant"
    assert kwargs["messages"][1]["tool_calls"][0]["function"]["arguments"] == json.dumps({"query": "x"})
    assert kwargs["messages"][2] == {"role": "tool", "tool_call_id": "t1", "content": "results"}
    assert kwargs["tools"][0] == {"type": "function", "function": {"name": "search", "description": "d", "parameters": {"type": "object"}}}


def test_openai_chat_system_prepended(oai_client):
    oai_client.chat([{"role": "user", "content": "q"}], system="SYS")
    assert oai_client._client._fake.calls[0]["messages"][0] == {"role": "system", "content": "SYS"}


def test_openai_chat_openai_provider_uses_max_completion_tokens():
    client = OpenAICompatClient(get_provider("openai"), "sk-x", "gpt-5.4", _client=FakeOpenAI())
    client.chat([{"role": "user", "content": "q"}])
    assert client._client._fake.calls[0]["max_completion_tokens"] == 1024


def test_anthropic_chat_returns_content_and_tool_calls(ant_client):
    resp = ant_client.chat([{"role": "user", "content": "q"}])
    assert resp.content == "claude says "
    assert resp.tool_calls == [ToolCall(id="tu1", name="search", arguments={"query": "x"})]


def test_anthropic_chat_merges_tool_results_into_user(ant_client):
    messages = [
        {"role": "user", "content": "q"},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "tu1", "name": "search", "arguments": {"query": "x"}}]},
        {"role": "tool", "tool_call_id": "tu1", "content": "result1"},
        {"role": "tool", "tool_call_id": "tu2", "content": "result2"},
    ]
    ant_client.chat(messages)
    kwargs = ant_client._client._fake.calls[0]
    user_msgs = [m for m in kwargs["messages"] if m["role"] == "user"]
    assert len(user_msgs) == 2
    assert user_msgs[1]["content"] == [
        {"type": "tool_result", "tool_use_id": "tu1", "content": "result1"},
        {"type": "tool_result", "tool_use_id": "tu2", "content": "result2"},
    ]
    assert kwargs["messages"][1]["content"][0]["type"] == "tool_use"


def test_anthropic_chat_system_and_tools_kwargs(ant_client):
    ant_client.chat(
        [{"role": "user", "content": "q"}],
        tools={"search": {"description": "d", "parameters": {"type": "object"}}},
        system="SYS",
    )
    kwargs = ant_client._client._fake.calls[0]
    assert kwargs["system"] == "SYS"
    assert kwargs["tools"] == [{"name": "search", "description": "d", "input_schema": {"type": "object"}}]


def test_openai_malformed_tool_arguments_salvaged():
    # Truncated / invalid tool-arguments JSON must not crash serialization (L17).
    class BadArgsClient:
        def __init__(self):
            self.calls = []

        def chat_completions_create(self, **kwargs):
            self.calls.append(kwargs)
            message = types.SimpleNamespace(
                content="",
                tool_calls=[types.SimpleNamespace(
                    id="tc1",
                    function=types.SimpleNamespace(name="search", arguments='{"query": "kimi", "top_k":'),
                )],
            )
            return types.SimpleNamespace(choices=[types.SimpleNamespace(message=message)])

    class FakeOpenAI2:
        def __init__(self, *args, **kwargs):
            fake = BadArgsClient()
            self.chat = types.SimpleNamespace(completions=types.SimpleNamespace(create=fake.chat_completions_create))
            self._fake = fake

    client = OpenAICompatClient(get_provider("groq"), "gsk-x", "m", _client=FakeOpenAI2())
    resp = client.chat([{"role": "user", "content": "q"}])
    assert resp.tool_calls[0].arguments["query"] == "kimi"


def test_anthropic_merges_consecutive_user_turns(ant_client):
    ant_client.chat([
        {"role": "user", "content": "first"},
        {"role": "user", "content": "second"},
    ])
    kwargs = ant_client._client._fake.calls[0]
    user_msgs = [m for m in kwargs["messages"] if m["role"] == "user"]
    assert len(user_msgs) == 1
    assert user_msgs[0]["content"] == [
        {"type": "text", "text": "first"},
        {"type": "text", "text": "second"},
    ]


def test_openai_unknown_role_raises_chat_error(oai_client):
    with pytest.raises(ChatError, match="Unknown message role"):
        oai_client.chat([{"role": "gremlin", "content": "x"}])