"""Small modal dialogs: informational and item-list (pick one)."""
from __future__ import annotations

from typing import Callable, List, Optional, Tuple

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.message import Message
from textual.screen import ModalScreen
from textual.widgets import Input, Label, ListItem, ListView, Static


class InfoDialog(ModalScreen):
    """A dismissible text modal (used by /help)."""

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


class InputDialog(ModalScreen):
    """A single-line text-input modal (used by /connect for API keys).

    Enter dismisses with the (stripped) value; esc dismisses with None.
    """

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