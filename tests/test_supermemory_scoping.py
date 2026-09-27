"""Supermemory container scoping.

The write path used to compute its container tag from an ``AskResult`` that had no
``notebook`` attribute, so every turn was written to the default ``opennote``
container while ``memory_search`` read from ``opennote-<notebook>``. Nothing
OpenNote stored was ever retrievable.

These tests pin the invariant that was broken: **the tag used to write must equal
the tag used to read for the same notebook.** The tag is captured off the wire
(both paths POST a ``containerTag``) rather than asserted on a helper, so a
divergence anywhere in the chain fails here.
"""
import os
from typing import ClassVar

import pytest

import opennote.plugins.builtin.supermemory as sm
from opennote.chat.ask import AskResult


class _FakeResponse:
    def __init__(self, payload=None, status_code=200):
        self._payload = payload if payload is not None else {}
        self.status_code = status_code

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class _RecordingClient:
    """Stands in for ``httpx.Client``; records every POSTed payload."""

    posted: ClassVar[list] = []
    response_payload: ClassVar[dict] = {"results": []}

    def __init__(self, *a, **kw):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def post(self, url, json=None, headers=None):
        type(self).posted.append({"url": url, "json": json})
        return _FakeResponse(type(self).response_payload)

    @classmethod
    def reset(cls, response_payload=None):
        cls.posted = []
        cls.response_payload = response_payload if response_payload is not None else {"results": []}

    @classmethod
    def tags(cls):
        return [p["json"].get("containerTag") for p in cls.posted if p["json"]]


@pytest.fixture
def fake_supermemory(monkeypatch):
    import httpx

    _RecordingClient.reset()
    monkeypatch.setattr(httpx, "Client", _RecordingClient)
    monkeypatch.setenv("SUPERMEMORY_API_KEY", "test-key")
    monkeypatch.delenv("SUPERMEMORY_CONTAINER_TAG", raising=False)
    return _RecordingClient


class _FakeNotebook:
    def __init__(self, name):
        self.name = name


# --- the invariant that was broken -----------------------------------------


def test_read_and_write_container_tags_match(fake_supermemory):
    """A stored turn must be findable by a later read in the same notebook."""
    from opennote.agents.tools import ToolContext

    ctx = ToolContext(notebook=_FakeNotebook("alpha"))

    sm._memory_search_tool(ctx, "what did we decide?")
    sm._on_turn_complete(AskResult(question="q", answer="a real grounded answer", notebook="alpha"))

    tags = fake_supermemory.tags()
    assert len(tags) == 2, tags
    assert tags[0] == tags[1], f"read tag {tags[0]!r} != write tag {tags[1]!r}"
    assert tags[0] == "opennote-alpha"


def test_notebook_object_and_name_string_agree():
    """ToolContext carries a Notebook; AskResult carries its name. Both must map alike."""
    from opennote.agents.tools import ToolContext

    ctx = ToolContext(notebook=_FakeNotebook("beta"))
    from_ctx = sm._container_tag_for(ctx)
    from_result = sm._container_tag_for(AskResult(question="q", answer="a", notebook="beta"))
    assert from_ctx == from_result == "opennote-beta"


def test_notebooks_do_not_share_a_container(fake_supermemory):
    sm._on_turn_complete(AskResult(question="q", answer="a", notebook="alpha"))
    sm._on_turn_complete(AskResult(question="q", answer="a", notebook="beta"))
    assert fake_supermemory.tags() == ["opennote-alpha", "opennote-beta"]


# --- fallbacks --------------------------------------------------------------


def test_env_var_is_fallback_only_when_no_notebook(monkeypatch):
    monkeypatch.setenv("SUPERMEMORY_CONTAINER_TAG", "custom-tag")
    assert sm._container_tag_for(None) == "custom-tag"
    # An explicit notebook still wins, so read and write cannot diverge.
    assert sm._container_tag_for(AskResult(question="q", answer="a", notebook="alpha")) == "opennote-alpha"


def test_default_container_when_nothing_is_known(monkeypatch):
    monkeypatch.delenv("SUPERMEMORY_CONTAINER_TAG", raising=False)
    assert sm._container_tag_for(None) == sm._DEFAULT_CONTAINER
    assert sm._container_tag_for(AskResult(question="q", answer="a")) == sm._DEFAULT_CONTAINER


def test_notebook_name_is_stripped(monkeypatch):
    """AskResult.notebook is a name, so a stray space would create a phantom container."""
    assert sm._container_tag_for(AskResult(question="q", answer="a", notebook="  alpha  ")) == "opennote-alpha"


# --- write path guards (unchanged behaviour, pinned) ------------------------


def test_write_is_skipped_without_a_question_or_answer(fake_supermemory):
    sm._on_turn_complete(AskResult(question="", answer="a", notebook="alpha"))
    sm._on_turn_complete(AskResult(question="q", answer="", notebook="alpha"))
    assert fake_supermemory.posted == []


def test_abstention_is_never_stored(fake_supermemory):
    sm._on_turn_complete(
        AskResult(question="q", answer="sources don't contain this", notebook="alpha")
    )
    assert fake_supermemory.posted == []


def test_stored_payload_carries_the_turn(fake_supermemory):
    sm._on_turn_complete(AskResult(question="why?", answer="because 340 MPa", notebook="alpha"))
    posted = fake_supermemory.posted[0]["json"]
    assert posted["content"] == "Q: why?\nA: because 340 MPa"
    assert posted["containerTag"] == "opennote-alpha"


def test_api_base_override_is_not_a_notebook_concern():
    """SUPERMEMORY_API_BASE only redirects the endpoint; scoping is independent."""
    os.environ["SUPERMEMORY_API_BASE"] = "http://localhost:9"
    try:
        assert sm._api_base() == "http://localhost:9"
    finally:
        os.environ.pop("SUPERMEMORY_API_BASE", None)
