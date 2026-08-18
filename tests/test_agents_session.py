import pytest

from opennote.agents.session import (
    append_messages,
    list_sessions,
    load_session,
    new_session,
    save_session,
    trim_messages,
)
from opennote.chat.client import ChatError


class StubNotebook:
    def __init__(self, directory):
        self.directory = directory


@pytest.fixture
def notebook(tmp_path):
    return StubNotebook(tmp_path / "nb")


def test_new_session_saved_and_loadable(notebook):
    session = new_session(notebook, "groq", "openai/gpt-oss-120b")
    loaded = load_session(notebook, session["id"])
    assert loaded is not None
    assert loaded["provider_id"] == "groq"
    assert loaded["model"] == "openai/gpt-oss-120b"
    assert loaded["messages"] == []


def test_append_messages_persists(notebook):
    session = new_session(notebook, "groq", "m")
    append_messages(notebook, session["id"], [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello"},
    ])
    loaded = load_session(notebook, session["id"])
    assert [m["role"] for m in loaded["messages"]] == ["user", "assistant"]


def test_append_unknown_session_raises(notebook):
    with pytest.raises(ChatError, match="does not exist"):
        append_messages(notebook, "nope", [{"role": "user", "content": "x"}])


def test_list_sessions_sorted_by_updated_desc(notebook):
    a = new_session(notebook, "groq", "m")
    b = new_session(notebook, "groq", "m")
    append_messages(notebook, a["id"], [{"role": "user", "content": "newer"}])
    sessions = list_sessions(notebook)
    assert sessions[0]["id"] == a["id"]
    assert {s["id"] for s in sessions} == {a["id"], b["id"]}


def test_load_missing_returns_none(notebook):
    assert load_session(notebook, "nope") is None


def test_load_corrupt_returns_none(notebook):
    new_session(notebook, "groq", "m")
    (notebook.directory / "sessions").mkdir(exist_ok=True)
    bad = notebook.directory / "sessions" / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    assert load_session(notebook, "bad") is None


def test_trim_messages_keeps_newest_within_budget():
    messages = [
        {"role": "user", "content": "x" * 60_000},
        {"role": "user", "content": "y" * 60_000},
        {"role": "user", "content": "keep me"},
    ]
    trimmed = trim_messages(messages, max_chars=100_000)
    assert trimmed[-1]["content"] == "keep me"
    assert sum(len(m["content"]) for m in trimmed) <= 100_000


def test_trim_messages_never_empty():
    messages = [{"role": "user", "content": "x" * 10_000}]
    assert trim_messages(messages, max_chars=100) == messages


def test_trim_messages_never_orphans_tool():
    # A tool exchange at the head of an oversized history: the first survivor
    # must be a 'user' message, never a lone 'tool' or a dangling
    # assistant-with-tool_calls (both 400 on OpenAI/Anthropic).
    messages = [
        {"role": "user", "content": "a" * 40_000},
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "t1", "name": "search", "arguments": {"query": "x"}}
        ]},
        {"role": "tool", "tool_call_id": "t1", "content": "y" * 40_000},
        {"role": "user", "content": "keep me"},
    ]
    trimmed = trim_messages(messages, max_chars=60_000)
    assert trimmed[0]["role"] == "user"
    assert trimmed[-1]["content"] == "keep me"


def test_trim_messages_skips_leading_orphan_tool():
    # Defensive: corrupt persisted state starting with a 'tool' message.
    messages = [
        {"role": "tool", "tool_call_id": "x", "content": "orphan"},
        {"role": "user", "content": "keep me"},
    ]
    trimmed = trim_messages(messages, max_chars=1)
    assert trimmed[0]["role"] == "user"
    assert trimmed[-1]["content"] == "keep me"


def test_save_session_atomic_no_tmp_leftover(notebook):
    session = new_session(notebook, "groq", "m")
    append_messages(notebook, session["id"], [{"role": "user", "content": "hi"}])
    leftovers = [p for p in (notebook.directory / "sessions").iterdir() if p.suffix == ".tmp"]
    assert leftovers == []


def test_load_corrupt_warns(notebook, caplog):
    (notebook.directory / "sessions").mkdir(parents=True, exist_ok=True)
    bad = notebook.directory / "sessions" / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    with caplog.at_level("WARNING", logger="opennote.agents.session"):
        assert load_session(notebook, "bad") is None
    assert any("corrupt" in r.message for r in caplog.records)


def test_save_session_roundtrip(notebook):
    session = new_session(notebook, "groq", "m")
    session["model"] = "other"
    save_session(notebook, session)
    assert load_session(notebook, session["id"])["model"] == "other"