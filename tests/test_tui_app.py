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
        assert bar.mode == "studio"
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
        from textual.widgets import Button

        from opennote.tui.dialogs import HelpDialog
        from opennote.tui.screens.chat import ChatScreen

        assert isinstance(app.screen, HelpDialog)
        body = app.screen.query_one("#dialog-body")
        assert (
            "Press ctrl+p to see all available actions and commands in any context."
            in body.render().plain
        )
        assert app.screen.query_one("#help-okay", Button) is not None
        await pilot.press("enter")
        await pilot.pause()
        assert isinstance(app.screen, ChatScreen)


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


BANNER_ROW = "█▀▀█ █▀▀█ █▀▀█ █▀▀▄"


async def test_startup_create_opens_with_single_banner(tmp_path):
    from opennote.tui.dialogs import InputDialog
    from opennote.tui.screens.chat import ChatScreen

    app = await _make_app(tmp_path, notebook_name=None)
    async with app.run_test() as pilot:
        await pilot.pause()
        assert isinstance(app.screen, InputDialog)
        await pilot.press(*"nb123")
        await pilot.press("enter")
        await pilot.pause()
        assert isinstance(app.screen, ChatScreen)
        assert app.screen.notebook is not None
        assert app.screen.notebook.name == "nb123"
        assert _transcript_text(app.screen.transcript).count(BANNER_ROW) == 1


async def test_startup_guard_blocks_second_dialog(tmp_path):
    from opennote.tui.dialogs import InputDialog

    app = await _make_app(tmp_path, notebook_name=None)
    async with app.run_test() as pilot:
        await pilot.pause()
        assert isinstance(app.screen, InputDialog)
        depth_before = len(app.screen_stack)
        # Direct re-entry while a startup dialog is pending must no-op.
        chat = next(s for s in app.screen_stack if hasattr(s, "_startup_auto_new"))
        chat._startup_auto_new()
        await pilot.pause()
        assert len(app.screen_stack) == depth_before
        assert isinstance(app.screen, InputDialog)


async def test_startup_invalid_name_single_banner(tmp_path):
    from opennote.tui.dialogs import InputDialog
    from opennote.tui.screens.chat import ChatScreen

    app = await _make_app(tmp_path, notebook_name=None)
    async with app.run_test() as pilot:
        await pilot.pause()
        assert isinstance(app.screen, InputDialog)
        await pilot.press(*"bad name!")
        await pilot.press("enter")
        await pilot.pause()
        assert isinstance(app.screen, ChatScreen)
        assert app.screen.notebook is None
        text = _transcript_text(app.screen.transcript)
        assert "Cannot create notebook" in text
        assert text.count(BANNER_ROW) == 1


async def test_snake_opens_and_quits_with_single_banner(tmp_path):
    from textual.widgets import Static

    from opennote.tui.screens.chat import ChatScreen
    from opennote.tui.screens.snake import SnakeScreen

    app = await _make_app(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("slash", "s", "n", "a", "k", "e")
        await pilot.press("enter")
        await pilot.pause()
        assert isinstance(app.screen, SnakeScreen)
        assert app.screen._state.alive
        board = app.screen.query_one("#snake-board", Static)
        rendered = board.render()
        text = rendered.plain if hasattr(rendered, "plain") else str(rendered)
        assert "@" in text
        # Arrow keys steer the snake.
        await pilot.press("up")
        await pilot.pause()
        from opennote.tui.games.snake_logic import UP

        assert app.screen._state.direction == UP
        game = app.screen
        await pilot.press("q")
        await pilot.pause()
        assert isinstance(app.screen, ChatScreen)
        assert game._timer is None  # timer torn down on dismiss
        assert _transcript_text(app.screen.transcript).count(BANNER_ROW) == 1


async def test_snake_while_busy_then_result_lands(tmp_path):
    from opennote.tui.screens.chat import ChatScreen
    from opennote.tui.screens.snake import SnakeScreen

    client = BlockingClient()
    app = await _make_app(tmp_path, client=client, retriever=FakeRetriever(results=[]))
    async with app.run_test() as pilot:
        await pilot.pause()
        bar = app.screen.query_one("#prompt-bar", PromptBar)
        await pilot.press(*"how now")
        await pilot.press("enter")
        await pilot.pause()
        assert bar.busy
        # /snake is allowed while busy (waiting-room game).
        await pilot.press("slash", "s", "n", "a", "k", "e")
        await pilot.press("enter")
        await pilot.pause()
        assert isinstance(app.screen, SnakeScreen)
        game = app.screen
        client.release.set()
        await _wait_idle(pilot, bar)
        await pilot.press("q")
        await pilot.pause()
        assert isinstance(app.screen, ChatScreen)
        assert "done" in _transcript_text(app.screen.transcript)
        assert game._timer is None


async def test_ask_turn_updates_context_readout(tmp_path):
    from opennote.chat.client import TokenUsage

    client = ScriptedClient(
        [ChatResponse(content="The answer is 42.", usage=TokenUsage(prompt_tokens=1000, completion_tokens=50))],
        provider_id="groq",
        model="gpt-x",
    )
    app = await _make_app(tmp_path, client=client, retriever=FakeRetriever(results=[]))
    async with app.run_test() as pilot:
        await pilot.pause()
        bar = app.screen.query_one("#prompt-bar", PromptBar)
        assert "ctx" in bar.context  # always visible, even before first turn
        assert "0/" in bar.context
        await pilot.press(*"what is the meaning")
        await pilot.press("enter")
        await _wait_idle(pilot, bar)
        assert "ctx" in bar.context
        assert "1,050" in bar.context
        # /context shows the full panel on demand
        await pilot.press(*"/context")
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        transcript = app.screen.query_one("#transcript")
        assert "Context" in _transcript_text(transcript)


async def test_sidebar_shows_session_context_services(tmp_path):
    from opennote.tui.widgets.sidebar import SideBar

    app = await _make_app(tmp_path, retriever=FakeRetriever(results=[]))
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        side = app.screen.query_one("#sidebar", SideBar)
        assert side.display is not False
        assert side.session_title != ""
        assert "Context" in side.query_one("#side-context-head").render().plain
        assert "Services" in side.query_one("#side-services-head").render().plain
        # ASCII only - no odd glyphs anywhere in the sidebar
        for lbl in side.query("#side-session-title, #side-context, #side-services, #side-footer-left, #side-footer-right"):
            text = lbl.render().plain
            assert "┬" not in text and "Γ" not in text, text


async def test_sidebar_hidden_on_narrow_screen(tmp_path):
    from opennote.tui.widgets.sidebar import SideBar

    app = await _make_app(tmp_path, retriever=FakeRetriever(results=[]))
    async with app.run_test(size=(80, 30)) as pilot:
        await pilot.pause()
        side = app.screen.query_one("#sidebar", SideBar)
        assert side.display is False


async def test_meta_row_spaced_and_short_model(tmp_path):
    from opennote.tui.widgets.prompt import PromptBar, _short_model

    assert _short_model("models/gemini-3.5-flash") == "gemini-3.5-flash"
    assert _short_model("openai/gpt-oss-120b") == "gpt-oss-120b"
    assert _short_model("x" * 40).endswith("~")
    app = await _make_app(tmp_path, retriever=FakeRetriever(results=[]))
    async with app.run_test() as pilot:
        await pilot.pause()
        bar = app.screen.query_one("#prompt-bar", PromptBar)
        sep = bar.query_one("#meta-sep")
        assert sep.render().plain.strip() == "|"


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


async def test_slash_popup_enter_selects_highlighted(tmp_path):
    app = await _make_app(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("slash", "h")
        await pilot.pause()
        from opennote.tui.widgets.command_popup import CommandPopup

        popup = app.screen.query_one("#command-popup", CommandPopup)
        assert popup.display is True
        assert [c.name for c in popup.commands] == ["help"]
        await pilot.press("enter")
        await pilot.pause()
        from textual.widgets import Button

        from opennote.tui.dialogs import HelpDialog
        from opennote.tui.screens.chat import ChatScreen

        assert isinstance(app.screen, HelpDialog)
        body = app.screen.query_one("#dialog-body")
        assert (
            "Press ctrl+p to see all available actions and commands in any context."
            in body.render().plain
        )
        assert app.screen.query_one("#help-okay", Button) is not None
        await pilot.press("enter")
        await pilot.pause()
        assert isinstance(app.screen, ChatScreen)


async def test_slash_popup_enter_with_multiple_matches(tmp_path):
    app = await _make_app(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        from opennote.tui.widgets.command_popup import CommandPopup

        popup = app.screen.query_one("#command-popup", CommandPopup)
        await pilot.press("slash", "n")
        await pilot.pause()
        assert [c.name for c in popup.commands] == ["notebooks", "notebook"]
        await pilot.press("enter")
        await pilot.pause()
        from opennote.tui.dialogs import ItemListDialog

        assert isinstance(app.screen, ItemListDialog)  # /notebooks
        await pilot.press("escape")
        await pilot.pause()


async def test_slash_popup_enter_single_match(tmp_path):
    app = await _make_app(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("slash", "m")
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        # /model with no arg opens the provider picker (if any configured).
        from opennote.tui.dialogs import ItemListDialog
        from opennote.tui.screens.chat import ChatScreen

        if isinstance(app.screen, ItemListDialog):
            await pilot.press("escape")
            await pilot.pause()
        assert isinstance(app.screen, ChatScreen)
        assert "Unknown command" not in _transcript_text(app.screen.transcript)


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
        export_path = app.screen.notebook.directory / "exports" / f"notebook-{app.screen.notebook.name}.md"
        assert export_path.exists()
        content = export_path.read_text(encoding="utf-8")
        assert "## You" in content and "## Assistant" in content


async def test_notebooks_picker_opens_choices(tmp_path):
    app = await _make_app(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("slash", "n", "o", "t", "e", "b", "o", "o", "k", "s")
        await pilot.press("enter")
        await pilot.pause()
        from opennote.tui.dialogs import ItemListDialog

        assert isinstance(app.screen, ItemListDialog)
        await pilot.press("escape")
        await pilot.pause()
        from opennote.tui.screens.chat import ChatScreen

        assert isinstance(app.screen, ChatScreen)


async def test_ctrl_p_opens_palette(tmp_path):
    app = await _make_app(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("ctrl+p")
        await pilot.pause()
        from opennote.tui.dialogs import CommandPalette

        assert isinstance(app.screen, CommandPalette)
        await pilot.press("escape")
        await pilot.pause()
        from opennote.tui.screens.chat import ChatScreen

        assert isinstance(app.screen, ChatScreen)


async def test_palette_no_matches_shows_single_placeholder(tmp_path):
    app = await _make_app(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("ctrl+p")
        await pilot.pause()
        from opennote.tui.dialogs import CommandPalette

        assert isinstance(app.screen, CommandPalette)
        await pilot.press(*"zzzzzz")
        await pilot.pause()
        ol = app.screen.query_one("#palette-options")
        assert len(ol.options) == 1  # single placeholder row, not per-character
        await pilot.press("escape")
        await pilot.pause()


async def test_palette_filter_runs_studio_mode(tmp_path):
    app = await _make_app(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("ctrl+p")
        await pilot.pause()
        from opennote.tui.dialogs import CommandPalette, ItemListDialog

        assert isinstance(app.screen, CommandPalette)
        await pilot.press(*"studio mode")
        await pilot.pause()
        await pilot.press("down")
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        # Studio Mode enters studio and opens the generator picker
        assert isinstance(app.screen, ItemListDialog)
        await pilot.press("escape")
        await pilot.pause()


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
        assert app.screen.notebook is not None
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

        assert isinstance(app.screen, ItemListDialog)  # 4-action picker
        await pilot.press("enter")  # pick "Open existing notebook"
        await pilot.pause()
        assert isinstance(app.screen, ItemListDialog)  # notebook list
        await pilot.press("enter")  # pick first (other)
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
    from opennote.auth.validate import ValidationResult

    monkeypatch.setattr(
        "opennote.auth.config.AuthConfig",
        lambda path=None: RealAuthConfig(path=str(tmp_path / "auth.json")),
    )
    monkeypatch.setattr(kc, "set_key", lambda pid, key: None)
    monkeypatch.setattr(kc, "resolve_key", lambda pid: None)
    monkeypatch.setattr(
        "opennote.auth.validate.validate_key",
        lambda provider, api_key, **kw: ValidationResult.success(["m1", "m2"]),
    )
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
        assert isinstance(app.screen, chat_mod.ChatScreen)
        assert app.screen.notebook.provider_id == "groq"
        assert app.screen.notebook.model == client.model
        assert app.screen._client is client


async def test_undo_last_turn_removes_exchange(tmp_path):
    from opennote.transcript import load_transcript

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
        before = len(load_transcript(app.screen.notebook))
        assert before == 2
        assert "First answer." in _transcript_text(app.screen.transcript)
        await pilot.press("slash", "u", "n", "d", "o")
        await pilot.press("enter")
        await pilot.pause()
        assert load_transcript(app.screen.notebook) == []
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
        assert "Notebook" in body
        assert "gpt-x" in body
        assert "groq" in body
        assert "t" in body
        await pilot.press("escape")
        await pilot.pause()