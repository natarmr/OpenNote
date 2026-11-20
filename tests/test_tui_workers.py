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


async def _wait_idle(pilot, bar, timeout=10.0, settle=3):
    """Require ``settle`` consecutive idle polls — a single non-busy poll is
    premature on paths that never set busy, and asserts on a half-done turn."""
    deadline = time.monotonic() + timeout
    idle = 0
    while time.monotonic() < deadline:
        await pilot.pause()
        if bar.busy:
            idle = 0
            continue
        idle += 1
        if idle >= settle:
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
        first = app.screen._get_retriever()
        second = app.screen._get_retriever()
        assert first is second
        assert len(built) == 1
        app.screen._invalidate_retrievers()
        third = app.screen._get_retriever()
        assert third is not first
        assert len(built) == 2


async def test_retriever_cache_ignores_top_k(tmp_path, monkeypatch):
    """One Retriever per notebook, not one per top_k (ledger L190).

    A Retriever owns a Chroma client and a BM25 snapshot, and the call sites ask
    for 5, 8 and "adaptive" - keying on top_k built one of each.
    """
    import opennote.retrieval.retriever as ret_mod

    built = []

    class CountingRet:
        def __init__(self, nb, **kw):
            built.append(kw)
            self.searches = []

        def search(self, query, top_k=None, source=None):
            self.searches.append(top_k)
            return []

    monkeypatch.setattr(ret_mod, "Retriever", CountingRet)
    app = _make_app(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        ret = app.screen._get_retriever()
        ret.search("q", top_k=5)
        ret.search("q", top_k=8)
        # A top_k is never passed to the constructor any more ...
        assert all("top_k" not in kw for kw in built)
        # ... and a different top_k must not build a second Retriever.
        app.screen._start_search("hello")
        await pilot.pause()
        assert len(built) == 1


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
    # Wide enough for the sidebar to be visible: _sync_sidebar is a no-op when
    # it is hidden, and at the default 80-column test size it always is.
    app = OpenNoteApp(
        notebook_name="t", palette=DARK, manager=_manager(tmp_path),
        client=_StubClient(), retriever=FakeRetriever(),
    )
    async with app.run_test(size=(130, 40)) as pilot:
        await pilot.pause()
        app.screen._sync_sidebar(model="m", pid="groq")
        # The slow sections run in a worker; wait for the cache to be written.
        for _ in range(120):
            await pilot.pause()
            if app.screen._sidebar_slow("services") is not None:
                break
        else:
            raise AssertionError("sidebar slow sections never populated the cache")
        first = len(calls)
        assert first, "skills were never discovered"
        app.screen._sync_sidebar(model="m", pid="groq")
        for _ in range(20):
            await pilot.pause()
        # A second sync must add zero discovery work (the TTL cache hit).
        assert len(calls) == first
        # And no extra worker is left in flight.
        assert app.screen._sidebar_slow_pending is False


async def test_sidebar_skips_slow_work_when_hidden(tmp_path, monkeypatch):
    """A hidden sidebar must not walk the FS, import plugins, or shell out."""
    import opennote.skills.registry as reg_mod

    calls = []
    real = reg_mod.SkillRegistry.discover

    @classmethod
    def counting(cls, cwd=None):
        calls.append(1)
        return real()

    monkeypatch.setattr(reg_mod.SkillRegistry, "discover", counting)
    app = _make_app(tmp_path)  # default 80 cols -> sidebar hidden
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app.screen.query_one("#sidebar").display is False
        app.screen._sync_sidebar(model="m", pid="groq")
        for _ in range(20):
            await pilot.pause()
        assert calls == []
