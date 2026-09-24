"""Studio-mode coverage: every artifact kind must generate (fallback + LLM
paths) and — except audio/video — display natively in the terminal.

Run: pytest tests/test_studio_modes.py -v
Add -s to always see the per-kind output previews (shown on failure anyway).
"""
from __future__ import annotations

import time
from pathlib import Path

import pytest
from typer.testing import CliRunner

from opennote.artifacts import load_artifact
from opennote.retrieval.citations import citation_for
from opennote.retrieval.retriever import SearchResult
from opennote.tui.app import OpenNoteApp
from opennote.tui.screens.chat import ChatScreen, StudioResultMsg
from opennote.tui.theme import DARK
from opennote.tui.widgets.prompt import PromptBar

TEXT_KINDS = ["mindmap", "study", "faq", "briefing", "timeline", "suggest"]
AV_KINDS = ["audio", "video"]
ALL_KINDS = TEXT_KINDS + AV_KINDS

# mindmap without an LLM goes through create_mindmap (kind "markdown").
EXPECTED_FRONTMATTER = {
    "mindmap": "markdown",
    "study": "study",
    "faq": "faq",
    "briefing": "briefing",
    "timeline": "timeline",
    "suggest": "suggest",
}


def _result(filename, content):
    meta = {"filename": filename, "chunk_id": "c", "pages": "2"}
    return SearchResult(content=content, metadata=meta, similarity=0.5, citation=citation_for(meta))


class FakeRetriever:
    def __init__(self, results):
        self._results = results

    def search(self, query, top_k=None, source=None):
        return list(self._results)


class EmptyRetriever:
    def search(self, query, top_k=None, source=None):
        raise ValueError("no sources")


class FakeLlm:
    """Scripted LLM: returns markdown mentioning the kind under test."""

    def __init__(self, kind):
        self.kind = kind
        self.provider_id = "test"
        self.model = "mock"

    def chat(self, messages):
        from opennote.chat.client import ChatResponse

        return ChatResponse(content=f"# mock {self.kind} answer\n- grounded point one\n- grounded point two")


class TranscriptStub:
    def __init__(self):
        self.errors = []
        self.infos = []

    def add_error(self, message):
        self.errors.append(message)

    def add_info(self, message):
        self.infos.append(message)


def _manager(tmp_path):
    from opennote.notebooks import NotebookManager

    manager = NotebookManager(home=tmp_path)
    try:
        manager.get("t")
    except KeyError:
        manager.create("t")
    return manager


def _stub_screen(retriever=None, client=None):
    stub = object.__new__(ChatScreen)
    stub._retriever = retriever
    stub._client = client
    stub.transcript = TranscriptStub()
    return stub


def _results():
    return [
        _result("doc-a.pdf", "Alpha content about retrieval and ranking."),
        _result("doc-b.pdf", "Beta content about citations and grounding."),
    ]


def _preview(kind, body):
    print(f"\n--- {kind} ({len(body)} chars) ---")
    print(body[:600])
    print(f"--- end {kind} ---")


# -- layer 1: generation -------------------------------------------------------

@pytest.mark.parametrize("kind", TEXT_KINDS)
def test_generate_fallback(tmp_path, kind):
    nb = _manager(tmp_path).get("t")
    stub = _stub_screen(retriever=FakeRetriever(_results()))
    path = ChatScreen._generate_studio_artifact(stub, kind, "retrieval", nb)
    assert path, f"{kind}: no path returned (see transcript errors: {stub.transcript.errors})"
    art = load_artifact(path)
    assert art.kind == EXPECTED_FRONTMATTER[kind], f"{kind}: frontmatter kind is {art.kind!r}"
    assert art.body.strip(), f"{kind}: empty body"
    assert "retrieval" in art.body.lower(), f"{kind}: topic missing from body"
    _preview(kind, art.body)


@pytest.mark.parametrize("kind", TEXT_KINDS)
def test_generate_llm_path(tmp_path, kind):
    nb = _manager(tmp_path).get("t")
    stub = _stub_screen(retriever=FakeRetriever(_results()), client=FakeLlm(kind))
    path = ChatScreen._generate_studio_artifact(stub, kind, "retrieval", nb)
    assert path, f"{kind}: no path returned"
    art = load_artifact(path)
    assert art.body.strip(), f"{kind}: empty body"
    assert f"mock {kind}" in art.body, f"{kind}: LLM answer not saved as body"
    _preview(kind, art.body)


def test_generate_empty_notebook_returns_blank(tmp_path):
    nb = _manager(tmp_path).get("t")
    stub = _stub_screen(retriever=EmptyRetriever())
    path = ChatScreen._generate_studio_artifact(stub, "study", "retrieval", nb)
    assert path == ""
    # No transcript touch from the worker thread; _run_studio reports the failure.
    assert stub.transcript.errors == []


def test_generate_llm_exception_propagates(tmp_path):
    class BoomLlm(FakeLlm):
        def chat(self, messages):
            raise RuntimeError("provider down")

    nb = _manager(tmp_path).get("t")
    stub = _stub_screen(retriever=FakeRetriever(_results()), client=BoomLlm("study"))
    with pytest.raises(RuntimeError, match="provider down"):
        ChatScreen._generate_studio_artifact(stub, "study", "retrieval", nb)
    # And crucially: no "LLM error" body was persisted as an artifact.
    files = list(nb.directory.glob("artifacts/*.md"))
    assert files == []


# -- layer 2: native terminal display (headless pilot) --------------------------

def _transcript_text(transcript) -> str:
    return "\n".join("".join(seg.text for seg in strip) for strip in transcript.lines)


async def _wait_idle(pilot, bar, timeout=10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        await pilot.pause()
        if not bar.busy:
            return
    raise AssertionError("prompt bar never went idle")


@pytest.mark.parametrize("kind", TEXT_KINDS)
async def test_native_display(tmp_path, kind):
    nb = _manager(tmp_path).get("t")
    stub = _stub_screen(retriever=FakeRetriever(_results()), client=FakeLlm(kind))
    path = ChatScreen._generate_studio_artifact(stub, kind, "retrieval", nb)
    assert path
    app = OpenNoteApp(notebook_name="t", palette=DARK, manager=_manager(tmp_path), client=FakeLlm(kind))
    async with app.run_test() as pilot:
        await pilot.pause()
        app.screen.on_studio_result_msg(StudioResultMsg(kind, path))
        await pilot.pause()
        text = _transcript_text(app.screen.transcript)
        assert f"Studio {kind}:" in text, f"{kind}: missing path line"
        # The artifact content itself must be visible without external open.
        assert ("mock" in text and kind in text) or "retrieval" in text.lower(), (
            f"{kind}: body not rendered natively. transcript was:\n{text[:1500]}"
        )


@pytest.mark.parametrize("kind", AV_KINDS)
async def test_audio_video_stay_path_only(tmp_path, kind):
    app = OpenNoteApp(notebook_name="t", palette=DARK, manager=_manager(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        fake_bin = tmp_path / f"fake.{'mp3' if kind == 'audio' else 'mp4'}"
        fake_bin.write_bytes(b"\x00\x01binary")
        app.screen.on_studio_result_msg(StudioResultMsg(kind, str(fake_bin)))
        await pilot.pause()
        text = _transcript_text(app.screen.transcript)
        assert f"Studio {kind}:" in text
        assert "binary" not in text
        assert "unavailable" not in text  # real binary: no degradation note


@pytest.mark.parametrize("kind", AV_KINDS)
async def test_degraded_av_labeled_not_silent(tmp_path, kind):
    app = OpenNoteApp(notebook_name="t", palette=DARK, manager=_manager(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        stub_md = tmp_path / "fallback.md"
        stub_md.write_text("# transcript", encoding="utf-8")
        app.screen.on_studio_result_msg(StudioResultMsg(kind, str(stub_md)))
        await pilot.pause()
        text = _transcript_text(app.screen.transcript)
        assert f"Studio {kind}:" in text
        assert "unavailable" in text


async def test_run_studio_empty_index_posts_failure(tmp_path, monkeypatch):
    app = OpenNoteApp(notebook_name="t", palette=DARK, manager=_manager(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        monkeypatch.setattr(app.screen, "_generate_studio_artifact", lambda *a: "")
        bar = app.screen.query_one("#prompt-bar", PromptBar)
        app.screen._run_studio("study", "topic")
        await _wait_idle(pilot, bar)
        assert "No sources" in _transcript_text(app.screen.transcript)


# -- layer 3: CLI round-trip ----------------------------------------------------

@pytest.mark.parametrize("kind", TEXT_KINDS)
def test_cli_show_renders(kind, tmp_path, monkeypatch):
    import opennote.cli as cli_mod
    from opennote.notebooks import NotebookManager

    home = tmp_path / "home"
    monkeypatch.setenv("OPENNOTE_HOME", str(home))
    manager = NotebookManager(home=home)
    nb = manager.create("t")
    stub = _stub_screen(retriever=FakeRetriever(_results()), client=FakeLlm(kind))
    path = ChatScreen._generate_studio_artifact(stub, kind, "retrieval", nb)
    assert path
    monkeypatch.setattr(cli_mod, "get_manager", lambda: NotebookManager(home=home))
    result = CliRunner().invoke(cli_mod.app, ["artifacts", "show", Path(path).name, "--notebook", "t", "--tree"])
    assert result.exit_code == 0, f"{kind}: cli failed: {result.output}"
    assert "mock" in result.output or "retrieval" in result.output.lower(), (
        f"{kind}: cli output missing body:\n{result.output[:1500]}"
    )
    print(f"\n--- cli show {kind} ---\n{result.output[:600]}")


def test_cli_check_self_check(tmp_path, monkeypatch):
    import opennote.cli as cli_mod
    from opennote.notebooks import NotebookManager

    home = tmp_path / "home"
    monkeypatch.setenv("OPENNOTE_HOME", str(home))
    NotebookManager(home=home).create("t")

    class FakeRet:
        def __init__(self, nb, top_k=None):
            pass

        def search(self, topic):
            return _results()

    monkeypatch.setattr(cli_mod, "Retriever", FakeRet)
    monkeypatch.setattr(cli_mod, "get_manager", lambda: NotebookManager(home=home))
    result = CliRunner().invoke(cli_mod.app, ["artifacts", "check", "--notebook", "t", "--topic", "retrieval"])
    assert result.exit_code == 0, f"check failed:\n{result.output}"
    for kind in TEXT_KINDS:
        assert f" {kind:<9} PASS" in result.output, f"{kind} missing PASS:\n{result.output}"
    assert "FAIL" not in result.output
    assert "audio" in result.output and "video" in result.output


def test_cli_check_empty_notebook_skips(tmp_path, monkeypatch):
    import opennote.cli as cli_mod
    from opennote.notebooks import NotebookManager

    home = tmp_path / "home"
    monkeypatch.setenv("OPENNOTE_HOME", str(home))
    NotebookManager(home=home).create("t")
    monkeypatch.setattr(cli_mod, "get_manager", lambda: NotebookManager(home=home))
    result = CliRunner().invoke(cli_mod.app, ["artifacts", "check", "--notebook", "t"])
    assert result.exit_code == 1
    assert "SKIP" in result.output or "SKIP" in result.stderr


def test_video_error_path_materializes(tmp_path):
    """save_video_artifact must never return a dangling path (live /video bug)."""
    from pathlib import Path as _Path

    from opennote.video import save_video_artifact

    out = save_video_artifact("not json at all", tmp_path, "t")
    assert _Path(out).is_file(), f"video error path missing: {out}"


def test_fallback_slides_json_valid():
    import json as _json

    stub = _stub_screen()
    script = ChatScreen._fallback_slides_json(stub, "retrieval", _results())
    slides = _json.loads(script)
    assert isinstance(slides, list) and slides
    for s in slides:
        assert s["title"] and isinstance(s["bullets"], list) and s["narration"]


def test_slides_script_json_strips_fences():
    import json as _json

    from opennote.chat.client import ChatResponse

    class FencedLlm:
        provider_id = "test"
        model = "mock"

        def chat(self, messages):
            assert "JSON array" in messages[0]["content"]
            return ChatResponse(content='```json\n[{"title": "T", "bullets": ["b"], "narration": "n"}]\n```')

    stub = _stub_screen(client=FencedLlm())
    slides = _json.loads(ChatScreen._slides_script_json(stub, "retrieval", _results()))
    assert slides[0]["title"] == "T"


def test_slides_script_json_rejects_garbage():
    from opennote.chat.client import ChatResponse

    class GarbageLlm:
        provider_id = "test"
        model = "mock"

        def chat(self, messages):
            return ChatResponse(content="no json here")

    stub = _stub_screen(client=GarbageLlm())
    with pytest.raises(ValueError, match="slide JSON"):
        ChatScreen._slides_script_json(stub, "retrieval", _results())


# -- summary report (always prints; fails loudly on any gap) --------------------

def test_studio_report(tmp_path):
    rows = []
    for kind in TEXT_KINDS:
        nb = _manager(tmp_path).get("t")
        stub = _stub_screen(retriever=FakeRetriever(_results()))
        try:
            path = ChatScreen._generate_studio_artifact(stub, kind, "report", nb)
            art = load_artifact(path)
            ok = bool(art.body.strip())
            rows.append((kind, "PASS" if ok else "FAIL", f"{len(art.body)} chars"))
        except Exception as e:  # noqa: BLE001 - report must show the failure, not raise
            rows.append((kind, "FAIL", f"{type(e).__name__}: {e}"))
    for kind in AV_KINDS:
        rows.append((kind, "N/A", "binary: path-only by design"))
    print("\n kind      status  detail")
    for kind, status, detail in rows:
        print(f" {kind:<9} {status:<6} {detail}")
    assert all(s != "FAIL" for _, s, _ in rows), "studio gaps above"
