"""Regression tests for the TUI audit fixes (ledger L184-L191).

Each test pins a defect that was reproduced before the fix, so the failure mode
cannot come back silently.
"""
from __future__ import annotations

import threading
from typing import ClassVar

import pytest

from opennote.notebooks import NotebookManager
from opennote.tui.app import OpenNoteApp
from opennote.tui.theme import DARK, LIGHT
from opennote.tui.widgets.prompt import PromptBar


class _StubClient:
    provider_id = "groq"
    model = "llama-3.3-70b"


class _StubRetriever:
    def search(self, q, top_k=None, source=None):
        return []

    def sources(self):
        return []


def _manager(tmp_path):
    manager = NotebookManager(home=tmp_path)
    try:
        manager.get("t")
    except KeyError:
        manager.create("t")
    return manager


def _app(tmp_path, retriever=None, **kw):
    return OpenNoteApp(
        notebook_name="t", palette=DARK, manager=_manager(tmp_path),
        client=_StubClient(), retriever=retriever or _StubRetriever(), **kw
    )


def _transcript_text(transcript) -> str:
    return "\n".join("".join(seg.text for seg in strip) for strip in transcript.lines)


class _FakeResult:
    def __init__(self, answer):
        self.answer = answer
        self.provider_id = "groq"
        self.model = "llama-3.3-70b"
        self.usage = None
        self.dropped_claims = 0


class _FakeAgent:
    def __init__(self, question, answer):
        self.messages = [
            {"role": "user", "content": question},
            {"role": "assistant", "content": answer},
        ]
        self.result = _FakeResult(answer)


class _Gate:
    """A thread the test can park in and release."""

    def __init__(self):
        self.entered = threading.Event()
        self.release = threading.Event()


async def _until(pred, pilot, tries=200):
    for _ in range(tries):
        if pred():
            return True
        await pilot.pause()
    return False


# -- L184: one turn at a time -------------------------------------------------

async def test_second_ask_refused_while_busy(tmp_path):
    """A second question must not start a second turn (ledger L184).

    Textual's exclusive worker group does not stop a running thread, so
    unguarded entry points ran two full turns, both appended to the transcript,
    and whichever finished first called set_idle() - leaving the UI claiming to
    be free while a turn was still in flight.
    """
    gates = []

    def gated(nb, question, **kw):
        g = _Gate()
        gates.append(g)
        g.entered.set()
        g.release.wait(timeout=30)
        return _FakeAgent(question, f"ANSWER<{question}>")

    import opennote.tui.screens.chat as chat_mod

    real = chat_mod.agent_turn
    chat_mod.agent_turn = gated
    try:
        async with _app(tmp_path).run_test() as pilot:
            screen = pilot.app.screen
            await pilot.pause()
            bar = screen.query_one("#prompt-bar", PromptBar)
            screen._start_ask("question A")
            assert await _until(lambda: len(gates) >= 1, pilot)
            assert bar.busy

            screen._start_ask("question B")
            await pilot.pause()
            # Refused: the second turn never reached the provider.
            assert len(gates) == 1
            assert "Busy" in _transcript_text(screen.transcript)

            gates[0].release.set()
            for _ in range(80):
                await pilot.pause()
                if not bar.busy:
                    break
    finally:
        chat_mod.agent_turn = real


async def test_search_and_studio_refused_while_busy(tmp_path):
    gates = []

    def gated(nb, question, **kw):
        g = _Gate()
        gates.append(g)
        g.entered.set()
        g.release.wait(timeout=30)
        return _FakeAgent(question, "ok")

    import opennote.tui.screens.chat as chat_mod

    real = chat_mod.agent_turn
    chat_mod.agent_turn = gated
    try:
        async with _app(tmp_path).run_test() as pilot:
            screen = pilot.app.screen
            await pilot.pause()
            bar = screen.query_one("#prompt-bar", PromptBar)
            screen._start_ask("long one")
            assert await _until(lambda: len(gates) >= 1, pilot)
            assert bar.busy
            screen._start_search("a search")
            screen._start_studio("a topic")
            await pilot.pause()
            text = _transcript_text(screen.transcript)
            assert text.count("Busy") == 2
            # The studio menu must not have been pushed either.
            from opennote.tui.screens.chat import ChatScreen

            assert isinstance(pilot.app.screen, ChatScreen)
            gates[0].release.set()
            for _ in range(80):
                await pilot.pause()
                if not bar.busy:
                    break
    finally:
        chat_mod.agent_turn = real


async def test_mode_switch_does_not_clear_busy(tmp_path):
    """_set_mode calls set_idle() for its hint; that must not run mid-turn."""
    async with _app(tmp_path).run_test() as pilot:
        screen = pilot.app.screen
        await pilot.pause()
        bar = screen.query_one("#prompt-bar", PromptBar)
        bar.set_busy("working")
        screen._set_mode("search")
        await pilot.pause()
        assert bar.busy, "switching mode reported idle while a turn was running"
        assert screen.mode == "ask", "the mode must not have changed while busy"


# -- L185: dialogs must not query children during Mount ------------------------

@pytest.mark.parametrize(
    "factory",
    ["item_list", "info", "help", "input", "palette", "mindmap"],
)
async def test_dialogs_open_without_touching_unmounted_children(tmp_path, factory):
    """Dialogs must do their child work *after* the subtree is mounted.

    ``on_mount``/``on_screen_resume`` both fired before the composed children
    were registered, so ``query_one`` raced and raised NoMatches - which kills
    the app. Work is deferred via ``call_after_refresh`` instead.
    """
    from opennote.tui import dialogs as dlg

    class _Node:
        label = "root"
        children: ClassVar[list] = []

    async with _app(tmp_path).run_test() as pilot:
        await pilot.pause()
        if factory == "item_list":
            dlg.item_list(pilot.app, "Pick", [("a", "Alpha")], lambda v: None)
        elif factory == "info":
            pilot.app.push_screen(dlg.InfoDialog("T", "body"))
        elif factory == "help":
            pilot.app.push_screen(dlg.HelpDialog("T", "body"))
        elif factory == "input":
            dlg.ask_input(pilot.app, "T", "label")
        elif factory == "palette":
            from opennote.tui.screens.chat import ChatScreen

            pilot.app.push_screen(dlg.CommandPalette(ChatScreen()))
        else:
            pilot.app.push_screen(dlg.MindmapDialog("T", _Node()))
        # No exception, and the screen is up and functional.
        for _ in range(40):
            await pilot.pause()
        assert pilot.app.screen is not None
        assert len(pilot.app.screen.query("#dialog")) == 1
        if factory in ("item_list", "input", "palette"):
            # The deferred work ran: something inside the dialog has focus.
            assert pilot.app.screen.focused is not None, factory
        await pilot.press("escape")
        await pilot.pause()


async def test_dialogs_defer_instead_of_querying_in_on_mount():
    """Structural guard: no dialog may query children from on_mount."""
    import inspect

    from opennote.tui import dialogs as dlg
    from opennote.tui.screens import snake as snake_mod

    for cls in (
        dlg.InfoDialog,
        dlg.ItemListDialog,
        dlg.MindmapDialog,
        dlg.HelpDialog,
        dlg.InputDialog,
        dlg.CommandPalette,
        snake_mod.SnakeScreen,
    ):
        for hook in ("on_mount", "on_screen_resume"):
            fn = cls.__dict__.get(hook)
            if fn is None:
                continue
            src = inspect.getsource(fn)
            assert "query_one" not in src, f"{cls.__name__}.{hook} queries children"
        assert hasattr(cls, "_after_mount") or hasattr(cls, "_start"), cls.__name__


# -- L186: /theme re-points the transcript ------------------------------------

async def test_theme_switch_repoints_transcript_palette(tmp_path):
    async with _app(tmp_path).run_test() as pilot:
        screen = pilot.app.screen
        await pilot.pause()
        screen._switch_theme("light")
        await pilot.pause()
        assert screen.palette is LIGHT
        # The transcript bakes colors into each Text at write time, so it must
        # be re-pointed or new lines keep the dark palette.
        assert screen.transcript.palette is LIGHT
        screen._switch_theme("dark")
        await pilot.pause()
        assert screen.transcript.palette is DARK


async def test_theme_switch_does_not_stack_banners(tmp_path):
    async with _app(tmp_path).run_test() as pilot:
        screen = pilot.app.screen
        await pilot.pause()
        for _ in range(4):
            screen._switch_theme("light")
            screen._switch_theme("dark")
            await pilot.pause()
        # The banner is redrawn in place, so it stays a single logical write.
        assert screen.transcript.write_count == 1


async def test_theme_switch_after_history_does_not_grow(tmp_path):
    async with _app(tmp_path).run_test() as pilot:
        screen = pilot.app.screen
        await pilot.pause()
        screen.transcript.add_info("something happened")
        await pilot.pause()
        before = screen.transcript.write_count
        for _ in range(3):
            screen._switch_theme("light")
            await pilot.pause()
        # One info line per explicit switch, not a banner each time.
        assert screen.transcript.write_count == before + 3


# -- L187: no duplicated Context heading ---------------------------------------

async def test_sidebar_context_heading_not_duplicated(tmp_path):
    async with _app(tmp_path).run_test(size=(130, 40)) as pilot:
        screen = pilot.app.screen
        await pilot.pause()
        from opennote.context_meter import ContextUsage

        screen.query_one("#sidebar").set_usage(
            ContextUsage(input_tokens=100, output_tokens=20, limit=1000, exact=True)
        )
        await pilot.pause()
        body = screen.query_one("#side-context").render().plain
        heading = screen.query_one("#side-context-head").render().plain
        assert heading == "Context"
        assert "Context" not in body
        assert "120 tokens" in body


def test_context_meter_render_is_heading_plus_body():
    from opennote.context_meter import ContextUsage

    u = ContextUsage(input_tokens=1, output_tokens=2, limit=10, exact=True)
    assert u.render() == "Context\n" + u.render_body()
    assert u.render().startswith("Context\n")


# -- L188: hidden sidebar costs nothing ----------------------------------------

async def test_hidden_sidebar_does_no_slow_work(tmp_path, monkeypatch):
    import opennote.skills.registry as reg_mod

    calls = []
    real = reg_mod.SkillRegistry.discover

    @classmethod
    def counting(cls, cwd=None):
        calls.append(1)
        return real()

    monkeypatch.setattr(reg_mod.SkillRegistry, "discover", counting)
    async with _app(tmp_path).run_test() as pilot:  # 80 cols -> sidebar hidden
        screen = pilot.app.screen
        await pilot.pause()
        assert screen.query_one("#sidebar").display is False
        screen._sync_meta()
        for _ in range(30):
            await pilot.pause()
        assert calls == []


# -- L190: /help lists the real commands ---------------------------------------

async def test_help_lists_commands_not_a_pointer(tmp_path):
    from opennote.tui.dialogs import HelpDialog

    async with _app(tmp_path).run_test() as pilot:
        await pilot.pause()
        await pilot.press("slash", "h", "e", "l", "p")
        await pilot.press("enter")
        await pilot.pause()
        assert isinstance(pilot.app.screen, HelpDialog)
        plain = pilot.app.screen.query_one("#dialog-body").render().plain
        for expected in ("/ingest", "/notebooks", "/context", "/snake", "Studio"):
            assert expected in plain, expected


# -- L191: studio menu asks for a topic when it has none -----------------------

async def test_studio_menu_without_topic_prompts(tmp_path):
    from opennote.tui.dialogs import InputDialog, ItemListDialog

    async with _app(tmp_path).run_test() as pilot:
        screen = pilot.app.screen
        await pilot.pause()
        screen.mode = "studio"
        screen.prompt.set_mode("studio")
        screen._studio_topic = ""
        screen._open_studio_menu()
        assert await _until(
            lambda: isinstance(pilot.app.screen, ItemListDialog), pilot
        )
        from textual.widgets import ListView

        # The list is registered by the container's own message pump and focused
        # by the dialog's deferred retry, so wait for focus rather than forcing
        # it - pressing Enter before focus lands just dismisses the picker.
        assert await _until(
            lambda: isinstance(pilot.app.screen.focused, ListView), pilot
        ), "the generator list never took focus"
        # Row 0 is "View artifacts"; move to the first actual generator.
        await pilot.press("down")
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        # No topic was supplied, so it must ask rather than generate for "".
        assert isinstance(pilot.app.screen, InputDialog)


async def test_studio_topic_is_not_reused_by_a_later_menu(tmp_path):
    """A consumed topic must not silently apply to the next generator run."""
    generated = []

    async with _app(tmp_path).run_test() as pilot:
        screen = pilot.app.screen
        await pilot.pause()
        import opennote.tui.screens.chat as chat_mod

        real = chat_mod.ChatScreen._run_studio
        chat_mod.ChatScreen._run_studio = (
            lambda self, kind, topic: generated.append((kind, topic))
        )
        try:
            screen._studio_topic = "quantum"
            screen._on_studio_picked("mindmap")
            assert generated == [("mindmap", "quantum")]
            screen._on_studio_picked("faq")
            # The second pick has no topic left, so it must prompt, not reuse.
            assert generated == [("mindmap", "quantum")]
        finally:
            chat_mod.ChatScreen._run_studio = real


# -- L193: /auth renders ASCII, not mojibake ----------------------------------

async def test_auth_dialog_is_ascii(tmp_path):
    from opennote.tui.dialogs import InfoDialog

    async with _app(tmp_path).run_test() as pilot:
        screen = pilot.app.screen
        await pilot.pause()
        screen._show_auth()
        await pilot.pause()
        assert isinstance(pilot.app.screen, InfoDialog)
        plain = pilot.app.screen.query_one("#dialog-body").render().plain
        assert all(ord(ch) < 128 for ch in plain), repr(plain)
        assert "key=" in plain


def test_tui_chrome_strings_are_ascii():
    """Chrome is ASCII-only (AGENTS.md); the logo block art is exempt.

    Walks real string literals via ``ast`` so docstrings and comments -- which
    are never rendered -- are excluded.
    """
    import ast
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[1] / "opennote" / "tui"
    offenders = []
    for path in sorted(root.rglob("*.py")):
        if path.name == "banner.py":  # the logo is intentionally block-art
            continue
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        docstrings = set()
        for node in ast.walk(tree):
            if isinstance(
                node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
            ):
                body = getattr(node, "body", [])
                if body and isinstance(body[0], ast.Expr) and isinstance(
                    body[0].value, ast.Constant
                ):
                    docstrings.add(id(body[0].value))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
                continue
            if id(node) in docstrings:
                continue
            for ch in node.value:
                # Operator decision: a small curated glyph set is now allowed
                # in transcript chrome (prettify pass). Everything else stays
                # banned so mojibake cannot creep back in.
                if ord(ch) > 127 and ch not in "◆●✗›…·":
                    offenders.append(f"{path.name}:{node.lineno} {ch!r}")
                    break
    assert not offenders, "\n".join(offenders)
