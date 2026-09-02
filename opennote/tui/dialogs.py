"""Small modal dialogs: informational and item-list (pick one)."""
from __future__ import annotations

from typing import Callable, List, Optional, Tuple

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.message import Message
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, ListItem, ListView, Static, OptionList
from textual.widgets.option_list import Option

from opennote.tui.palette import PaletteEntry, make_palette


class InfoDialog(ModalScreen):
    """A dismissible text modal (used by /details, /auth, etc.; /help uses HelpDialog)."""

    BINDINGS = [Binding("escape", "dismiss_modal", "Close", show=False)]

    def __init__(self, title: str, body: str, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._title = title
        self._body = body

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog", classes="dialog"):
            yield Label(self._title, id="dialog-title")
            yield Static(self._body, id="dialog-body")
            yield Label("esc close", id="dialog-hint", classes="muted")

    def action_dismiss_modal(self) -> None:
        self.dismiss()


class ItemListDialog(ModalScreen):
    """A pick-one modal backed by a ListView.

    ``items`` is a list of ``(value, label)`` pairs; dismissing via Enter
    returns the picked ``value`` through ``push_screen(callback=...)``.
    """

    BINDINGS = [Binding("escape", "dismiss_modal", "Close", show=False)]

    class Picked(Message):
        def __init__(self, value: str) -> None:
            super().__init__()
            self.value = value

    def __init__(self, title: str, items: List[Tuple[str, str]], *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._title = title
        self._items = items

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog", classes="dialog"):
            yield Label(self._title, id="dialog-title")
            with VerticalScroll(id="dialog-list"):
                yield ListView(
                    *[
                        ListItem(Label(label), id=f"item-{i}")
                        for i, (_, label) in enumerate(self._items)
                    ],
                    id="item-list",
                )
            yield Label("↑↓ pick · enter select · esc close", id="dialog-hint", classes="muted")

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        index = self.query_one("#item-list", ListView).index
        if index is not None and index < len(self._items):
            value, _ = self._items[index]
            self.dismiss(value)

    def on_mount(self) -> None:
        self.query_one("#item-list", ListView).focus()

    def action_dismiss_modal(self) -> None:
        self.dismiss()


def item_list(
    app,
    title: str,
    items: List[Tuple[str, str]],
    on_pick: Optional[Callable[[Optional[str]], None]] = None,
) -> None:
    """Open an ItemListDialog, routing the pick (or None on esc) to *on_pick*."""
    dialog = ItemListDialog(title, items)
    app.push_screen(dialog, callback=on_pick)


class HelpDialog(ModalScreen):
    """A dismissible info dialog with an Okay button."""

    BINDINGS = [Binding("escape", "dismiss_modal", "Close", show=False),
                Binding("enter", "dismiss_modal", "Okay", show=False)]

    def __init__(self, title: str, body: str, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._title = title
        self._body = body

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog", classes="dialog"):
            yield Label(self._title, id="dialog-title")
            yield Static(self._body, id="dialog-body")
            yield Button("Okay", id="help-okay", variant="primary")

    def on_mount(self) -> None:
        self.query_one("#help-okay", Button).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss()


class InputDialog(ModalScreen):
    """A single-line text-input modal (used by /connect for API keys)."""

    BINDINGS = [Binding("escape", "dismiss_modal", "Close", show=False)]

    def __init__(
        self,
        title: str,
        label: str,
        placeholder: str = "",
        password: bool = False,
        *args,
        **kwargs,
    ) -> None:
        super().__init__(*args, **kwargs)
        self._title = title
        self._label = label
        self._placeholder = placeholder
        self._password = password

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog", classes="dialog"):
            yield Label(self._title, id="dialog-title")
            yield Label(self._label, id="dialog-body", classes="muted")
            yield Input(
                placeholder=self._placeholder,
                password=self._password,
                id="dialog-input",
            )
            yield Label("enter confirm · esc close", id="dialog-hint", classes="muted")

    def on_mount(self) -> None:
        self.query_one("#dialog-input", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        value = event.value.strip()
        self.dismiss(value or None)

    def action_dismiss_modal(self) -> None:
        self.dismiss(None)


def ask_input(
    app,
    title: str,
    label: str,
    placeholder: str = "",
    password: bool = False,
    on_submit: Optional[Callable[[Optional[str]], None]] = None,
) -> None:
    """Open an InputDialog, routing the value (or None on esc) to *on_submit*."""
    dialog = InputDialog(title, label, placeholder=placeholder, password=password)
    app.push_screen(dialog, callback=on_submit)


# --------------------------------------------------------------------------- #
# Command palette
# --------------------------------------------------------------------------- #

class CommandPalette(ModalScreen):
    """A command-palette modal (opened by Ctrl+P) with a search input and
    category‑based segregation.

    On start (empty search) all entries are shown grouped by section.
    Typing filters the visible entries (matching title, description, section
    or keywords).  Enter runs the leaf action (or opens a submenu); Esc dismisses.
    """

    BINDINGS = [
        Binding("escape", "dismiss_modal", "Close", show=False),
        Binding("up", "cursor_up", "Up", show=False, priority=True),
        Binding("down", "cursor_down", "Down", show=False, priority=True),
        Binding("enter", "run_highlighted", "Run", show=False, priority=True),
    ]

    def __init__(
        self,
        screen,
        on_done: Optional[Callable[[Optional[str]], None]] = None,
        *args,
        **kwargs,
    ) -> None:
        super().__init__(*args, **kwargs)
        self._screen = screen
        self._on_done = on_done
        self._entries: List[PaletteEntry] = make_palette(screen)
        # Parallel list mirroring the OptionList options order:
        # None = section header, entry = PaletteEntry for leaf/submenu
        self._matched_entries: List[Optional[PaletteEntry]] = []

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog", classes="dialog"):
            yield Input(id="palette-input", placeholder="Type to search…")
            yield OptionList(id="palette-options")
            yield Label("↑↓ navigate · enter run · esc close", id="dialog-hint", classes="muted")

    def on_mount(self) -> None:
        """Called just after the screen is pushed – set focus and install filter."""
        input_widget = self.query_one("#palette-input", Input)
        input_widget.focus()
        self._refresh("")

    def on_input_changed(self, event: Input.Changed) -> None:
        """Live‑filter the palette as the user types."""
        self._refresh(event.value)

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        """Run the highlighted entry when clicked (or Enter pressed while focused)."""
        self._run_index(event.option_index)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """Fallback Enter handler (when the binding doesn't consume the event)."""
        self._run_highlighted()

    def action_cursor_up(self) -> None:
        self._move_highlight(-1)

    def action_cursor_down(self) -> None:
        self._move_highlight(1)

    def action_run_highlighted(self) -> None:
        self._run_highlighted()

    def _move_highlight(self, delta: int) -> None:
        """Move the OptionList highlight, skipping disabled section headers."""
        ol = self.query_one("#palette-options", OptionList)
        if not ol.options:
            return
        if delta < 0:
            ol.action_cursor_up()
        else:
            ol.action_cursor_down()

    def _refresh(self, filter_text: str) -> None:
        """Rebuild the OptionList showing *only* entries matching *filter_text*,
        grouped under disabled section headers."""
        ol = self.query_one("#palette-options", OptionList)
        ol.clear_options()
        self._matched_entries = []

        # Determine which entries match the filter.
        matched: List[PaletteEntry] = []
        for entry in self._entries:
            title_match = not filter_text or filter_text.lower() in entry.title.lower()
            desc_match = not filter_text or filter_text.lower() in entry.description.lower()
            section_match = not filter_text or filter_text.lower() in entry.section.lower()
            kw_match = not filter_text or any(
                filter_text.lower() in kw.lower() for kw in entry.keywords.split()
            )
            if not filter_text or (title_match or desc_match or section_match or kw_match):
                matched.append(entry)

        if not matched:
            ol.add_options(["No matching entries"])
            self._matched_entries.append(None)  # placeholder so index 0 exists
            return

        # Group by section, preserving the first-seen palette order.
        sections: dict = {}
        order: List[str] = []
        for entry in matched:
            if entry.section not in sections:
                sections[entry.section] = []
                order.append(entry.section)
            sections[entry.section].append(entry)

        for section in order:
            ol.add_option(Option(f"—— {section} ——", disabled=True))
            self._matched_entries.append(None)
            for entry in sections[section]:
                display = f"**{entry.title}**  {entry.description}"
                ol.add_option(display)
                self._matched_entries.append(entry)  # track the entry

    def _run_highlighted(self) -> None:
        """Run the highlighted entry's action or open its submenu."""
        ol = self.query_one("#palette-options", OptionList)
        if not ol.options:
            return
        self._run_index(ol.highlighted if ol.highlighted is not None else 0)

    def _run_index(self, idx: int) -> None:
        """Run the entry at *idx*, skipping disabled section headers.

        The palette is dismissed first and the action deferred via
        ``call_later`` so any screen it pushes mounts cleanly on top of the
        chat screen (not mid-dismissal of this modal)."""
        n = len(self._matched_entries)
        if n == 0:
            return
        entry = None
        for offset in range(n):
            candidate = (idx + offset) % n
            if self._matched_entries[candidate] is not None:
                entry = self._matched_entries[candidate]
                break
        if entry is None:
            # Only headers / placeholders present – just dismiss.
            self.dismiss(None)
            return
        action = entry.submenu or entry.action
        if action is None:
            self.dismiss(None)
            return
        self.dismiss(None)
        self.app.call_later(action)

    def action_dismiss_modal(self) -> None:
        self.dismiss(None)