"""The opencode-style prompt bar: textarea + meta row + status row.

Layout (bottom of the screen):
    ┌─ (left border, colored by the current mode)
    │ Ask anything... "<example>"
    │ Ask · gpt-oss-120b groq
    ⣿ Searching sources… round 2/5        esc interrupt

The left border and the mode label share the mode color (ask = primary,
search = secondary). The status row sits below the box and holds a spinner
while a turn is running.
"""
from __future__ import annotations

from typing import Optional

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.events import Key
from textual.message import Message
from textual.reactive import reactive
from textual.widget import Widget
from textual.widgets import Label, LoadingIndicator, Static, TextArea

from opennote.tui.commands import Command, matches_prefix
from opennote.tui.widgets.command_popup import CommandPopup

#: Modes the Tab key cycles through, in order.
MODES = ("ask", "search")

#: Random-ish placeholder examples for a fresh session.
PLACEHOLDER_EXAMPLES = [
    "What are the key findings?",
    "Summarize the meeting notes.",
    "Compare the two approaches.",
    "What does the data say about Q3?",
]

MODE_LABELS = {"ask": "Ask", "search": "Search"}


class PromptInput(TextArea):
    """A textarea that submits on Enter and lets Shift+Enter add a newline.

    While the slash-command popup is open, up/down navigate it, Tab completes
    the highlighted command, and Escape hides it.
    """

    class Submitted(Message):
        def __init__(self, text: str) -> None:
            super().__init__()
            self.text = text

    def _popup(self) -> CommandPopup:
        return self.screen.query_one("#command-popup", CommandPopup)

    async def _on_key(self, event: Key) -> None:
        popup = self._popup()
        if popup.display and popup.commands and event.key in ("up", "down"):
            popup.move(-1 if event.key == "up" else 1)
            event.stop()
            return
        if event.key == "enter":
            event.stop()
            self.post_message(self.Submitted(self.text))
            return
        await super()._on_key(event)


class PromptBar(Widget):
    """Input bar with a mode/model/provider meta row and a status row."""

    mode = reactive("ask")
    model: reactive = reactive("")  # type: ignore[assignment]
    provider: reactive = reactive("")  # type: ignore[assignment]
    busy: reactive = reactive(False)  # type: ignore[assignment]
    status_text: reactive = reactive("")  # type: ignore[assignment]
    hint: reactive = reactive("")  # type: ignore[assignment]

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._interrupt_armed = False
        self.commands: list = []

    def compose(self) -> ComposeResult:
        yield CommandPopup(id="command-popup")
        yield Static("", id="prompt-box-spacer")
        with Horizontal(id="prompt-box"):
            yield PromptInput(
                placeholder="Ask anything...",
                id="prompt-input",
                soft_wrap=True,
            )
        with Horizontal(id="meta-row"):
            yield Label("Ask", id="meta-mode")
            yield Label("·", id="meta-sep", classes="muted")
            yield Label("", id="meta-model")
            yield Label("", id="meta-provider", classes="muted")
        with Horizontal(id="status-row"):
            yield LoadingIndicator(id="status-spinner")
            yield Label("", id="status-text")
            yield Label("", id="status-hint", classes="hint")

    def on_mount(self) -> None:
        self.set_mode(self.mode)

    # -- reactive watchers ------------------------------------------------

    def on_text_area_changed(self, event) -> None:
        """Live-filter the slash-command popup as the user types."""
        popup = self.query_one("#command-popup", CommandPopup)
        text = event.text_area.text
        if text.startswith("/") and " " not in text[1:]:
            popup.set_commands(matches_prefix(text[1:], self.commands))
            popup.display = bool(popup.commands)
        else:
            popup.display = False

    def watch_mode(self, mode: str) -> None:
        label = self.query_one("#meta-mode", Label)
        label.update(MODE_LABELS.get(mode, mode))
        vars_ = self.app.get_css_variables()  # type: ignore[attr-defined]
        mode_color = vars_.get("secondary" if mode == "search" else "primary", "")
        label.styles.color = mode_color
        self.query_one("#prompt-box").styles.border_left = ("round", mode_color)

    def watch_model(self, model: str) -> None:
        self.query_one("#meta-model", Label).update(model or "")

    def watch_provider(self, provider: str) -> None:
        self.query_one("#meta-provider", Label).update(provider or "")

    def watch_busy(self, busy: bool) -> None:
        spinner = self.query_one("#status-spinner", LoadingIndicator)
        spinner.display = busy
        self._interrupt_armed = False

    def watch_status_text(self, text: str) -> None:
        self.query_one("#status-text", Label).update(text)

    def watch_hint(self, text: str) -> None:
        self.query_one("#status-hint", Label).update(text)

    # -- public API -------------------------------------------------------

    def set_commands(self, commands: list) -> None:
        """Register the slash-command registry driving the autocomplete popup."""
        self.commands = list(commands)

    def set_mode(self, mode: str) -> None:
        self.mode = mode if mode in MODES else "ask"

    def set_model(self, model: str, provider: str) -> None:
        self.model = model
        self.provider = provider

    def set_busy(self, text: str, hint: str = "esc interrupt") -> None:
        self.status_text = text
        self.hint = hint
        self.busy = True

    def set_idle(self, hint: str = "tab modes · ctrl+p") -> None:
        self.status_text = ""
        self.hint = hint
        self.busy = False

    def update_status(self, text: str) -> None:
        """Update only the status text (e.g. round progress)."""
        self.status_text = text

    def clear_input(self) -> None:
        self.query_one("#prompt-input", PromptInput).text = ""

    def focus_input(self) -> None:
        self.query_one("#prompt-input", PromptInput).focus()

    def arm_interrupt(self) -> None:
        """First esc press: hint that a second press interrupts."""
        if self.busy and not self._interrupt_armed:
            self._interrupt_armed = True
            self.hint = "esc again to interrupt"

    def popup_visible(self) -> bool:
        popup = self.query_one("#command-popup", CommandPopup)
        return popup.display and bool(popup.commands)

    def complete_command(self) -> None:
        """Tab-complete the highlighted command into the input."""
        popup = self.query_one("#command-popup", CommandPopup)
        if popup.display and popup.commands:
            popup.complete_to_input(self.query_one("#prompt-input", PromptInput))

    def hide_popup(self) -> None:
        popup = self.query_one("#command-popup", CommandPopup)
        popup.display = False