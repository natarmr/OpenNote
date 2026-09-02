import json

import pytest

from opennote.transcript import (
    append_messages,
    clear_transcript,
    load_transcript,
    save_transcript,
    trim_messages,
)


class StubNotebook:
    def __init__(self, directory):
        self.directory = directory
        self.updated = ""
        self.name = "test"

    def touch_updated(self):
        import datetime
        self.updated = datetime.datetime.now(datetime.timezone.utc).isoformat()


@pytest.fixture
def notebook(tmp_path):
    d = tmp_path / "nb"
    d.mkdir(parents=True, exist_ok=True)
    return StubNotebook(d)


def test_transcript_empty_initially(notebook):
    assert load_transcript(notebook) == []


def test_append_messages_persists(notebook):
    append_messages(notebook, [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello"},
    ])
    loaded = load_transcript(notebook)
    assert [m["role"] for m in loaded] == ["user", "assistant"]


def test_save_and_load_roundtrip(notebook):
    save_transcript(notebook, [{"role": "user", "content": "hello"}])
    loaded = load_transcript(notebook)
    assert loaded[0]["content"] == "hello"


def test_load_missing_returns_empty(notebook):
    # No transcript file
    assert load_transcript(notebook) == []


def test_load_corrupt_returns_empty(notebook, caplog):
    p = notebook.directory / "transcript.json"
    p.write_text("{not json", encoding="utf-8")
    result = load_transcript(notebook)
    assert result == []


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
    messages = [
        {"role": "tool", "tool_call_id": "x", "content": "orphan"},
        {"role": "user", "content": "keep me"},
    ]
    trimmed = trim_messages(messages, max_chars=1)
    assert trimmed[0]["role"] == "user"
    assert trimmed[-1]["content"] == "keep me"


def test_save_atomic_no_tmp_leftover(notebook):
    save_transcript(notebook, [{"role": "user", "content": "hi"}])
    append_messages(notebook, [{"role": "user", "content": "again"}])
    leftovers = [p for p in notebook.directory.iterdir() if p.suffix == ".tmp"]
    assert leftovers == []


def test_clear_transcript(notebook):
    append_messages(notebook, [{"role": "user", "content": "hi"}])
    clear_transcript(notebook)
    assert load_transcript(notebook) == []


def test_append_trims(notebook):
    # Large history should be trimmed on append
    big = [{"role": "user", "content": "x" * 50_000} for _ in range(5)]
    save_transcript(notebook, big)
    append_messages(notebook, [{"role": "user", "content": "keep me"}])
    loaded = load_transcript(notebook)
    assert loaded[-1]["content"] == "keep me"
