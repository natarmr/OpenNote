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
        # Bound scrollback: long sessions otherwise grow memory and slow
        # every resize re-layout. 2000 lines ≈ hundreds of turns.
        try:
            from opennote.config import get_config

            kwargs.setdefault("max_lines", get_config().tool_output.max_lines)
        except Exception:
            kwargs.setdefault("max_lines", 2000)
        kwargs.setdefault("wrap", True)
        super().__init__(*args, **kwargs)
        self.markup = False
        self.auto_scroll = True
        self.palette = None  # set by ChatScreen right after mount
        # Logical write count. ``RichLog.lines`` cannot be used for this: a
        # write made before the widget's size is known is only rendered later,
        # so the strip count lags. ``/theme`` needs to know whether the banner
        # is still the whole transcript.
        self._writes = 0

    @property
    def write_count(self) -> int:
        """How many add_* calls have been made since the last clear()."""
        return self._writes

    def clear(self) -> None:
        super().clear()
        self._writes = 0

    def _color(self, field: str, default: str) -> str:
        return getattr(self.palette, field, default) if self.palette else default

    def _reveal(self) -> None:
        """Ask the owning ChatScreen to leave the welcome view, if it hasn't
        already. Safe to call unconditionally - a screen without this hook,
        one that's already revealed, or an unmounted widget just no-ops."""
        try:
            reveal = getattr(self.screen, "_reveal_transcript", None)
        except Exception:
            return
        if callable(reveal):
            reveal()

    def add_banner(self, palette) -> None:
        self._writes += 1
        self.write(render_logo(palette))
        self.write("")

    def add_user(self, text: str) -> None:
        self._writes += 1
        self._reveal()
        when = datetime.now().strftime("%H:%M")
        header = Text(f" ◆  You  ·  {when}", style=f"bold {self._color('primary', '#fab283')}")
        body = Text(text, style=self._color("text", "#eeeeee"))
        self.write(header)
        self.write(body)
        self.write("")

    def add_answer(self, answer: str) -> None:
        self._writes += 1
        self._reveal()
        when = datetime.now().strftime("%H:%M")
        header = Text(f" ●  OpenNote  ·  {when}", style=f"bold {self._color('secondary', '#5c9cf5')}")
        self.write(header)
        self.write(Markdown(answer, code_theme="monokai"))
        self.write("")

    def add_error(self, message: str) -> None:
        self._writes += 1
        self._reveal()
        self.write(
            Text(f" ✗  {message} ", style=f"bold white on {self._color('error', 'red')}")
        )
        self.write("")

    def add_info(self, message: str) -> None:
        self._writes += 1
        self._reveal()
        self.write(Text(f" ›  {message}", style=self._color("text_muted", "#808080")))
        self.write("")

    def add_mindmap(self, title: str, root) -> None:
        """Render a parsed mind-map tree inline (ASCII guides)."""
        from rich.tree import Tree

        self._writes += 1
        self._reveal()
        tree = Tree(title, guide_style=self._color("text_muted", "#808080"))

        def _add(branch, node) -> None:
            for child in node.children:
                leaf = branch.add(child.label)
                _add(leaf, child)

        _add(tree, root)
        self.write(tree)
        self.write("")
