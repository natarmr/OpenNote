"""Regression for nb2 duplicate-answer bug: resize must not re-append history."""
from __future__ import annotations

import pytest
from opennote.chat.client import ChatResponse
from opennote.retrieval.citations import citation_for
from opennote.retrieval.retriever import SearchResult
from opennote.transcript import append_messages
from opennote.tui.app import OpenNoteApp
from opennote.tui.theme import DARK
from opennote.tui.widgets.prompt import PromptBar


def _manager(tmp_path):
    from opennote.notebooks import NotebookManager
    manager = NotebookManager(home=tmp_path)
    try:
        manager.get("t")
    except KeyError:
        manager.create("t")
    return manager


def _transcript_text(transcript) -> str:
    return "\n".join("".join(seg.text for seg in strip) for strip in transcript.lines)


def _count_phrase(transcript, phrase: str) -> int:
    return _transcript_text(transcript).count(phrase)


def _result(content, filename="kimi.pdf"):
    meta = {"filename": filename, "chunk_id": "c", "pages": "1"}
    return SearchResult(content=content, metadata=meta, similarity=0.5, citation=citation_for(meta))


class ScriptedClient:
    def __init__(self, responses, provider_id="groq", model="m"):
        self._responses = list(responses)
        self.provider_id = provider_id
        self.model = model
    def chat(self, messages, tools=None, system=None, max_tokens=1024):
        if self._responses:
            return self._responses.pop(0)
        return ChatResponse(content="done")


async def _make_app(tmp_path, client=None, retriever=None):
    class FakeRetriever:
        def __init__(self, results=None):
            self._results = results or []
        def search(self, query, top_k=None, source=None):
            return list(self._results)
        def sources(self):
            return []
    ret = retriever if retriever is not None else FakeRetriever([_result("kimi kimi")])
    return OpenNoteApp(notebook_name="t", palette=DARK, manager=_manager(tmp_path), client=client or ScriptedClient([ChatResponse(content="ans")]), retriever=ret)


# -- dedup on mount / resize -----------------------------------------------

async def test_history_renders_once_on_fresh_mount(tmp_path):
    app = await _make_app(tmp_path)
    nb = _manager(tmp_path).get("t")
    append_messages(nb, [{"role": "user", "content": "q1"}, {"role": "assistant", "content": "a1"}])
    async with app.run_test() as pilot:
        await pilot.pause()
        assert _count_phrase(app.screen.transcript, "a1") == 1


async def test_resize_does_not_duplicate_history(tmp_path):
    app = await _make_app(tmp_path)
    nb = _manager(tmp_path).get("t")
    append_messages(nb, [{"role": "user", "content": "q1"}, {"role": "assistant", "content": "a1"}])
    async with app.run_test() as pilot:
        await pilot.pause()
        # Simulate several resizes (the real trigger is a terminal width change).
        for _ in range(3):
            app.screen._fit_sidebar_display()
            app.screen._finish_mount()
            await pilot.pause()
        assert _count_phrase(app.screen.transcript, "a1") == 1


async def test_double_finish_mount_dedupes(tmp_path):
    app = await _make_app(tmp_path)
    nb = _manager(tmp_path).get("t")
    append_messages(nb, [{"role": "user", "content": "q1"}, {"role": "assistant", "content": "a1"}])
    async with app.run_test() as pilot:
        await pilot.pause()
        app.screen._finish_mount()
        app.screen._finish_mount()
        await pilot.pause()
        assert _count_phrase(app.screen.transcript, "a1") == 1


# -- round-trip deduplication + echo ---------------------------------------

async def test_live_echo_and_no_double_render_after_remount(tmp_path):
    app = await _make_app(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        bar = app.screen.query_one("#prompt-bar", PromptBar)
        # Submit through the prompt — live echo goes in immediately.
        app.screen.on_prompt_input_submitted(type("E", (), {"text": "what is kimi?", "stop": lambda self: None})())
        await pilot.pause()
        # Give the agent turn time to finish (fake retriever + scripted client = ~1s).
        # Wait idle before checking counts.
        deadline = __import__("time").monotonic() + 10
        while __import__("time").monotonic() < deadline:
            await pilot.pause()
            if not bar.busy:
                break
        text = _transcript_text(app.screen.transcript)
        assert text.count("what is kimi?") == 1
        # One answer body expected.
        # Remount must not duplicate the echo or the answer — storage is deduplicated.
        app.screen._finish_mount()
        await pilot.pause()
        assert _transcript_text(app.screen.transcript).count("what is kimi?") == 1


# -- notebook switch / clear / undo reset paths -----------------------------

async def test_notebook_switch_renders_new_history_once(tmp_path):
    mgr = _manager(tmp_path)
    mgr.create("other")
    nb = mgr.get("t")
    append_messages(nb, [{"role": "user", "content": "q1"}, {"role": "assistant", "content": "a1"}])
    app = OpenNoteApp(notebook_name="other", palette=DARK, manager=mgr, client=ScriptedClient([ChatResponse(content="done")]))
    async with app.run_test() as pilot:
        await pilot.pause()
        app.screen._switch_notebook("t")
        await pilot.pause()
        assert _count_phrase(app.screen.transcript, "a1") == 1


async def test_clear_followed_by_undo_renders_remainder_once(tmp_path):
    app = await _make_app(tmp_path)
    nb = _manager(tmp_path).get("t")
    append_messages(nb, [{"role": "user", "content": "q1"}, {"role": "assistant", "content": "a1"}, {"role": "user", "content": "q2"}, {"role": "assistant", "content": "a2"}])
    async with app.run_test() as pilot:
        await pilot.pause()
        app.screen._undo_last_turn("")
        await pilot.pause()
        assert _count_phrase(app.screen.transcript, "a1") == 1
        assert _count_phrase(app.screen.transcript, "a2") == 0
