"""Transcript: the scrollable conversation log shown above the prompt bar.

Renders the banner, user messages, assistant answers (markdown), and
status/error lines into a RichLog so the container handles scrolling.
"""
from __future__ import annotations

from datetime import datetime

from rich.markdown import Markdown
from rich.text import Text
from textual.widgets import RichLog

from opennote.tui.banner import render_logo


class Transcript(RichLog):
    """Appends conversation items in opencode's muted/bold text styling."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.markup = False
        self.auto_scroll = True

    def add_banner(self, palette) -> None:
        self.write(render_logo(palette))
        self.write("")

    def add_user(self, text: str, name: str = "You") -> None:
        when = datetime.now().strftime("%H:%M")
        header = Text(f" {name} · {when} ", style="bold")
        header.stylize("bold #d6d6d6")
        body = Text(text)
        self.write(header)
        self.write(body)
        self.write("")

    def add_answer(self, answer: str) -> None:
        self.write(Markdown(answer))
        self.write("")

    def add_error(self, message: str) -> None:
        self.write(Text(f" Error: {message} ", style="bold red"))
        self.write("")

    def add_info(self, message: str) -> None:
        self.write(Text(message, style="dim"))
        self.write("")

    def clear(self) -> None:
        super().clear()