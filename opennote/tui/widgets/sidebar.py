"""Right-side status panel (opencode-style): session, context, services.

ASCII only - no box-drawing or unicode symbols (terminal-safe).
Fixed 32-column width; hidden on narrow screens via CSS media query.
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import List, Optional

from textual.reactive import reactive
from textual.widget import Widget
from textual.widgets import Label


def git_branch(cwd: Optional[Path] = None) -> str:
    """Best-effort git branch for cwd; empty string on any failure."""
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True,
            text=True,
            timeout=2,
            cwd=str(cwd or Path.cwd()),
        )
        branch = (out.stdout or "").strip()
        if branch and branch != "HEAD":
            return branch
    except Exception:
        pass
    return ""


class SideBar(Widget):
    """Persistent right panel: session title, Context block, services, footer."""

    session_title: reactive = reactive("")  # type: ignore[assignment]
    session_sub: reactive = reactive("")  # type: ignore[assignment]
    context_text: reactive = reactive("")  # type: ignore[assignment]
    services_text: reactive = reactive("")  # type: ignore[assignment]
    footer_left: reactive = reactive("")  # type: ignore[assignment]
    footer_right: reactive = reactive("")  # type: ignore[assignment]

    def compose(self):
        from textual.app import ComposeResult
        from textual.containers import Vertical

        with Vertical(id="sidebar-body"):
            yield Label("", id="side-session-title")
            yield Label("", id="side-session-sub", classes="muted")
            yield Label("", id="side-spacer1")
            yield Label("Context", id="side-context-head")
            yield Label("", id="side-context")
            yield Label("", id="side-spacer2")
            yield Label("Services", id="side-services-head")
            yield Label("", id="side-services")
        with Vertical(id="sidebar-footer"):
            yield Label("", id="side-footer-left", classes="muted")
            yield Label("", id="side-footer-right", classes="muted")

    # -- reactive watchers -------------------------------------------------

    def watch_session_title(self, text: str) -> None:
        self.query_one("#side-session-title", Label).update(text)

    def watch_session_sub(self, text: str) -> None:
        self.query_one("#side-session-sub", Label).update(text)

    def watch_context_text(self, text: str) -> None:
        self.query_one("#side-context", Label).update(text)

    def watch_services_text(self, text: str) -> None:
        self.query_one("#side-services", Label).update(text)

    def watch_footer_left(self, text: str) -> None:
        self.query_one("#side-footer-left", Label).update(text)

    def watch_footer_right(self, text: str) -> None:
        self.query_one("#side-footer-right", Label).update(text)

    # -- public API --------------------------------------------------------

    def set_session(self, title: str, sub: str = "") -> None:
        self.session_title = title
        self.session_sub = sub

    def set_usage(self, usage) -> None:
        try:
            self.context_text = usage.render() if usage is not None else "Context\nno turns yet"
        except Exception:
            self.context_text = ""

    def set_services(self, lines: List[str]) -> None:
        try:
            self.services_text = "\n".join(lines) if lines else "none"
        except Exception:
            self.services_text = ""

    def set_footer(self, left: str, right: str) -> None:
        self.footer_left = left
        self.footer_right = right
