"""Wave 3 TUI: cached retrievers, worker-based sources, worker validation."""
from __future__ import annotations

import time
import types

from opennote.retrieval.citations import citation_for
from opennote.retrieval.retriever import SearchResult
from opennote.tui.app import OpenNoteApp
from opennote.tui.dialogs import ItemListDialog
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


class _StubClient:
    provider_id = "groq"
    model = "m"


def _result(content, filename="doc.pdf"):
    meta = {"filename": filename, "chunk_id": "c", "pages": "1"}
    return SearchResult(content=content, metadata=meta, similarity=0.5, citation=citation_for(meta))


class FakeRetriever:
    def __init__(self, results=None):
        self._results = results or []

    def search(self, query, top_k=None, source=None):
        return list(self._results)

    def sources(self):
        return sorted({r.metadata["filename"] for r in self._results})


def _transcript_text(transcript) -> str:
    return "\n".join("".join(seg.text for seg in strip) for strip in transcript.lines)


async def _wait_idle(pilot, bar, timeout=10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        await pilot.pause()
        if not bar.busy:
            return
    raise AssertionError("prompt bar never went idle")


def _make_app(tmp_path, retriever=None):
    return OpenNoteApp(
        notebook_name="t", palette=DARK, manager=_manager(tmp_path),
        client=_StubClient(), retriever=retriever,
    )


# -- retriever session cache --------------------------------------------------

async def test_get_retriever_reuses_instance(tmp_path, monkeypatch):
    import opennote.retrieval.retriever as ret_mod

    built = []

    class CountingRet:
        def __init__(self, nb, **kw):
            built.append(kw)

    monkeypatch.setattr(ret_mod, "Retriever", CountingRet)
    app = _make_app(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        first = app.screen._get_retriever(top_k=8)
        second = app.screen._get_retriever(top_k=8)
        assert first is second
        assert len(built) == 1
        app.screen._invalidate_retrievers()
        third = app.screen._get_retriever(top_k=8)
        assert third is not first
        assert len(built) == 2


# -- /sources worker ----------------------------------------------------------

async def test_list_sources_worker_success(tmp_path):
    app = _make_app(tmp_path, retriever=FakeRetriever([_result("x", "a.pdf"), _result("y", "b.pdf")]))
    async with app.run_test() as pilot:
        await pilot.pause()
        bar = app.screen.query_one("#prompt-bar", PromptBar)
        app.screen._list_sources("")
        await _wait_idle(pilot, bar)
        text = _transcript_text(app.screen.transcript)
        assert "a.pdf" in text and "b.pdf" in text


async def test_list_sources_worker_failure(tmp_path):
    class BoomRet(FakeRetriever):
        def sources(self):
            raise RuntimeError("store down")

    app = _make_app(tmp_path, retriever=BoomRet([]))
    async with app.run_test() as pilot:
        await pilot.pause()
        bar = app.screen.query_one("#prompt-bar", PromptBar)
        app.screen._list_sources("")
        await _wait_idle(pilot, bar)
        assert "store down" in _transcript_text(app.screen.transcript)


# -- connect / switch-model validation worker ---------------------------------

def _fake_provider():
    return types.SimpleNamespace(
        id="groq", label="Groq", env_var="GROQ_API_KEY",
        preferred_models=["m1", "m2"], excluded_models=(),
    )


async def test_connect_validation_off_ui_thread(tmp_path, monkeypatch):
    import opennote.auth.registry as reg_mod
    import opennote.auth.validate as val_mod

    monkeypatch.setattr(reg_mod, "get_provider", lambda pid: _fake_provider())
    monkeypatch.setattr(
        val_mod, "validate_key",
        lambda provider, key: types.SimpleNamespace(ok=True, models=["live-m"], error=None),
    )
    app = _make_app(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        bar = app.screen.query_one("#prompt-bar", PromptBar)
        app.screen._open_connect_model(_fake_provider(), key="k")
        assert bar.busy  # busy indicator set synchronously, network off-thread
        await _wait_idle(pilot, bar)
        assert isinstance(app.screen, ItemListDialog)
        await pilot.press("escape")
        await pilot.pause()


async def test_switch_model_validation_worker(tmp_path, monkeypatch):
    import opennote.auth.registry as reg_mod
    import opennote.auth.validate as val_mod

    monkeypatch.setattr(reg_mod, "get_provider", lambda pid: _fake_provider())
    monkeypatch.setattr(
        val_mod, "validate_key",
        lambda provider, key: types.SimpleNamespace(ok=True, models=["live-m"], error=None),
    )
    app = _make_app(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        bar = app.screen.query_one("#prompt-bar", PromptBar)
        app.screen._open_model_dialog("")
        await _wait_idle(pilot, bar)
        assert isinstance(app.screen, ItemListDialog)
        await pilot.press("escape")
        await pilot.pause()


# -- sidebar slow-section TTL -------------------------------------------------

async def test_sidebar_services_cached_across_syncs(tmp_path, monkeypatch):
    import opennote.skills.registry as reg_mod

    calls = []
    real = reg_mod.SkillRegistry.discover

    @classmethod
    def counting(cls, cwd=None):
        calls.append(1)
        return real()

    monkeypatch.setattr(reg_mod.SkillRegistry, "discover", counting)
    app = _make_app(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        app.screen._sync_sidebar(model="m", pid="groq")
        await pilot.pause()
        app.screen._sync_sidebar(model="m", pid="groq")
        await pilot.pause()
        assert len(calls) == 1
