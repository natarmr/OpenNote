"""Autocomplete popup for slash commands.

Shown while the prompt starts with ``/``; lists matching commands and lets the
user highlight one with up/down. Tab completes (inserts the command text),
Enter selects the highlighted command, Esc hides.
"""
from __future__ import annotations

from typing import List, Optional

from rich.text import Text
from textual.reactive import reactive
from textual.widget import Widget

from opennote.tui.commands import Command


class CommandPopup(Widget):
    """A slim bordered list of matching commands, rendered as text."""

    index: reactive = reactive(0)  # type: ignore[assignment]

    def __init__(self, commands: Optional[List[Command]] = None, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.commands: List[Command] = list(commands or [])
        self.display = False

    def set_commands(self, commands: List[Command]) -> None:
        self.commands = list(commands)
        self.index = 0
        self.refresh()

    def move(self, delta: int) -> None:
        if not self.commands:
            return
        self.index = (self.index + delta) % len(self.commands)
        self.refresh()

    def selected(self) -> Optional[Command]:
        if not self.commands:
            return None
        return self.commands[self.index]

    def complete_to_input(self, input_widget) -> None:
        cmd = self.selected()
        if cmd is None:
            self.display = False
            return
        input_widget.text = f"/{cmd.name} "
        input_widget.move_cursor(input_widget.document.end)
        self.display = False

    def render(self) -> Text:
        out = Text()
        if not self.commands:
            out.append("  (no matching commands)")
            return out
        for i, cmd in enumerate(self.commands):
            line = Text(f"  /{cmd.name}")
            if i == self.index:
                line.stylize("bold reverse")
            line.append(f"  {cmd.description}", style="dim")
            out.append(line)
            if i < len(self.commands) - 1:
                out.append("\n")
        return out
