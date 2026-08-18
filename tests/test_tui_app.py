"""End-to-end TUI app tests via Textual's headless pilot."""
import threading
import time

import pytest

from opennote.agents.loop import agent_turn
from opennote.chat.client import ChatResponse
from opennote.retrieval.citations import citation_for
from opennote.retrieval.retriever import SearchResult
from opennote.tui.app import OpenNoteApp
from opennote.tui.theme import DARK
from opennote.tui.widgets.prompt import PromptBar


class ScriptedClient:
    def __init__(self, responses, provider_id="groq", model="m"):
        self._responses = list(responses)
        self.provider_id = provider_id
        self.model = model
        self.sent = []

    def chat(self, messages, tools=None, system=None, max_tokens=1024):
        self.sent.append({"messages": list(messages), "tools": tools})
        if self._responses:
            return self._responses.pop(0)
        return ChatResponse(content="No more responses.")


class BlockingClient(ScriptedClient):
    """Stays alive until released, for interrupt tests."""

    def __init__(self):
        super().__init__([ChatResponse(content="done")])
        self.release = threading.Event()

    def chat(self, messages, tools=None, system=None, max_tokens=1024):
        self.release.wait(timeout=10)
        return super().chat(messages, tools=tools, system=system)


class FakeRetriever:
    def __init__(self, results=None):
        self._results = results or []
        self.calls = []

    def search(self, query, top_k=None, source=None):
        self.calls.append({"query": query, "top_k": top_k})
        return list(self._results)

    def sources(self):
        return []


def _result(filename, content):
    meta = {"filename": filename, "chunk_id": "c", "pages": "2"}
    return SearchResult(
        content=content, metadata=meta, similarity=0.5, citation=citation_for(meta)
    )


async def _make_app(tmp_path, client=None, retriever=None, notebook_name="t"):
    app = OpenNoteApp(
        notebook_name=notebook_name,
        palette=DARK,
        manager=_manager(tmp_path),
        client=client,
        retriever=retriever,
    )
    return app


async def _wait_idle(pilot, bar, timeout=10.0):
    """Pause until the prompt bar is idle (worker finished)."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        await pilot.pause()
        if not bar.busy:
            return
    raise AssertionError("prompt bar never went idle")


def _transcript_text(transcript) -> str:
    """Flatten a mounted RichLog's lines into plain text."""
    return "\n".join(
        "".join(seg.text for seg in strip) for strip in transcript.lines
    )


async def test_app_mounts_chat_screen(tmp_path):
    app = await _make_app(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        from opennote.tui.screens.chat import ChatScreen

        assert isinstance(app.screen, ChatScreen)
        bar = app.screen.query_one("#prompt-bar", PromptBar)
        assert bar.mode == "ask"


async def test_meta_row_shows_model_and_provider(tmp_path):
    client = ScriptedClient([ChatResponse(content="hi")], provider_id="groq", model="gpt-x")
    app = await _make_app(tmp_path, client=client)
    async with app.run_test() as pilot:
        await pilot.pause()
        bar = app.screen.query_one("#prompt-bar", PromptBar)
        assert bar.model == "gpt-x"
        assert bar.provider == "groq"


async def test_tab_cycles_modes(tmp_path):
    app = await _make_app(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        bar = app.screen.query_one("#prompt-bar", PromptBar)
        assert bar.mode == "ask"
        await pilot.press("tab")
        await pilot.pause()
        assert bar.mode == "search"
        await pilot.press("tab")
        await pilot.pause()
        assert bar.mode == "ask"


async def test_slash_help_opens_dialog(tmp_path):
    app = await _make_app(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("slash", "h", "e", "l", "p")
        await pilot.press("enter")
        await pilot.pause()
        from opennote.tui.dialogs import InfoDialog

        assert isinstance(app.screen, InfoDialog)
        body = app.screen.query_one("#dialog-body")
        assert "Slash commands" in body.render().plain


async def test_ask_submit_runs_agent_and_shows_answer(tmp_path):
    client = ScriptedClient(
        [ChatResponse(content="The answer is 42.")], provider_id="groq", model="gpt-x"
    )
    app = await _make_app(tmp_path, client=client, retriever=FakeRetriever(results=[]))
    async with app.run_test() as pilot:
        await pilot.pause()
        bar = app.screen.query_one("#prompt-bar", PromptBar)
        await pilot.press(*"what is the meaning")
        await pilot.press("enter")
        await _wait_idle(pilot, bar)
        transcript = app.screen.query_one("#transcript")
        assert "42" in _transcript_text(transcript)
        assert client.sent  # agent_turn actually invoked the client


async def test_search_mode_uses_retriever(tmp_path):
    retriever = FakeRetriever(results=[_result("a.pdf", "some content here")])
    app = await _make_app(tmp_path, retriever=retriever)
    async with app.run_test() as pilot:
        await pilot.pause()
        bar = app.screen.query_one("#prompt-bar", PromptBar)
        await pilot.press("tab")  # -> search
        await pilot.pause()
        await pilot.press(*"find it")
        await pilot.press("enter")
        await _wait_idle(pilot, bar)
        transcript = app.screen.query_one("#transcript")
        assert "some content here" in _transcript_text(transcript)
        assert retriever.calls and retriever.calls[0]["query"] == "find it"


async def test_interrupt_cancels_blocked_turn(tmp_path):
    client = BlockingClient()
    app = await _make_app(tmp_path, client=client, retriever=FakeRetriever(results=[]))
    async with app.run_test() as pilot:
        await pilot.pause()
        bar = app.screen.query_one("#prompt-bar", PromptBar)
        await pilot.press(*"how now")
        await pilot.press("enter")
        # First esc arms the interrupt hint; second sets the cancel flag.
        await pilot.press("escape")
        await pilot.pause()
        assert "esc again" in bar.hint
        await pilot.press("escape")
        await pilot.pause()
        client.release.set()
        await _wait_idle(pilot, bar)
        transcript = app.screen.query_one("#transcript")
        assert "Interrupted" in _transcript_text(transcript)


async def test_unknown_provider_shows_error_notice(tmp_path):
    app = OpenNoteApp(
        notebook_name="t",
        palette=DARK,
        manager=_manager(tmp_path),
        provider_id="nope",
    )
    async with app.run_test() as pilot:
        await pilot.pause()
        transcript = app.screen.query_one("#transcript")
        assert "provider" in _transcript_text(transcript).lower()


async def test_slash_popup_filters_and_tab_completes(tmp_path):
    app = await _make_app(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        bar = app.screen.query_one("#prompt-bar", PromptBar)
        from opennote.tui.widgets.command_popup import CommandPopup

        popup = app.screen.query_one("#command-popup", CommandPopup)
        await pilot.press("slash", "m", "o")
        await pilot.pause()
        assert popup.display is True
        assert [c.name for c in popup.commands] == ["model"]
        await pilot.press("tab")
        await pilot.pause()
        input_widget = bar.query_one("#prompt-input")
        assert input_widget.text == "/model "
        assert popup.display is False


async def test_slash_popup_escape_hides(tmp_path):
    app = await _make_app(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        from opennote.tui.widgets.command_popup import CommandPopup

        popup = app.screen.query_one("#command-popup", CommandPopup)
        await pilot.press("slash", "h")
        await pilot.pause()
        assert popup.display is True
        await pilot.press("escape")
        await pilot.pause()
        assert popup.display is False


async def test_export_writes_session_markdown(tmp_path):
    client = ScriptedClient(
        [ChatResponse(content="The answer is 42.")], provider_id="groq", model="gpt-x"
    )
    app = await _make_app(tmp_path, client=client, retriever=FakeRetriever(results=[]))
    async with app.run_test() as pilot:
        await pilot.pause()
        bar = app.screen.query_one("#prompt-bar", PromptBar)
        await pilot.press(*"what is the meaning")
        await pilot.press("enter")
        await _wait_idle(pilot, bar)
        await pilot.press("slash", "e", "x", "p", "o", "r", "t")
        await pilot.press("enter")
        await pilot.pause()
        from opennote.agents.session import list_sessions

        session_id = list_sessions(app.screen.notebook)[0]["id"]
        export_path = app.screen.notebook.directory / "exports" / f"session-{session_id}.md"
        assert export_path.exists()
        content = export_path.read_text(encoding="utf-8")
        assert "## You" in content and "## Assistant" in content


async def test_sessions_dialog_resumes_picked(tmp_path):
    from opennote.agents.session import list_sessions, new_session

    app = await _make_app(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        first = app.screen.session["id"]
        second = new_session(app.screen.notebook, "groq", "gpt-x")["id"]
        await pilot.press("slash", "s", "e", "s", "s", "i", "o", "n", "s")
        await pilot.press("enter")
        await pilot.pause()
        from opennote.tui.dialogs import ItemListDialog

        assert isinstance(app.screen, ItemListDialog)
        await pilot.press("enter")  # picks the most recent (second) session
        await pilot.pause()
        assert app.screen.session["id"] == second
        assert first != second


async def test_ctrl_p_opens_palette(tmp_path):
    app = await _make_app(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("ctrl+p")
        await pilot.pause()
        from opennote.tui.dialogs import ItemListDialog

        assert isinstance(app.screen, ItemListDialog)
        await pilot.press("escape")
        await pilot.pause()
        from opennote.tui.screens.chat import ChatScreen

        assert isinstance(app.screen, ChatScreen)


async def test_theme_command_switches_palette(tmp_path):
    app = await _make_app(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app.theme == "opencode"
        await pilot.press("slash", "t", "h", "e", "m", "e")
        await pilot.press("enter")
        await pilot.pause()
        assert app.theme == "opencode-light"
        await pilot.press("slash", "t", "h", "e", "m", "e")
        await pilot.press("enter")
        await pilot.pause()
        assert app.theme == "opencode"


def _manager(tmp_path):
    from opennote.notebooks import NotebookManager

    manager = NotebookManager(home=tmp_path)
    try:
        manager.get("t")
    except KeyError:
        manager.create("t")
    return manager


async def test_create_notebook_switches(tmp_path):
    app = await _make_app(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app.screen.notebook.name == "t"
        await pilot.press("slash", "c", "r", "e", "a", "t", "e")
        await pilot.press("space")
        await pilot.press(*"notes")
        await pilot.press("enter")
        await pilot.pause()
        assert app.screen.notebook.name == "notes"
        assert app.screen.session is not None
        app.screen._manager.get("notes")


async def test_create_notebook_invalid_name(tmp_path):
    app = await _make_app(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("slash", "c", "r", "e", "a", "t", "e")
        await pilot.press("space")
        await pilot.press(*"bad name")
        await pilot.press("enter")
        await pilot.pause()
        assert app.screen.notebook.name == "t"
        text = _transcript_text(app.screen.transcript)
        assert "Invalid" in text or "empty" in text


async def test_notebooks_dialog_switches(tmp_path):
    app = await _make_app(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        app.screen._manager.create("other")
        await pilot.press("slash", "n", "o", "t", "e", "b", "o", "o", "k", "s")
        await pilot.press("enter")
        await pilot.pause()
        from opennote.tui.dialogs import ItemListDialog

        assert isinstance(app.screen, ItemListDialog)
        await pilot.press("enter")  # sorted: ["other", "t"], index 0 = "other"
        await pilot.pause()
        from opennote.tui.screens.chat import ChatScreen

        assert isinstance(app.screen, ChatScreen)
        assert app.screen.notebook.name == "other"


async def test_ingest_worker_reports_chunks(tmp_path):
    calls = {}

    def fake_ingest(notebook, target):
        calls["target"] = target
        return 7

    app = await _make_app(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        app.screen._ingest_fn = fake_ingest
        await pilot.press("slash", "i", "n", "g", "e", "s", "t")
        await pilot.press("space")
        await pilot.press(*"report.md")
        await pilot.press("enter")
        await pilot.pause()
        bar = app.screen.query_one("#prompt-bar", PromptBar)
        await _wait_idle(pilot, bar)
        assert calls.get("target") == "report.md"
        assert "Indexed 7 chunk(s)" in _transcript_text(app.screen.transcript)


async def test_ingest_failure_reports_error(tmp_path):
    def fake_ingest(notebook, target):
        raise FileNotFoundError("report.md")

    app = await _make_app(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        app.screen._ingest_fn = fake_ingest
        await pilot.press("slash", "i", "n", "g", "e", "s", "t")
        await pilot.press("space")
        await pilot.press(*"report.md")
        await pilot.press("enter")
        await pilot.pause()
        bar = app.screen.query_one("#prompt-bar", PromptBar)
        await _wait_idle(pilot, bar)
        assert "Ingest failed" in _transcript_text(app.screen.transcript)


async def test_auth_opens_info_dialog(tmp_path, monkeypatch):
    from opennote.auth.config import AuthConfig as RealAuthConfig

    monkeypatch.setattr(
        "opennote.auth.config.AuthConfig",
        lambda path=None: RealAuthConfig(path=str(tmp_path / "auth.json")),
    )
    monkeypatch.setattr("opennote.auth.keychain.resolve_key", lambda pid: None)
    app = await _make_app(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("slash", "a", "u", "t", "h")
        await pilot.press("enter")
        await pilot.pause()
        from opennote.tui.dialogs import InfoDialog

        assert isinstance(app.screen, InfoDialog)
        body = app.screen.query_one("#dialog-body").render().plain
        assert "groq" in body
        assert "no key" in body
        await pilot.press("escape")
        await pilot.pause()


async def test_connect_flow(tmp_path, monkeypatch):
    from opennote.auth.config import AuthConfig as RealAuthConfig
    from opennote.tui.screens import chat as chat_mod
    from opennote.auth import keychain as kc

    monkeypatch.setattr(
        "opennote.auth.config.AuthConfig",
        lambda path=None: RealAuthConfig(path=str(tmp_path / "auth.json")),
    )
    monkeypatch.setattr(kc, "set_key", lambda pid, key: None)
    monkeypatch.setattr(kc, "resolve_key", lambda pid: None)
    client = ScriptedClient([], provider_id="groq", model="llama-3.3-70b-versatile")
    monkeypatch.setattr(chat_mod, "get_client", lambda pid: client)
    app = await _make_app(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("slash", "c", "o", "n", "n", "e", "c", "t")
        await pilot.press("enter")
        await pilot.pause()
        from opennote.tui.dialogs import ItemListDialog, InputDialog

        assert isinstance(app.screen, ItemListDialog)
        # all_providers() order: anthropic, openai, opencode, cerebras, groq, google
        await pilot.press("down", "down", "down", "down")
        await pilot.press("enter")
        await pilot.pause()
        assert isinstance(app.screen, InputDialog)
        await pilot.press(*"sk-test")
        await pilot.press("enter")
        await pilot.pause()
        assert isinstance(app.screen, ItemListDialog)
        await pilot.press("enter")
        await pilot.pause()
        assert app.screen.session["provider_id"] == "groq"
        assert app.screen.session["model"] == client.model
        assert app.screen._client is client


async def test_undo_last_turn_removes_exchange(tmp_path):
    client = ScriptedClient(
        [ChatResponse(content="First answer.")], provider_id="groq", model="gpt-x"
    )
    app = await _make_app(tmp_path, client=client, retriever=FakeRetriever(results=[]))
    async with app.run_test() as pilot:
        await pilot.pause()
        bar = app.screen.query_one("#prompt-bar", PromptBar)
        await pilot.press(*"hello there")
        await pilot.press("enter")
        await _wait_idle(pilot, bar)
        before = len(app.screen.session["messages"])
        assert before == 2
        assert "First answer." in _transcript_text(app.screen.transcript)
        await pilot.press("slash", "u", "n", "d", "o")
        await pilot.press("enter")
        await pilot.pause()
        assert app.screen.session["messages"] == []
        text = _transcript_text(app.screen.transcript)
        assert "First answer." not in text
        assert "Undid the last turn" in text


async def test_undo_empty_session_informs(tmp_path):
    app = await _make_app(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("slash", "u", "n", "d", "o")
        await pilot.press("enter")
        await pilot.pause()
        assert "Nothing to undo." in _transcript_text(app.screen.transcript)


async def test_details_opens_info_dialog(tmp_path):
    client = ScriptedClient([ChatResponse(content="hi")], provider_id="groq", model="gpt-x")
    app = await _make_app(tmp_path, client=client, retriever=FakeRetriever(results=[]))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("slash", "d", "e", "t", "a", "i", "l", "s")
        await pilot.press("enter")
        await pilot.pause()
        from opennote.tui.dialogs import InfoDialog

        assert isinstance(app.screen, InfoDialog)
        body = app.screen.query_one("#dialog-body").render().plain
        assert "Session" in body
        assert "Notebook" in body
        assert "gpt-x" in body
        assert "groq" in body
        assert "t" in body  # notebook name
        await pilot.press("escape")
        await pilot.pause()