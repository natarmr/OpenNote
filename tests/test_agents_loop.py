import pytest

from opennote.agents.loop import agent_turn
from opennote.chat.client import ChatResponse, ToolCall
from opennote.retrieval.citations import citation_for
from opennote.retrieval.retriever import SearchResult


class ScriptedClient:
    """Emits a fixed sequence of ChatResponses, then inspects what was sent."""

    def __init__(self, responses, provider_id="groq", model="m"):
        self._responses = list(responses)
        self.provider_id = provider_id
        self.model = model
        self.sent = []

    def chat(self, messages, tools=None, system=None, max_tokens=1024):
        self.sent.append({"messages": list(messages), "tools": tools, "system": system})
        return self._responses.pop(0)


class FakeRetriever:
    def __init__(self, results=None, sources=None):
        self._results = results or []
        self._sources = sources or []
        self.calls = []

    def search(self, query, top_k=None, source=None):
        self.calls.append({"query": query, "top_k": top_k})
        return list(self._results)

    def sources(self):
        return list(self._sources)


class StubNotebook:
    name = "stub"


def _result(filename, content, pages="2"):
    meta = {"filename": filename, "chunk_id": "c", "pages": pages}
    return SearchResult(content=content, metadata=meta, similarity=0.5, citation=citation_for(meta))


def test_direct_answer_no_tools():
    client = ScriptedClient([ChatResponse(content="Answer [1].")])
    out = agent_turn(
        StubNotebook(), "q", client=client, retriever=FakeRetriever(results=[_result("a.pdf", "a")])
    )
    # No tool was called, so nothing was retrieved to ground the citation.
    assert out.result.answer == "Answer [1]."
    assert out.result.sources == []
    assert out.result.provider_id == "groq"
    assert len(client.sent) == 1


def test_tool_call_then_answer():
    client = ScriptedClient(
        [
            ChatResponse(
                content="",
                tool_calls=[ToolCall(id="t1", name="search", arguments={"query": "kimi", "top_k": 3})],
            ),
            ChatResponse(content="Kimi is [1]."),
        ]
    )
    retriever = FakeRetriever(results=[_result("k3.pdf", "kimi k3")])
    out = agent_turn(StubNotebook(), "q", client=client, retriever=retriever)
    assert out.result.answer.startswith("Kimi is [1].")
    assert "[k3.pdf, p.2]" in out.result.answer
    assert retriever.calls[0]["query"] == "kimi"
    # history: user, assistant(tool_calls), tool, assistant(final)
    roles = [m["role"] for m in out.messages]
    assert roles == ["user", "assistant", "tool", "assistant"]
    assert out.messages[1]["tool_calls"][0]["name"] == "search"


def test_multiple_tool_calls_in_one_round():
    client = ScriptedClient(
        [
            ChatResponse(
                content="",
                tool_calls=[
                    ToolCall(id="t1", name="search", arguments={"query": "a"}),
                    ToolCall(id="t2", name="list_sources", arguments={}),
                ],
            ),
            ChatResponse(content="Found [1] in sources."),
        ]
    )
    retriever = FakeRetriever(results=[_result("a.pdf", "x")], sources=["a.pdf", "b.pdf"])
    out = agent_turn(StubNotebook(), "q", client=client, retriever=retriever)
    tool_msgs = [m for m in out.messages if m["role"] == "tool"]
    assert len(tool_msgs) == 2
    assert "a.pdf" in tool_msgs[1]["content"]


def test_tool_error_does_not_kill_turn():
    client = ScriptedClient(
        [
            ChatResponse(content="", tool_calls=[ToolCall(id="t1", name="search", arguments={"query": "x", "source": "nope"})]),
            ChatResponse(content="Retried."),
        ]
    )

    class BoomRetriever:
        def search(self, query, top_k=None, source=None):
            raise RuntimeError("boom")

        def sources(self):
            return []

    out = agent_turn(StubNotebook(), "q", client=client, retriever=BoomRetriever())
    tool_msg = [m for m in out.messages if m["role"] == "tool"][0]
    assert "boom" in tool_msg["content"]
    assert out.result.answer == "Retried."


def test_budget_exhaustion_returns_forced_answer():
    search_call = ChatResponse(
        content="",
        tool_calls=[ToolCall(id="t", name="search", arguments={"query": "q"})],
    )
    client = ScriptedClient([search_call] * 5)
    out = agent_turn(StubNotebook(), "q", client=client, retriever=FakeRetriever(results=[]), max_rounds=5)
    assert "without being able to answer" in out.result.answer


def test_history_is_passed_through():
    client = ScriptedClient([ChatResponse(content="Replied.")])
    history = [{"role": "user", "content": "earlier"}, {"role": "assistant", "content": "earlier answer"}]
    out = agent_turn(StubNotebook(), "q2", client=client, retriever=FakeRetriever(results=[]), history=history)
    sent = client.sent[0]["messages"]
    assert [m["content"] for m in sent[:2]] == ["earlier", "earlier answer"]
    assert sent[-1]["content"] == "q2"


def test_tools_and_system_sent():
    client = ScriptedClient([ChatResponse(content="ok")])
    agent_turn(StubNotebook(), "q", client=client, retriever=FakeRetriever(results=[]))
    sent = client.sent[0]
    assert sent["tools"] is not None
    assert "search" in sent["tools"]
    assert sent["system"] and "ONLY" in sent["system"]


def test_provider_rejection_corrects_and_retries():
    class FlakyClient:
        def __init__(self):
            self.provider_id = "groq"
            self.model = "m"
            self.attempts = 0

        def chat(self, messages, tools=None, system=None, max_tokens=1024):
            self.attempts += 1
            if self.attempts == 1:
                raise RuntimeError("attempted to call tool 'open_file' which was not in request.tools")
            return ChatResponse(content="Fixed answer.")

    client = FlakyClient()
    out = agent_turn(StubNotebook(), "q", client=client, retriever=FakeRetriever(results=[]))
    assert client.attempts == 2
    assert out.result.answer == "Fixed answer."
    correction = [m for m in out.messages if "rejected" in m.get("content", "")]
    assert correction and "search, list_sources" in correction[0]["content"]


def test_network_error_propagates():
    class BoomClient:
        provider_id = "groq"
        model = "m"

        def chat(self, messages, tools=None, system=None, max_tokens=1024):
            raise TimeoutError("gateway timeout")

    with pytest.raises(TimeoutError):
        agent_turn(StubNotebook(), "q", client=BoomClient(), retriever=FakeRetriever(results=[]))


def test_rounds_used_reported():
    client = ScriptedClient([ChatResponse(content="final")])
    out = agent_turn(StubNotebook(), "q", client=client, retriever=FakeRetriever(results=[]))
    assert out.rounds_used == 1

    client2 = ScriptedClient([
        ChatResponse(content="", tool_calls=[ToolCall(id="t", name="search", arguments={"query": "q"})]),
        ChatResponse(content="final2"),
    ])
    out2 = agent_turn(StubNotebook(), "q", client=client2, retriever=FakeRetriever(results=[]))
    assert out2.rounds_used == 2


def test_two_searches_numbered_globally():
    # The model searches twice in one turn; citation indices must refer to the
    # flat accumulated list, not restart at [1] per search call (L16).
    client = ScriptedClient([
        ChatResponse(content="", tool_calls=[ToolCall(id="a", name="search", arguments={"query": "first"})]),
        ChatResponse(content="", tool_calls=[ToolCall(id="b", name="search", arguments={"query": "second"})]),
        ChatResponse(content="Second search [2] answered."),
    ])
    retriever = FakeRetriever(results=[_result("a.pdf", "aaa"), _result("b.pdf", "bbb")])
    out = agent_turn(StubNotebook(), "q", client=client, retriever=retriever)
    second_tool_msg = [m for m in out.messages if m["role"] == "tool"][1]
    assert "[3] [a.pdf, p.2]" in second_tool_msg["content"], "second search must offset past the first"
    assert "[4] [b.pdf, p.2]" in second_tool_msg["content"]
    assert "[2] [b.pdf, p.2]" in out.result.answer


def test_consecutive_user_messages_merged():
    # A bad-request correction followed by a fresh question must not produce
    # back-to-back user turns (a hard Anthropic error) (L15).
    class FlakyClient:
        def __init__(self):
            self.provider_id = "groq"
            self.model = "m"
            self.attempts = 0

        def chat(self, messages, tools=None, system=None, max_tokens=1024):
            self.attempts += 1
            if self.attempts == 1:
                raise RuntimeError("invalid_request_error: bad tool call")
            return ChatResponse(content="ok")

    out = agent_turn(StubNotebook(), "q", client=FlakyClient(), retriever=FakeRetriever(results=[]))
    roles = [m["role"] for m in out.messages]
    assert roles.count("user") == 1
    assert out.messages[0]["role"] == "user"


def test_should_cancel_raises_before_first_round():
    from opennote.agents.loop import TurnCancelled

    client = ScriptedClient([ChatResponse(content="never")])
    with pytest.raises(TurnCancelled):
        agent_turn(
            StubNotebook(),
            "q",
            client=client,
            retriever=FakeRetriever(results=[]),
            should_cancel=lambda: True,
        )


def test_should_cancel_after_model_call():
    # Cancel set mid-turn (e.g. double-esc) is honoured as soon as the model
    # returns, not only at the next round boundary.
    from opennote.agents.loop import TurnCancelled

    state = {"cancel": False}

    class FlipClient(ScriptedClient):
        def chat(self, messages, tools=None, system=None, max_tokens=1024):
            state["cancel"] = True
            return super().chat(messages, tools=tools, system=system)

    client = FlipClient([ChatResponse(content="answer")])
    with pytest.raises(TurnCancelled):
        agent_turn(
            StubNotebook(),
            "q",
            client=client,
            retriever=FakeRetriever(results=[]),
            should_cancel=lambda: state["cancel"],
        )


def test_on_round_reports_progress():
    rounds = []
    client = ScriptedClient(
        [
            ChatResponse(
                content="",
                tool_calls=[ToolCall(id="t", name="search", arguments={"query": "x"})],
            ),
            ChatResponse(content="done [1]."),
        ]
    )
    agent_turn(
        StubNotebook(),
        "q",
        client=client,
        retriever=FakeRetriever(results=[_result("a.pdf", "a")]),
        on_round=lambda used, total: rounds.append((used, total)),
    )
    assert rounds == [(1, 5), (2, 5)]