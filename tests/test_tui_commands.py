"""Phase U2 tests: command registry, autocomplete popup, dialogs, export."""
import pytest

from opennote.tui.commands import help_text, lookup, make_commands, matches_prefix
from opennote.tui.dialogs import InfoDialog, ItemListDialog, item_list
from opennote.tui.widgets.command_popup import CommandPopup
from opennote.tui.widgets.prompt import PromptBar


class StubScreen:
    """Minimal stand-in for ChatScreen (commands only call screen methods)."""

    def __init__(self):
        self.transcript = []
        self.calls = []

    def add_info(self, text):
        self.transcript.append(text)

    def _show_help(self, _arg=""):
        self.calls.append("help")

    def _new_session(self, _arg=""):
        self.calls.append("new")

    def _clear_transcript(self, _arg=""):
        self.calls.append("clear")

    def _open_sessions_dialog(self, _arg=""):
        self.calls.append("sessions")

    def _resume_session(self, _arg=""):
        self.calls.append("resume")

    def _resume_last(self, _arg=""):
        self.calls.append("continue")

    def _switch_provider(self, _arg=""):
        self.calls.append("model")

    def _list_sources(self, _arg=""):
        self.calls.append("sources")

    def _export_session(self, _arg=""):
        self.calls.append("export")

    def _set_mode_ask(self, _arg=""):
        self.calls.append("ask")

    def _set_mode_search(self, _arg=""):
        self.calls.append("search")

    def _switch_theme(self, _arg=""):
        self.calls.append("theme")

    def _open_palette(self, _arg=""):
        self.calls.append("palette")

    def _open_notebooks_dialog(self, _arg=""):
        self.calls.append("notebooks")

    def _switch_notebook(self, _arg=""):
        self.calls.append("notebook")

    def _create_notebook(self, _arg=""):
        self.calls.append("create")

    def _start_ingest(self, _arg=""):
        self.calls.append("ingest")

    def _show_auth(self, _arg=""):
        self.calls.append("auth")

    def _start_connect(self, _arg=""):
        self.calls.append("connect")

    def _undo_last_turn(self, _arg=""):
        self.calls.append("undo")

    def _show_details(self, _arg=""):
        self.calls.append("details")


def test_lookup_resolves_names_and_aliases():
    cmds = make_commands(StubScreen())
    assert lookup("/help", cmds).name == "help"
    assert lookup("model", cmds).name == "model"
    assert lookup("/q", cmds).name == "exit"
    assert lookup("/quit", cmds).name == "exit"


def test_lookup_missing_returns_none():
    cmds = make_commands(StubScreen())
    assert lookup("/nope", cmds) is None


def test_matches_prefix_filters_by_name():
    cmds = make_commands(StubScreen())
    assert set(c.name for c in matches_prefix("se", cmds)) == {"search", "sessions"}
    assert [c.name for c in matches_prefix("/m", cmds)] == ["model"]
    assert matches_prefix("zz", cmds) == []


def test_help_text_lists_commands():
    cmds = make_commands(StubScreen())
    text = help_text(cmds)
    assert "/model" in text
    assert "Switch LLM provider" in text


def test_u3_commands_registered():
    cmds = make_commands(StubScreen())
    names = {c.name for c in cmds}
    assert {"notebooks", "notebook", "create", "ingest", "auth", "connect"} <= names
    assert lookup("/notebook", cmds).arg_hint == "<name>"
    assert lookup("/ingest", cmds).arg_hint == "<path|url>"
    assert lookup("/connect", cmds).arg_hint == "<provider>"


def test_u4_commands_registered():
    cmds = make_commands(StubScreen())
    names = {c.name for c in cmds}
    assert {"undo", "details"} <= names
    assert lookup("/undo", cmds).name == "undo"
    assert lookup("/details", cmds).name == "details"


def test_command_handlers_bound():
    screen = StubScreen()
    cmds = make_commands(screen)
    lookup("sources", cmds).handler("")
    assert screen.calls or screen.transcript  # ran without exception


# -- CommandPopup -----------------------------------------------------------


def test_popup_selected_and_navigation():
    cmds = make_commands(StubScreen())
    popup = CommandPopup(cmds)
    assert popup.selected().name == "help"
    popup.move(1)
    assert popup.selected().name == "exit"
    popup.move(-1)
    assert popup.selected().name == "help"


def test_popup_renders_selected_line_reversed():
    cmds = make_commands(StubScreen())
    popup = CommandPopup(cmds)
    text = popup.render()
    assert "Show command help" in text.plain
    # The first line (selected) is styled bold+reverse.
    first_span = text.spans[0]
    assert "reverse" in str(first_span.style) and "bold" in str(first_span.style)


def test_popup_completes_to_input():
    cmds = make_commands(StubScreen())
    popup = CommandPopup(cmds)

    class FakeInput:
        text = ""

        class _Doc:
            end = (0, 6)

        document = _Doc()

        def move_cursor(self, n):
            pass

    inp = FakeInput()
    popup.complete_to_input(inp)
    assert inp.text == "/help "
    assert popup.display is False


def test_popup_no_commands_renders_hint():
    popup = CommandPopup([])
    assert "(no matching commands)" in popup.render().plain


# -- ItemListDialog ---------------------------------------------------------


async def test_item_list_dialog_dismisses_with_picked_value():
    from opennote.tui.app import OpenNoteApp
    from opennote.tui.theme import DARK

    app = OpenNoteApp(palette=DARK)
    picked = []
    async with app.run_test() as pilot:
        item_list(app, "Pick", [("a", "Apple"), ("b", "Banana")], on_pick=picked.append)
        await pilot.pause()
        from opennote.tui.dialogs import ItemListDialog

        assert isinstance(app.screen, ItemListDialog)
        await pilot.press("enter")  # picks the first item
        await pilot.pause()
        assert picked == ["a"]


async def test_item_list_dialog_escape_returns_none():
    from opennote.tui.app import OpenNoteApp
    from opennote.tui.theme import DARK

    app = OpenNoteApp(palette=DARK)
    picked = []
    async with app.run_test() as pilot:
        item_list(app, "Pick", [("a", "Apple")], on_pick=picked.append)
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()
        assert picked == [None]